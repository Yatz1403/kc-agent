"""The invariants that are expensive to retrofit and easy to violate silently.

Named acceptance tests from §18, plus the four structural ones: ID collision,
projector halt, rebuild fidelity, and chunks outliving their node.
"""

from __future__ import annotations

import json

import pytest

from kc.agents.gate import Gate
from kc.core.changeapi import ChangeAPI, GateDecision
from kc.core.errors import (
    IdCollision,
    IdentityNotPermitted,
    IdentityUnresolved,
    InbreedingBlocked,
    NotFound,
)
from kc.core.model import Claim, Node, now
from kc.core.projector import GraphProjector
from kc.entitlements import filter as ent
from kc.serve import okf
from kc.serve.pipeline import ServePipeline, query
from kc.stores.sqlite_store import Store


def _request(anchor: str, intent: str = "impact_analysis", **kwargs) -> dict:
    return okf.context_request(
        request_id=f"r_{anchor}_{intent}",
        intent=intent,
        anchors=[{"name": anchor}] if not anchor.startswith("n_") else [{"id": anchor}],
        **kwargs,
    )


# --- AT-4: a spoke may not write --------------------------------------------


class TestAT4SpokeWriteRejected:
    def test_spoke_identity_is_rejected(self, store: Store):
        change = ChangeAPI(store)
        claim = Claim(
            kind="identity", subject_id="n_test", node_type="Service", bu_owner="bu-a",
            source_system="git", source_instance="t", source_id="x", valid_from=now(),
        )
        with pytest.raises(IdentityNotPermitted):
            change.submit("kc-resolver", "add_claim", claims=[claim])

    def test_consumer_app_is_rejected(self, store: Store):
        with pytest.raises(IdentityNotPermitted):
            ChangeAPI(store).submit("consumer-app", "add_claim", claims=[])

    def test_unknown_identity_is_rejected(self, store: Store):
        with pytest.raises(IdentityNotPermitted):
            ChangeAPI(store).submit("someone-elses-service", "add_claim", claims=[])

    def test_nothing_was_appended(self, store: Store):
        with pytest.raises(IdentityNotPermitted):
            ChangeAPI(store).submit("kc-verifier", "add_claim", claims=[])
        assert store.last_seq() == 0


# --- AT-5: unresolved identity ----------------------------------------------


class TestAT5IdentityRejected:
    @pytest.mark.parametrize("user", [None, "", "anonymous", "unknown", "mallory@elsewhere"])
    def test_rejected_before_anything_else(self, loaded, user):
        store, _ = loaded
        with pytest.raises(IdentityUnresolved):
            ServePipeline(store).retrieve(
                _request("ledger-api"), user=user, application="kc-cli"
            )

    def test_unknown_application_is_rejected(self, loaded):
        store, _ = loaded
        with pytest.raises(IdentityUnresolved):
            ServePipeline(store).retrieve(
                _request("ledger-api"), user="alice@bu-a", application="not-registered"
            )

    def test_no_audit_record_for_a_rejected_call(self, loaded):
        store, _ = loaded
        with pytest.raises(IdentityUnresolved):
            ServePipeline(store).retrieve(
                _request("ledger-api"), user=None, application="kc-cli"
            )
        assert store.audit_record("r_ledger-api_impact_analysis") is None


# --- AT-6: BU-A cannot see BU-B restricted ----------------------------------


