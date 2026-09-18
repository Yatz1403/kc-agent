"""Config questions, the Git parsers, hash projections, OKF and the agents.

The Git parsers are pure functions over file content, which is why they can be
the MVP's authoritative source while every other connector is still a seam:
these tests need no credentials and no network.
"""

from __future__ import annotations

import json

import pytest

from kc.agents import llm as llm_seam
from kc.agents.base import ESCALATE, NO_ACTION
from kc.agents.curator import ROUTING, Curator
from kc.agents.resolver import Resolver
from kc.config import loader as cfg
from kc.connectors import registry
from kc.connectors.base import SourceObject
from kc.connectors.confluence import ConfluenceConnector, chunk_html
from kc.connectors.git_openapi import GitConnector, endpoint_index
from kc.connectors.jira import JiraConnector
from kc.core.errors import InvalidRequest, UnsupportedVersion
from kc.serve import okf

OPENAPI = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "ledger-api", "version": "1.4.0"},
        "servers": [{"url": "/v1"}],
        "paths": {
            "/accounts/{id}/entries": {
                "get": {"summary": "List ledger entries for an account"},
            },
            "/entries": {"post": {"summary": "Post a ledger entry"}},
        },
    }
)

PYTEST_FILE = '''
import pytest


def test_partial_refund_posts(client):
    """LED-101 partial refunds appear on the ledger."""
    response = client.get("/accounts/12345/entries")
    assert response.status_code == 200


def test_rejects_over_limit(client):
    response = client.post("/entries")
    assert response.status_code == 422
'''


def _obj(source_system: str, source_id: str, container: str, raw: str, **content) -> SourceObject:
    return SourceObject(
        source_system=source_system, source_instance="test", source_id=source_id,
        container=container, content=content, raw=raw,
    )


# --- config as executable config --------------------------------------------


class TestConfigQuestions:
    def test_edge_domain_and_range(self):
        assert cfg.edge_permitted("PUBLISHES_TO", "Service", "Topic")
        assert not cfg.edge_permitted("PUBLISHES_TO", "Service", "Requirement")
        assert cfg.edge_permitted("PART_OF", "Endpoint", "API")
        assert not cfg.edge_permitted("PART_OF", "API", "Endpoint")
        assert not cfg.edge_permitted("INVENTED", "Service", "API")

    def test_cardinality_one_edges(self):
        for edge in ("OWNED_BY", "PART_OF", "EXECUTES", "MEMBER_OF"):
            assert cfg.edge_cardinality(edge) == "one"

    def test_property_cardinality(self):
        assert cfg.is_single_valued("Endpoint", "path")
        assert not cfg.is_single_valued("TestCase", "file_path")
        with pytest.raises(KeyError):
            cfg.is_single_valued("Service", "vibe")

    def test_visibility_defaults_cover_every_node_type(self):
        for node_type in cfg.node_types():
            assert cfg.default_visibility(node_type) in ("public", "topology", "restricted")
            assert cfg.ttl_days(node_type) > 0

    def test_document_subtypes_override_the_type_default(self):
        assert cfg.default_visibility("Document") == "restricted"
        assert cfg.default_visibility("Document", "standard") == "public"

    def test_authority_overrides_beat_the_default_order(self):
        assert cfg.authority_rank("jira", "Requirement", "status") > cfg.authority_rank(
            "git", "Requirement", "status"
        )
        assert cfg.authority_rank("git", "Service", "name") > cfg.authority_rank(
            "jira", "Service", "name"
        )

    def test_human_confirmed_outranks_everything(self):
        assert cfg.authority_rank("kc-review", "Service", "name", "human-confirmed") > 60

    def test_agent_derived_is_penalised(self):
        assert cfg.authority_rank("git", "Service", "name", "agent-derived") < cfg.authority_rank(
            "git", "Service", "name"
        )

    def test_write_identities(self):
        assert cfg.may("kc-ingest", "add_claim")
        assert not cfg.may("kc-ingest", "merge_nodes")
        assert not cfg.may("kc-resolver", "add_claim")
        assert cfg.is_tier_gated("kc-curator")
        assert not cfg.is_tier_gated("kc-ingest")

    def test_scope_intersection(self):
        assert cfg.bus_for_identity("alice@bu-a", "kc-cli") == ["bu-a", "commons"]
        # design-assistant has no BU-B scope, so a BU-B user gets commons only.
        assert cfg.bus_for_identity("bob@bu-b", "design-assistant") == ["commons"]
        assert cfg.bus_for_identity("carol@platform", "kc-cli") == ["bu-a", "bu-b", "commons"]
        assert cfg.bus_for_identity("nobody@nowhere", "kc-cli") == []

    def test_bu_mapping_is_explicit(self):
        assert cfg.bu_for_source("jira", "LED") == "bu-a"
        assert cfg.bu_for_source("git", "acme/recon-engine") == "bu-b"
        assert cfg.bu_for_source("jira", "UNMAPPED") is None

    def test_intent_budgets(self):
        assert cfg.intent_budget("lookup")["hops"] == 1
        assert cfg.intent_budget("impact_analysis")["hops"] == 3
        assert cfg.edge_criticality("DEPENDS_ON") > cfg.edge_criticality("DOCUMENTS")


