"""The schema and identity tables are configuration the validator reads.

These tests assert the properties the spec relies on, including completeness:
a node type with no visibility default or no TTL is the v0.1 gap that FR-5.2
and FR-14.1 exist to close.
"""

import pytest

from kc.config import load_identities, load_schema

# FR-4.1 and FR-4.2, verbatim.
NODE_TYPES = {
    "Service", "Component", "API", "Endpoint", "Topic", "Requirement", "TestCase",
    "TestRun", "Defect", "Document", "Environment", "Team", "BusinessGroup", "DataEntity",
}
EDGE_TYPES = {
    "DEPENDS_ON", "CALLS", "PUBLISHES_TO", "CONSUMES_FROM", "VALIDATES", "COVERS",
    "RAISED_AGAINST", "IMPLEMENTS", "OWNED_BY", "DOCUMENTS", "SUPERSEDES",
    "DERIVED_FROM", "PART_OF", "EXECUTES", "DEPLOYED_TO", "MEMBER_OF", "READS", "WRITES",
}


@pytest.fixture(scope="module")
def schema():
    return load_schema()


@pytest.fixture(scope="module")
def identities():
    return load_identities()


class TestSchemaCompleteness:
    def test_every_node_type_present(self, schema):
        assert set(schema.node_types) == NODE_TYPES

    def test_every_edge_type_present(self, schema):
        assert set(schema.edges) == EDGE_TYPES

    def test_every_node_type_has_visibility_and_ttl(self, schema):
        for node_type in NODE_TYPES:
            assert schema.default_visibility(node_type) in schema.visibility_classes
            assert schema.ttl_days(node_type) > 0

    def test_every_edge_target_is_a_real_node_type(self, schema):
        for edge_type, rule in schema.edges.items():
            referenced = set(rule.from_) | set(rule.to)
            for pair in rule.pairs:
                referenced |= {pair.from_, pair.to}
            unknown = referenced - NODE_TYPES
            assert not unknown, f"{edge_type} references {unknown}"

    def test_indexed_properties_are_on_schema(self, schema):
        for prop in schema.indexed_properties:
            assert schema.property_rule("Service", prop) is not None


class TestEdgeRules:
    def test_async_edges_target_topic(self, schema):
        # The reason Topic was added: PUBLISHES_TO had no valid target (M2).
        assert schema.edge_permitted("PUBLISHES_TO", "Service", "Topic")
        assert not schema.edge_permitted("PUBLISHES_TO", "Service", "Requirement")

    def test_containment_pairs_are_directional(self, schema):
        assert schema.edge_permitted("PART_OF", "Endpoint", "API")
        assert not schema.edge_permitted("PART_OF", "API", "Endpoint")
        assert not schema.edge_permitted("PART_OF", "Endpoint", "Service")

    def test_supersedes_is_same_type_only(self, schema):
        assert schema.edge_permitted("SUPERSEDES", "Service", "Service")
        assert not schema.edge_permitted("SUPERSEDES", "Service", "API")

    def test_unknown_edge_type_is_off_schema(self, schema):
        assert not schema.edge_permitted("INVENTED_BY_AN_LLM", "Service", "API")

    def test_single_target_edges_are_cardinality_one(self, schema):
        # These are the edges whose second open claim is a contradiction (FR-6.11).
        for edge_type in ("OWNED_BY", "PART_OF", "EXECUTES", "MEMBER_OF"):
            assert schema.edge_cardinality(edge_type) == "one"


class TestProperties:
    def test_wildcard_properties_apply_to_every_type(self, schema):
        assert schema.is_single_valued("Topic", "name")
        assert schema.is_single_valued("Defect", "status")

    def test_type_specific_property(self, schema):
        assert schema.is_single_valued("Endpoint", "path")
        assert not schema.is_single_valued("TestCase", "file_path")

    def test_multi_source_keys_are_multi_valued(self, schema):
        # One TestCase can carry a Git path and a Jira key (FR-6.19).
        assert not schema.is_single_valued("TestCase", "key")

    def test_off_schema_property_raises(self, schema):
        with pytest.raises(KeyError):
            schema.is_single_valued("Service", "vibe")