class TestAT6RestrictedInvisible:
    def test_restricted_node_absent_from_results(self, loaded):
        store, built = loaded
        hidden = built["anchors"]["bu-b"]["restricted_service"]
        response = query(
            store, user="alice@bu-a", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        ids_returned = {e["id"] for e in response["entities"]}
        assert hidden not in ids_returned

    def test_restricted_node_cannot_be_anchored_directly(self, loaded):
        store, built = loaded
        hidden = built["anchors"]["bu-b"]["restricted_service"]
        with pytest.raises(NotFound):
            ServePipeline(store).retrieve(
                _request(hidden), user="alice@bu-a", application="kc-cli"
            )

    def test_shared_name_yields_no_disambiguation(self, loaded):
        """BU-A and BU-B both have a node named "ledger". Alice must get her
        own, with no hint that a second exists (AT-19)."""
        store, built = loaded
        response = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup", anchor="ledger",
        )
        assert response["message_type"] == okf.CONTEXT_RESPONSE
        assert response["anchors"][0]["id"] == built["anchors"]["bu-a"]["service"]

    def test_absence_and_denial_are_the_same_error(self, loaded):
        store, built = loaded
        hidden = built["anchors"]["bu-b"]["restricted_service"]
        with pytest.raises(NotFound) as denied:
            ServePipeline(store).retrieve(
                _request(hidden), user="alice@bu-a", application="kc-cli"
            )
        with pytest.raises(NotFound) as absent:
            ServePipeline(store).retrieve(
                _request("n_does_not_exist"), user="alice@bu-a", application="kc-cli"
            )
        assert okf.error(denied.value.code) == okf.error(absent.value.code)

    def test_no_restricted_content_reaches_artifacts(self, loaded):
        store, built = loaded
        response = query(
            store, user="alice@bu-a", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        bu_b_requirement = built["anchors"]["bu-b"]["requirement"]
        assert all(a["node_id"] != bu_b_requirement for a in response["artifacts"])

    def test_audit_record_holds_no_exclusion_data(self, loaded):
        """The request record carries what was returned, never what was
        withheld (FR-15.2, §20 DR7)."""
        store, _ = loaded
        response = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup",
            anchor="ledger-api",
        )
        record = store.audit_record(response["request_id"])
        assert record is not None
        assert set(json.loads(record["scope"])) == {"bu-a", "commons"}
        assert "excluded" not in json.dumps(dict(record))


# --- AT-7: topology gives metadata, never content ---------------------------