# --- Git parsers -------------------------------------------------------------


class TestGitOpenApi:
    def test_api_and_endpoints_extracted(self):
        connector = GitConnector()
        obj = _obj("git", "acme/ledger-api:openapi.json", "acme/ledger-api", OPENAPI,
                   path="openapi.json", committed_at="2026-01-05T00:00:00+00:00")
        result = connector.extract(obj, "bu-a")
        types = [c.node_type for c in result.claims if c.kind == "identity"]
        assert types.count("API") == 1
        assert types.count("Endpoint") == 2
        edges = {c.edge_type for c in result.claims if c.kind == "relationship"}
        assert edges == {"PART_OF"}
        assert result.llm_used is False

    def test_endpoint_properties_are_on_schema(self):
        connector = GitConnector()
        obj = _obj("git", "spec", "acme/ledger-api", OPENAPI, path="openapi.json")
        for claim in connector.extract(obj, "bu-a").claims:
            if claim.kind == "property":
                assert cfg.property_rule(str(claim.node_type), str(claim.prop)) is not None

    def test_chunks_carry_endpoint_text(self):
        connector = GitConnector()
        obj = _obj("git", "spec", "acme/ledger-api", OPENAPI, path="openapi.json")
        chunks = connector.extract(obj, "bu-a").chunks
        assert any("List ledger entries" in c["content"] for c in chunks)

    def test_package_json_gives_components_and_depends_on(self):
        connector = GitConnector()
        raw = json.dumps({"name": "ledger-workers", "dependencies": {"express": "^4.19.2"}})
        obj = _obj("git", "pkg", "acme/ledger-workers", raw, path="package.json")
        result = connector.extract(obj, "bu-a")
        assert any(c.node_type == "Component" and c.kind == "identity" for c in result.claims)
        assert any(c.edge_type == "DEPENDS_ON" for c in result.claims)

    def test_requirements_txt_parses_specs(self):
        connector = GitConnector()
        raw = "fastapi>=0.115\n# comment\npytest==8.3.2\n"
        obj = _obj("git", "req", "acme/ledger-api", raw, path="requirements.txt")
        result = connector.extract(obj, "bu-a")
        names = {c.value for c in result.claims if c.prop == "name"}
        assert {"fastapi", "pytest"} <= names

    def test_pytest_covers_only_matched_endpoints(self):
        connector = GitConnector()
        spec_obj = _obj("git", "spec", "acme/ledger-api", OPENAPI, path="openapi.json")
        index = endpoint_index(connector.extract(spec_obj, "bu-a").claims)
        assert "GET /accounts/{id}/entries" in index

        test_obj = _obj(
            "git", "tests/test_entries.py", "acme/ledger-api", PYTEST_FILE,
            path="tests/test_entries.py", endpoint_index=index,
        )
        result = connector.extract(test_obj, "bu-a")
        tests = [c for c in result.claims if c.kind == "identity" and c.node_type == "TestCase"]
        assert len(tests) == 2
        covers = [c for c in result.claims if c.edge_type == "COVERS"]
        assert len(covers) == 2  # GET matched, POST /entries matched

    def test_unmatched_target_gets_no_covers_edge(self):
        connector = GitConnector()
        test_obj = _obj(
            "git", "tests/test_other.py", "acme/ledger-api",
            'def test_x(client):\n    client.get("/not/in/spec")\n',
            path="tests/test_other.py", endpoint_index={},
        )
        result = connector.extract(test_obj, "bu-a")
        assert not [c for c in result.claims if c.edge_type == "COVERS"]

    def test_jira_key_without_a_node_becomes_a_mention(self):
        connector = GitConnector()
        test_obj = _obj(
            "git", "tests/test_entries.py", "acme/ledger-api", PYTEST_FILE,
            path="tests/test_entries.py", endpoint_index={},
        )
        result = connector.extract(test_obj, "bu-a")
        assert any(m["name"] == "LED-101" for m in result.mentions)
        assert not [c for c in result.claims if c.edge_type == "VALIDATES"]