class TestVisibilityDefaults:
    def test_v01_defaults_are_preserved(self, schema):
        assert schema.default_visibility("Service") == "topology"
        assert schema.default_visibility("API") == "topology"
        for node_type in ("Requirement", "Defect", "TestCase"):
            assert schema.default_visibility(node_type) == "restricted"

    def test_standards_and_glossary_are_public(self, schema):
        assert schema.default_visibility("Document", "standard") == "public"
        assert schema.default_visibility("Document", "glossary_term") == "public"

    def test_pages_stay_restricted(self, schema):
        for subtype in ("page", "adr", "incident"):
            assert schema.default_visibility("Document", subtype) == "restricted"

    def test_topology_whitelist_holds_no_content_fields(self, schema):
        fields = set(schema.topology_fields())
        assert fields == {"id", "type", "subtype", "name", "status", "bu_owner", "live"}
        assert "url" not in fields and "path" not in fields


class TestEmbedding:
    def test_only_confluence_prose_is_embedded(self, schema):
        assert schema.is_embedded("Document", "page")
        assert schema.is_embedded("Document", "adr")
        assert not schema.is_embedded("Document", "standard")
        assert not schema.is_embedded("Requirement", None)
        assert not schema.is_embedded("TestCase", None)


class TestAuthority:
    def test_code_outranks_confluence_by_default(self, schema):
        assert schema.authority_rank("git", "Service") > schema.authority_rank("confluence", "Service")

    def test_per_property_override_beats_default_order(self, schema):
        # Jira is the only authority on requirement status, whatever the global order.
        assert schema.authority_rank("jira", "Requirement", "status") > schema.authority_rank(
            "git", "Requirement", "status"
        )
        # Without the override, git still outranks jira.
        assert schema.authority_rank("jira", "Service", "name") < schema.authority_rank(
            "git", "Service", "name"
        )

    def test_human_confirmation_outranks_every_source(self, schema):
        confirmed = schema.authority_rank("kc-review", "Service", "name", "human-confirmed")
        assert confirmed > max(schema.authority.default_order.values())

    def test_unknown_source_has_no_authority(self, schema):
        assert schema.authority_rank("random-wiki", "Service") == 0


class TestWriteIdentities:
    def test_fast_path_writes_without_a_gate(self, identities):
        # B1: ingestion must write under the kill switch and in dry-run.
        assert identities.may("kc-ingest", "add_claim")
        assert identities.may("kc-ingest", "update_claim_same_source")
        assert identities.may("kc-ingest", "close_claim")
        assert not identities.is_tier_gated("kc-ingest")
        assert not identities.dry_run_applies("kc-ingest")

    def test_fast_path_cannot_merge_or_write_alias_rules(self, identities):
        assert not identities.may("kc-ingest", "merge_nodes")
        assert not identities.may("kc-ingest", "alias_rule")

    def test_source_key_mapping_is_not_an_alias_rule(self, identities):
        # The pair B1 asked to separate: one is ungated, the other is T4.
        assert identities.may("kc-ingest", "source_key_mapping")
        assert not identities.may("kc-ingest", "alias_rule")
        assert identities.may("kc-curator", "alias_rule")

    def test_curator_is_gated_and_dry_run_applies(self, identities):
        assert identities.may("kc-curator", "merge_nodes")
        assert identities.is_tier_gated("kc-curator")
        assert identities.dry_run_applies("kc-curator")

    def test_tightening_visibility_is_ungated_loosening_is_not(self, identities):
        assert identities.may("kc-ingest", "visibility_tighten")
        assert not identities.may("kc-ingest", "visibility_loosen")
        assert identities.may("kc-curator", "visibility_loosen")

    def test_maintenance_is_limited_to_signals(self, identities):
        assert identities.may("kc-maintenance", "quality_signal")
        assert not identities.may("kc-maintenance", "add_claim")

    def test_reviewer_writes_are_human_confirmed(self, identities):
        assert identities.may("kc-review", "merge_nodes")
        assert identities.constraint("kc-review", "provenance_class") == "human-confirmed"

    def test_operator_owns_rollback_and_erasure(self, identities):
        assert identities.may("kc-operator", "rollback")
        assert identities.may("kc-operator", "erasure")
        assert not identities.may("kc-operator", "add_claim")

    def test_spokes_and_consumers_may_write_nothing(self, identities):
        # AT-4.
        for identity in identities.denied:
            for operation in identities.operations:
                assert not identities.may(identity, operation)

    def test_unknown_identity_is_denied(self, identities):
        assert not identities.may("someone-elses-service", "add_claim")
        assert not identities.is_tier_gated("someone-elses-service")

    def test_every_permitted_operation_is_declared(self, identities):
        declared = set(identities.operations)
        for name, rule in identities.identities.items():
            unknown = set(rule.may) - declared
            assert not unknown, f"{name} permits undeclared {unknown}"