class TestAT7TopologyMetadataOnly:
    def test_cross_bu_topology_node_is_metadata(self, loaded):
        store, built = loaded
        response = query(
            store, user="bob@bu-b", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        api = next(e for e in response["entities"] if e["id"] == built["anchors"]["bu-a"]["api"])
        assert api["visibility"] == "metadata"

    def test_metadata_entity_carries_only_whitelisted_props(self, loaded):
        store, built = loaded
        response = query(
            store, user="bob@bu-b", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        api = next(e for e in response["entities"] if e["id"] == built["anchors"]["bu-a"]["api"])
        assert set(api["props"]) <= {"name", "status"}
        assert "version" not in api["props"] and "base_path" not in api["props"]

    def test_metadata_entity_has_no_artifacts(self, loaded):
        store, built = loaded
        response = query(
            store, user="bob@bu-b", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        metadata_ids = {e["id"] for e in response["entities"] if e["visibility"] == "metadata"}
        assert all(a["node_id"] not in metadata_ids for a in response["artifacts"])

    def test_cross_bu_edge_is_still_traversable(self, loaded):
        """Topology exists so cross-BU dependency traversal works (FR-5.1)."""
        store, built = loaded
        response = query(
            store, user="alice@bu-a", application="kc-cli",
            intent="impact_analysis", anchor="ledger-api",
        )
        cross = [
            r for r in response["relations"]
            if r["type"] == "DEPENDS_ON" and r["to"] == built["anchors"]["bu-a"]["api"]
        ]
        assert cross, "BU-B's dependency on BU-A's API should be visible"
        assert max(r["corroboration"] for r in cross) == 2


# --- AT-41: the current-claim filter ----------------------------------------


class TestAT41CurrentClaimFilter:
    def test_closed_edges_are_excluded_from_traversal(self, loaded):
        store, built = loaded
        api = built["anchors"]["bu-a"]["api"]
        before = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup", anchor=api,
        )
        endpoint = built["anchors"]["bu-a"]["endpoint"]
        assert any(e["id"] == endpoint for e in before["entities"])

        # Close the PART_OF claim the way a source removal would (FR-6.18).
        from kc.core import ids as id_helpers

        edge = id_helpers.edge_id(endpoint, api, "PART_OF")
        claims = store.open_claims(edge, kind="relationship")
        assert claims
        ChangeAPI(store).submit(
            "kc-ingest", "close_claim", closes=[c.claim_id for c in claims],
            payload={"valid_to": now()},
        )
        GraphProjector(store).run_once()

        after = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup", anchor=api,
        )
        assert all(e["id"] != endpoint for e in after["entities"])

    def test_closed_claims_are_retained_not_deleted(self, loaded):
        store, _ = loaded
        claims = store.open_claims_for_source("jira:fixture:LED-101")
        target = next(c for c in claims if c.prop == "status")
        ChangeAPI(store).submit(
            "kc-ingest", "close_claim", closes=[target.claim_id],
            payload={"valid_to": now()},
        )
        reloaded = store.get_claim(target.claim_id)
        assert reloaded is not None and reloaded.valid_to is not None

    def test_node_with_no_open_identity_claim_is_not_live(self, loaded):
        store, built = loaded
        requirement = built["anchors"]["bu-a"]["requirement"]
        identity_claims = store.open_claims(requirement, kind="identity")
        ChangeAPI(store).submit(
            "kc-ingest", "close_claim", closes=[c.claim_id for c in identity_claims],
            payload={"valid_to": now()},
        )
        GraphProjector(store).run_once()
        node = store.get_node(requirement)
        assert node is not None and node.live is False


# --- ID collision ------------------------------------------------------------


class TestIdCollisionRaises:
    def test_same_id_different_source_key_raises(self, store: Store):
        """A false merge is a segregation breach, so a collision is an error,
        never a silent merge (FR-4.10, FR-6.10)."""
        with store.transaction() as conn:
            store.put_node(
                conn,
                Node(id="n_shared", type="Service", bu_owner="bu-a",
                     visibility_class="topology", source_key="git:one:alpha"),
            )
            with pytest.raises(IdCollision):
                store.put_node(
                    conn,
                    Node(id="n_shared", type="Service", bu_owner="bu-b",
                         visibility_class="topology", source_key="git:two:beta"),
                )

    def test_same_source_key_is_an_update(self, store: Store):
        with store.transaction() as conn:
            store.put_node(
                conn,
                Node(id="n_same", type="Service", bu_owner="bu-a",
                     visibility_class="topology", source_key="git:one:alpha"),
            )
            store.put_node(
                conn,
                Node(id="n_same", type="Service", bu_owner="bu-a",
                     visibility_class="restricted", source_key="git:one:alpha"),
            )
        node = store.get_node("n_same")
        assert node is not None and node.visibility_class == "restricted"


# --- projector halts rather than skips --------------------------------------


class TestProjectorHaltsNeverSkips:
    def test_failure_halts_and_dead_letters(self, loaded, monkeypatch):
        store, _ = loaded
        projector = GraphProjector(store)

        # Append two more events; the first will fail to apply.
        change = ChangeAPI(store)
        change.submit("kc-ingest", "chunk_add", payload={
            "chunk_id": "k_boom", "node_id": "n_missing", "bu_owner": "bu-a",
            "kind": "doc", "section_path": "x", "content": "boom",
        })
        change.submit("kc-ingest", "chunk_add", payload={
            "chunk_id": "k_ok", "node_id": "n_missing", "bu_owner": "bu-a",
            "kind": "doc", "section_path": "y", "content": "fine",
        })

        original = GraphProjector.apply
        calls = {"n": 0}

        def flaky(self, conn, event):
            calls["n"] += 1
            if json.loads(event["payload"]).get("chunk_id") == "k_boom":
                raise RuntimeError("projector bug")
            return original(self, conn, event)

        monkeypatch.setattr(GraphProjector, "apply", flaky)
        result = projector.run_once()

        assert result["halted"] is True
        state = store.projector_state("graph")
        assert state["halted"] is True
        assert store.dead_letters(), "the failing event must be dead-lettered"
        # The event after the failure is not applied: halting beats skipping.
        conn = store.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE chunk_id='k_ok'"
            ).fetchone()
        finally:
            conn.close()
        assert int(row["n"]) == 0

    def test_halted_projector_does_not_advance(self, loaded, monkeypatch):
        store, _ = loaded
        store.halt_projector("graph", 99, "manual halt")
        result = GraphProjector(store).run_once()
        assert result["applied"] == 0 and result["halted"] is True


# --- rebuild reproduces the projection exactly ------------------------------