# --- hash projections --------------------------------------------------------


class TestHashProjection:
    def test_jira_volatile_fields_are_dropped(self):
        connector = JiraConnector()
        base = {"fields": {"summary": "A", "status": {"name": "Open"}}, "updated": "t1",
                "watches": {"watchCount": 3}, "rank": "0|i0001"}
        moved = {"fields": {"summary": "A", "status": {"name": "Open"}}, "updated": "t2",
                 "watches": {"watchCount": 9}, "rank": "0|i0002"}
        first = connector.hash_projection(_obj("jira", "LED-1", "LED", "", **base))
        second = connector.hash_projection(_obj("jira", "LED-1", "LED", "", **moved))
        assert first == second, "volatile churn must not look like a change (N1)"

    def test_a_real_change_changes_the_hash(self):
        connector = JiraConnector()
        before = {"fields": {"summary": "A", "status": {"name": "Open"}}, "updated": "t1"}
        after = {"fields": {"summary": "A", "status": {"name": "Done"}}, "updated": "t1"}
        assert connector.hash_projection(
            _obj("jira", "LED-1", "LED", "", **before)
        ) != connector.hash_projection(_obj("jira", "LED-1", "LED", "", **after))

    def test_confluence_version_churn_is_dropped(self):
        connector = ConfluenceConnector()
        body = {"body": {"storage": {"value": "<p>same</p>"}}}
        first = connector.hash_projection(
            _obj("confluence", "1", "LEDGER", "", version={"number": 4}, **body)
        )
        second = connector.hash_projection(
            _obj("confluence", "1", "LEDGER", "", version={"number": 5}, **body)
        )
        assert first == second


# --- deferred connectors are loud, not silent -------------------------------


