"""The fast path end to end, without credentials (FR-6.2, FR-6.18, FR-5.3).

Source objects are constructed directly here, which is exactly what the deferred
connectors will hand over once `fetch` is wired: everything after the fetch —
hash projection, extraction, resolution, claim-set diff, append — is implemented
and tested now.
"""

from __future__ import annotations

import json

import pytest

from kc.core.errors import Quarantined
from kc.core.projector import GraphProjector
from kc.connectors.base import SourceObject
from kc.ingest import FastPath
from kc.stores.sqlite_store import Store

SPEC_V1 = {
    "openapi": "3.1.0",
    "info": {"title": "ledger-api", "version": "1.4.0"},
    "paths": {
        "/accounts/{id}/entries": {"get": {"summary": "List entries"}},
        "/legacy-export": {"get": {"summary": "Legacy CSV export"}},
    },
}
SPEC_V2 = {
    "openapi": "3.1.0",
    "info": {"title": "ledger-api", "version": "1.5.0"},
    "paths": {"/accounts/{id}/entries": {"get": {"summary": "List entries"}}},
}


def _spec_object(spec: dict, committed_at: str = "2026-02-01T00:00:00+00:00") -> SourceObject:
    return SourceObject(
        source_system="git",
        source_instance="fixture",
        source_id="acme/ledger-api:openapi.json",
        container="acme/ledger-api",
        content={"path": "openapi.json", "committed_at": committed_at, "sha": "abc123"},
        raw=json.dumps(spec),
    )


class TestChangeDetection:
    def test_first_ingest_writes_claims(self, store: Store):
        result = FastPath(store).ingest_object(_spec_object(SPEC_V1))
        assert result.status == "ingested" and result.claims > 0

    def test_replay_of_an_unchanged_object_is_dropped(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        before = store.last_seq()
        again = fast.ingest_object(_spec_object(SPEC_V1))
        assert again.status == "dropped_unchanged"
        assert store.last_seq() == before, "a dropped event must append nothing (AT-1)"

    def test_volatile_commit_metadata_does_not_defeat_the_drop(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        moved = _spec_object(SPEC_V1)
        moved.content["sha"] = "deadbeef"  # a new commit, identical artefact
        assert fast.ingest_object(moved).status == "dropped_unchanged"

    def test_no_llm_was_invoked(self, store: Store):
        result = FastPath(store).ingest_object(_spec_object(SPEC_V1))
        assert result.llm_used is False
        stats = store.ingest_stats()
        assert stats["llm"] == 0


class TestClaimSetDiff:
    def test_removed_endpoint_is_closed_not_deleted(self, store: Store):
        """An endpoint removed from the spec stops being current, and the claim
        stays in the store (FR-6.18, AT-22)."""
        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        GraphProjector(store).run_once()

        legacy = [
            node for node in store.nodes_by_type("Endpoint")
            if node.value("path") == "/legacy-export"
        ]
        assert legacy, "first spec should have produced the legacy endpoint"
        legacy_id = legacy[0].id

        result = fast.ingest_object(_spec_object(SPEC_V2, committed_at="2026-03-01T00:00:00+00:00"))
        assert result.closed > 0
        GraphProjector(store).run_once()

        node = store.get_node(legacy_id)
        assert node is not None and node.live is False
        closed_claims = [
            c for c in store.open_claims(legacy_id, kind="identity")
        ]
        assert not closed_claims

    def test_surviving_endpoint_stays_live(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        fast.ingest_object(_spec_object(SPEC_V2, committed_at="2026-03-01T00:00:00+00:00"))
        GraphProjector(store).run_once()
        live_paths = {
            node.value("path") for node in store.nodes_by_type("Endpoint") if node.live
        }
        assert live_paths == {"/accounts/{id}/entries"}

    def test_deleted_source_object_closes_everything(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        deleted = _spec_object(SPEC_V1)
        deleted.deleted = True
        result = fast.ingest_object(deleted)
        assert result.status == "deleted" and result.closed > 0
        GraphProjector(store).run_once()
        assert all(not node.live for node in store.nodes_by_type("Endpoint", live_only=False))


class TestBuMapping:
    def test_unmapped_container_is_quarantined(self, store: Store):
        obj = SourceObject(
            source_system="git", source_instance="fixture",
            source_id="acme/not-ours:openapi.json", container="acme/not-ours",
            content={"path": "openapi.json"}, raw=json.dumps(SPEC_V1),
        )
        with pytest.raises(Quarantined):
            FastPath(store).ingest_object(obj)
        assert store.last_seq() == 0
        assert store.skip_counts().get("UNMAPPED_CONTAINER") == 1

    def test_bu_owner_comes_from_the_mapping(self, store: Store):
        FastPath(store).ingest_object(_spec_object(SPEC_V1))
        GraphProjector(store).run_once()
        assert {node.bu_owner for node in store.nodes_by_type("Endpoint")} == {"bu-a"}


class TestTestCodeIsAuthoritative:
    def test_pytest_file_yields_testcase_and_covers(self, store: Store):
        from kc.connectors.git_openapi import GitConnector, endpoint_index

        fast = FastPath(store)
        fast.ingest_object(_spec_object(SPEC_V1))
        GraphProjector(store).run_once()

        spec_claims = GitConnector().extract(_spec_object(SPEC_V1), "bu-a").claims
        index = endpoint_index(spec_claims)
        test_obj = SourceObject(
            source_system="git", source_instance="fixture",
            source_id="acme/ledger-api:tests/test_entries.py",
            container="acme/ledger-api",
            content={"path": "tests/test_entries.py", "endpoint_index": index},
            raw=(
                'def test_entries_listed(client):\n'
                '    response = client.get("/accounts/99/entries")\n'
                '    assert response.status_code == 200\n'
            ),
        )
        fast.ingest_object(test_obj)
        GraphProjector(store).run_once()

        tests = store.nodes_by_type("TestCase")
        assert len(tests) == 1
        assert store.has_incident_edge(tests[0].id, "COVERS", "out")