class TestRebuildIsFaithful:
    def test_rebuild_reproduces_the_fingerprint(self, loaded):
        store, _ = loaded
        before = store.graph_fingerprint()
        result = GraphProjector(store).rebuild()
        assert result["fingerprint"] == before

    def test_rebuild_needs_only_the_log(self, loaded):
        """Raw snapshots are for re-extraction, not for rebuild (NFR-5)."""
        store, _ = loaded
        before = store.graph_fingerprint()
        with store.transaction() as conn:
            conn.execute("DELETE FROM source_hashes")
        assert GraphProjector(store).rebuild()["fingerprint"] == before

    def test_projection_is_empty_before_replay(self, loaded):
        store, _ = loaded
        with store.transaction() as conn:
            store.clear_projection(conn)
            store.set_high_water(conn, "graph", 0)
        assert store.counts()["nodes_live"] == 0
        GraphProjector(store).run_once()
        assert store.counts()["nodes_live"] > 0


# --- a chunk may not outlive its node ---------------------------------------


class TestChunkLiveness:
    def test_chunk_of_a_closed_node_is_dropped(self, loaded):
        store, built = loaded
        requirement = built["anchors"]["bu-a"]["requirement"]
        hits = store.bm25_search("partial refunds ledger", ["bu-a", "commons"])
        assert any(h["node_id"] == requirement for h in hits)

        identity_claims = store.open_claims(requirement, kind="identity")
        ChangeAPI(store).submit(
            "kc-ingest", "close_claim", closes=[c.claim_id for c in identity_claims],
            payload={"valid_to": now()},
        )
        GraphProjector(store).run_once()

        hits_after = store.bm25_search("partial refunds ledger", ["bu-a", "commons"])
        assert all(h["node_id"] != requirement for h in hits_after)

    def test_retracted_chunks_leave_the_serve_path(self, loaded):
        store, built = loaded
        endpoint = built["anchors"]["bu-a"]["endpoint"]
        ChangeAPI(store).submit("kc-ingest", "chunk_retract", payload={"node_id": endpoint})
        GraphProjector(store).run_once()
        response = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup", anchor=endpoint,
        )
        assert all(a["node_id"] != endpoint for a in response["artifacts"])


# --- the gate enforces the inbreeding guard ---------------------------------


class TestGateInbreedingGuard:
    def _agent_claim(self, store: Store, evidence: tuple[str, ...]) -> Claim:
        claim = Claim(
            kind="property", subject_id="n_agentish", prop="status", value="guessed",
            node_type="Service", bu_owner="bu-a", source_system="kc-agent",
            source_instance="resolver", source_id="run_1", valid_from=now(),
            provenance_class="agent-derived", extraction_method="agent", evidence=evidence,
        )
        with store.transaction() as conn:
            seq = store.append_event(
                conn, op="add_claim", identity="kc-curator", payload={"claims": []}
            )
            store.insert_claim(conn, claim, seq)
        return claim

    def test_agent_evidence_for_an_agent_claim_is_rejected(self, loaded):
        store, _ = loaded
        agent_claim = self._agent_claim(store, evidence=("c_whatever",))
        proposal = {
            "proposal_id": "p_1",
            "changes": [{"op": "add_claim", "provenance_class": "agent-derived"}],
            "evidence": [{"claim_id": agent_claim.claim_id}],
            "subjects": [],
        }
        with pytest.raises(InbreedingBlocked):
            Gate(store).evaluate(proposal)

    def test_source_evidence_for_an_agent_claim_is_accepted(self, loaded):
        store, built = loaded
        source_claim = store.open_claims(built["anchors"]["bu-a"]["api"])[0]
        proposal = {
            "proposal_id": "p_2",
            "changes": [{"op": "alias_rule", "provenance_class": "agent-derived"}],
            "evidence": [{"claim_id": source_claim.claim_id}],
            "subjects": [{"id": built["anchors"]["bu-a"]["api"]}],
        }
        result = Gate(store).evaluate(proposal)
        assert result.accepted and 0.0 < result.tier_input.confidence < 1.0

    def test_a_proposal_without_evidence_is_rejected(self, loaded):
        store, _ = loaded
        with pytest.raises(InbreedingBlocked):
            Gate(store).evaluate({"proposal_id": "p_3", "changes": [], "evidence": []})

    def test_eligibility_is_per_claim_not_per_node(self, loaded):
        """A node carrying both agent- and source-derived claims can still be
        supported by its source-derived ones (FR-13.1 item 4)."""
        store, built = loaded
        api = built["anchors"]["bu-a"]["api"]
        self._agent_claim(store, evidence=("c_x",))
        source_claim = next(c for c in store.open_claims(api) if c.provenance_class == "source-derived")
        result = Gate(store).evaluate(
            {
                "proposal_id": "p_4",
                "changes": [{"op": "add_claim", "provenance_class": "agent-derived"}],
                "evidence": [{"claim_id": source_claim.claim_id}],
                "subjects": [{"id": api}],
            }
        )
        assert result.accepted

    def test_agent_reported_confidence_is_ignored(self, loaded):
        store, built = loaded
        source_claim = store.open_claims(built["anchors"]["bu-a"]["api"])[0]
        proposal = {
            "proposal_id": "p_5",
            "confidence": 0.99,  # an agent cannot buy its way through the gate
            "changes": [{"op": "alias_rule", "provenance_class": "agent-derived"}],
            "evidence": [{"claim_id": source_claim.claim_id}],
            "subjects": [{"id": built["anchors"]["bu-a"]["api"]}],
        }
        result = Gate(store).evaluate(proposal)
        assert result.tier_input.confidence != 0.99