class TestConnectorSeams:
    @pytest.mark.parametrize("source_system", ["jira", "confluence", "ado"])
    def test_fetch_raises_with_a_todo(self, source_system):
        connector = registry.get(source_system)
        assert connector is not None
        with pytest.raises(NotImplementedError) as exc:
            connector.fetch("anything")
        assert "TODO(office)" in str(exc.value)

    def test_git_extract_is_wired(self):
        status = {c["source_system"]: c for c in registry.status()}
        assert status["git"]["wired"]["extract"] is True
        assert status["jira"]["wired"]["fetch"] is False

    def test_confluence_may_not_create_anchor_nodes(self):
        connector = registry.get("confluence")
        assert connector is not None
        assert connector.creates_nodes is False
        assert tuple(connector.creatable_types) == ("Document",)

    def test_confluence_links_instead_of_creating(self):
        connector = ConfluenceConnector()
        obj = _obj(
            "confluence", "LEDGER-9", "LEDGER",
            "<h2>Overview</h2><p>The ledger-api serves payment-svc and recon-engine.</p>",
            title="Ledger overview",
        )
        result = connector.extract(obj, "bu-a")
        created = {c.node_type for c in result.claims if c.kind == "identity"}
        assert created == {"Document"}
        assert {m["name"] for m in result.mentions} >= {"ledger-api", "recon-engine"}

    def test_chunk_html_is_structure_aware(self):
        parts = chunk_html("<h2>Decision</h2><p>Append only.</p><h2>Consequence</h2><p>Immutable.</p>", "ADR-7")
        assert [p[0] for p in parts] == ["ADR-7/Decision", "ADR-7/Consequence"]
        assert parts[0][1].startswith("ADR-7/Decision")


# --- OKF ---------------------------------------------------------------------


class TestOkf:
    def test_family_is_complete(self):
        assert len(okf.MESSAGE_TYPES) == 11
        assert okf.OKF_VERSION == "0.2"

    def test_request_validation_clamps_budgets(self):
        request = okf.context_request(
            request_id="r1", intent="lookup", anchors=[{"name": "ledger-api"}],
            max_hops=9, max_tokens=999_999,
        )
        normalised = okf.validate_request(request)
        assert normalised["hops"] == 1  # a caller may lower, never raise
        assert normalised["max_tokens"] == cfg.intent_budget("lookup")["max_tokens"]

    def test_unsupported_version_is_rejected(self):
        request = okf.context_request(
            request_id="r1", intent="lookup", anchors=[{"name": "x"}], okf_version="0.9"
        )
        with pytest.raises(UnsupportedVersion):
            okf.validate_request(request)

    def test_anchorless_request_is_rejected(self):
        with pytest.raises(InvalidRequest):
            okf.validate_request(
                okf.context_request(request_id="r1", intent="lookup", anchors=[])
            )

    def test_gap_codes_are_closed(self):
        okf.gap("NO_TEST_COVERAGE", "n_1")
        with pytest.raises(InvalidRequest):
            okf.gap("SOMETHING_I_MADE_UP", "n_1")

    def test_proposal_requires_evidence(self):
        with pytest.raises(InvalidRequest):
            okf.proposal(
                proposal_id="p1", run_id="r1", agent="resolver", action="merge",
                subjects=[], changes=[], evidence=[],
            )

    def test_error_carries_no_free_text(self):
        payload = okf.error("NOT_FOUND", "r1")
        assert set(payload) == {
            "okf_version", "message_type", "request_id", "code", "retryable", "task_state"
        }


# --- agents ------------------------------------------------------------------


class TestAgents:
    def test_triage_routing_is_deterministic(self):
        assert ROUTING["contradiction"] == "reconciler"
        assert ROUTING["mention_candidate"] == "resolver"
        for _ in range(5):
            assert Curator.route("gap") == "prospector"
        assert Curator.route("something-new") is None

    def test_allocation_favours_the_weakest_metric(self, loaded):
        store, _ = loaded
        allocation = Curator(store).allocate(total_tool_calls=100)
        assert sum(allocation.values()) > 0
        assert set(allocation) == set(ROUTING.values()) | {"retrieval-critic", "schema-steward"}

    def test_resolver_escalates_without_a_client(self, loaded):
        store, _ = loaded
        resolver = Resolver(store)
        assert resolver.client is None
        outcome = resolver.run(
            {
                "run_id": "run_1",
                "task_scope": ["bu-a", "commons"],
                "candidates": [{"name": "leger-api-typo", "proposed_type": "API", "bu": "bu-a"}],
                "subjects": [],
            }
        )
        assert outcome.terminal_state == ESCALATE
        assert outcome.message["reason"] == "NO_LLM_CLIENT"

    def test_resolver_reports_no_action_when_already_resolvable(self, loaded):
        store, _ = loaded
        outcome = Resolver(store).run(
            {
                "run_id": "run_2",
                "task_scope": ["bu-a", "commons"],
                "candidates": [{"name": "ledger-api", "proposed_type": "API", "bu": "bu-a"}],
                "subjects": [],
            }
        )
        assert outcome.terminal_state == NO_ACTION

    def test_stubs_declare_goal_tools_and_write_scope(self, loaded):
        store, _ = loaded
        declarations = {d["agent"]: d for d in Curator(store).declarations()}
        for name in ("reconciler", "verifier", "prospector", "retrieval-critic", "schema-steward"):
            declaration = declarations[name]
            assert declaration["goal"]
            assert declaration["tools"]
            assert declaration["write_scope"].startswith("none")

    def test_stubs_escalate_and_leave_a_backlog_task(self, loaded):
        store, _ = loaded
        store.put_task(
            task_id="t_contra", kind="contradiction", state="open", scope=["bu-a"],
            payload={"subjects": [{"id": "n_x"}]}, reason="test",
        )
        results = Curator(store).work(limit=5)
        assert any(r.get("terminal_state") == ESCALATE for r in results)
        assert [t for t in store.tasks() if t["kind"].endswith("-backlog")]

    def test_kill_switch_stops_dispatch(self, loaded):
        store, _ = loaded
        curator = Curator(store)
        curator.change.set_cognitive_layer(False)
        assert curator.work() == [{"skipped": "cognitive layer off (FR-7.6)"}]

    def test_memory_recall_is_scoped_to_the_task(self, loaded):
        store, _ = loaded
        store.put_memory(memory_id="m_a", kind="episodic", agent="resolver", task_id=None,
                         bus=["bu-a"], body={"note": "bu-a only"})
        store.put_memory(memory_id="m_b", kind="episodic", agent="resolver", task_id=None,
                         bus=["bu-b"], body={"note": "bu-b only"})
        recalled = store.recall_memory(agent="resolver", task_scope=["bu-a", "commons"])
        notes = {m["body"]["note"] for m in recalled}
        assert "bu-a only" in notes and "bu-b only" not in notes

    def test_anonymous_memory_write_is_rejected(self, loaded):
        store, _ = loaded
        with pytest.raises(ValueError):
            store.put_memory(memory_id="m_x", kind="episodic", agent="", task_id=None,
                             bus=["bu-a"], body={})


# --- the LLM seam ------------------------------------------------------------


class TestLlmSeam:
    def test_default_client_is_none(self):
        assert llm_seam.get_client() is None

    def test_span_verification_grades_grounding(self):
        text = "The ledger-api publishes to ledger.entries.v1."
        assert llm_seam.verify_span("ledger-api publishes", text) == 1.0
        assert llm_seam.verify_span("LEDGER-API   PUBLISHES", text) == 0.8
        assert llm_seam.verify_span("recon-engine consumes", text) == 0.0

    def test_prompt_marks_source_as_data(self):
        system, user = llm_seam.extraction_prompt({"type": "object"}, "some page text")
        assert "data, not direction" in system
        assert "<<<SOURCE" in user and "SOURCE>>>" in user

    def test_serve_path_does_not_import_the_llm_seam(self):
        import kc.serve.pipeline as pipeline_module

        source = (pipeline_module.__file__ or "")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "agents.llm" not in text and "llm_seam" not in text

    def test_confluence_extraction_without_a_client_still_chunks(self):
        connector = ConfluenceConnector()
        obj = _obj("confluence", "LEDGER-9", "LEDGER", "<p>ledger-api notes</p>", title="T")
        result = connector.extract(obj, "bu-a", client=None)
        assert result.llm_used is False and result.chunks