# --- tier gate and dry-run ---------------------------------------------------


class TestTierGate:
    def test_dry_run_records_a_task_and_appends_nothing(self, loaded):
        store, built = loaded
        change = ChangeAPI(store)
        change.set_dry_run(True)
        before = store.last_seq()
        receipt = change.submit(
            "kc-curator", "add_claim", claims=[],
            payload={"scope": ["bu-a"]},
            gate=GateDecision(confidence=0.95, evidence=("c_1",), single_bu=True,
                              proposal_id="p_dry"),
        )
        assert receipt.applied is False and receipt.task_id
        assert store.last_seq() == before
        assert store.get_task(receipt.task_id)["state"] == "input-required"

    def test_t4_is_human_always_even_at_high_confidence(self, loaded):
        store, _ = loaded
        change = ChangeAPI(store)
        change.set_dry_run(False)
        receipt = change.submit(
            "kc-curator", "alias_rule",
            payload={"alias_key": "ledger", "node_id": "n_x", "scope": ["bu-a"]},
            gate=GateDecision(confidence=0.99, evidence=("c_1",), single_bu=True,
                              proposal_id="p_t4"),
        )
        assert receipt.applied is False and receipt.tier == "T4"

    def test_ingest_is_never_gated(self, loaded):
        store, _ = loaded
        ChangeAPI(store).set_dry_run(True)
        claim = Claim(
            kind="identity", subject_id="n_new_service", node_type="Service", bu_owner="bu-a",
            source_system="git", source_instance="fixture", source_id="acme/new", valid_from=now(),
        )
        receipt = ChangeAPI(store).submit("kc-ingest", "add_claim", claims=[claim])
        assert receipt.applied is True

    def test_kill_switch_leaves_the_fast_path_alone(self, loaded):
        store, _ = loaded
        change = ChangeAPI(store)
        change.set_cognitive_layer(False)
        claim = Claim(
            kind="identity", subject_id="n_during_killswitch", node_type="Service",
            bu_owner="bu-a", source_system="git", source_instance="fixture",
            source_id="acme/during", valid_from=now(),
        )
        assert change.submit("kc-ingest", "add_claim", claims=[claim]).applied is True
        response = query(
            store, user="alice@bu-a", application="kc-cli", intent="lookup", anchor="ledger-api"
        )
        assert response["message_type"] == okf.CONTEXT_RESPONSE


# --- entitlement module has no post-filter ----------------------------------


def test_entitlements_module_exposes_no_post_filter():
    """Deliberate absence: a post-filter helper would be reached for under
    deadline, and post-filtering is how metadata leaks (FR-5.4)."""
    names = [n for n in dir(ent) if not n.startswith("_")]
    assert not [n for n in names if "post" in n.lower()]
    assert "node_sql" in names and "edge_endpoint_sql" in names
