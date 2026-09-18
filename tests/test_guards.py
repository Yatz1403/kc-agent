"""Permanent guards against failure modes that produce no error.

Each class here exists because the failure it catches is silent. A dropped
event, a revived chunk, a fold that depends on arrival order and a memory
recall that crosses a BU boundary all look like a working system from the
outside, and would keep looking that way indefinitely.
"""

from __future__ import annotations

import copy
import itertools
import json
from typing import Any

import pytest

from kc.agents.tools import ToolContext, Tools
from kc.connectors import registry
from kc.connectors.base import SourceObject
from kc.core import ids
from kc.core.changeapi import ChangeAPI
from kc.core.projector import GraphProjector
from kc.ingest import FastPath
from kc.stores.sqlite_store import Store


def _extract(connector: Any, obj: SourceObject, bu: str = "bu-a"):
    if connector.source_system == "confluence":
        return connector.extract(obj, bu, client=None)
    return connector.extract(obj, bu)


def _extraction_signature(connector: Any, obj: SourceObject) -> str:
    """Everything `extract` read, in a comparable form.

    Claims, chunks and mentions together: if a mutation moves any of these, the
    hash projection has to move with it.
    """
    result = _extract(connector, obj)
    return json.dumps(
        {
            "claims": sorted(
                json.dumps(
                    {k: v for k, v in c.as_row().items()
                     if k not in ("claim_id", "recorded_at", "last_verified_at")},
                    sort_keys=True,
                )
                for c in result.claims
            ),
            "chunks": sorted(json.dumps(c, sort_keys=True) for c in result.chunks),
            "mentions": sorted(
                json.dumps({k: v for k, v in m.items() if k != "candidate_id"}, sort_keys=True)
                for m in result.mentions
            ),
        },
        sort_keys=True,
    )


def _leaf_paths(value: Any, prefix: tuple = ()) -> list[tuple]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _leaf_paths(v, prefix + (k,))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _leaf_paths(v, prefix + (i,))]
    return [prefix]


def _mutate(obj: SourceObject, path: tuple) -> SourceObject:
    clone = SourceObject(
        source_system=obj.source_system, source_instance=obj.source_instance,
        source_id=obj.source_id, container=obj.container,
        content=copy.deepcopy(obj.content), raw=obj.raw, deleted=obj.deleted,
    )
    target: Any = clone.content
    for key in path[:-1]:
        target = target[key]
    current = target[path[-1]]
    if isinstance(current, bool):
        target[path[-1]] = not current
    elif isinstance(current, (int, float)):
        target[path[-1]] = current + 7
    elif current is None:
        target[path[-1]] = "mutated"
    else:
        target[path[-1]] = f"{current}-mutated"
    return clone


CONNECTORS = sorted(registry.all_connectors().items())


class TestHashProjectionInvariant:
    """If a mutation changes what `extract` reads, the hash must change.

    The cases are derived from each connector's own samples rather than written
    by hand, so a new connector cannot ship without declaring samples: the
    parametrisation fails on `self_check_samples` raising NotImplementedError.
    """

    @pytest.mark.parametrize("name,connector", CONNECTORS, ids=[n for n, _ in CONNECTORS])
    def test_connector_declares_samples(self, name, connector):
        samples = connector.self_check_samples()
        assert samples, f"{name} must declare at least one self-check sample"
        for sample in samples:
            assert sample.source_system == name

    @pytest.mark.parametrize("name,connector", CONNECTORS, ids=[n for n, _ in CONNECTORS])
    def test_extraction_relevant_mutations_change_the_hash(self, name, connector):
        checked = 0
        for sample in connector.self_check_samples():
            base_signature = _extraction_signature(connector, sample)
            base_hash = connector.hash_projection(sample)
            for path in _leaf_paths(sample.content):
                mutated = _mutate(sample, path)
                try:
                    mutated_signature = _extraction_signature(connector, mutated)
                except Exception:  # noqa: BLE001 - a mutation the parser rejects
                    continue
                if mutated_signature == base_signature:
                    continue  # the field is not extraction-relevant: no claim on it
                checked += 1
                assert connector.hash_projection(mutated) != base_hash, (
                    f"{name}: mutating {'.'.join(map(str, path))} changed extraction but "
                    f"not the hash projection — the event would be dropped silently (FR-6.2)"
                )
        assert checked, f"{name}: no extraction-relevant field was exercised"

    @pytest.mark.parametrize("name,connector", CONNECTORS, ids=[n for n, _ in CONNECTORS])
    def test_body_changes_change_the_hash(self, name, connector):
        """The bug this guards: the body lived in `raw`, the hash covered only
        `content`, so a changed OpenAPI spec looked unchanged."""
        for sample in connector.self_check_samples():
            if not sample.raw:
                continue
            mutated = SourceObject(
                source_system=sample.source_system, source_instance=sample.source_instance,
                source_id=sample.source_id, container=sample.container,
                content=copy.deepcopy(sample.content), raw=sample.raw + " changed",
            )
            assert connector.hash_projection(mutated) != connector.hash_projection(sample)

    @pytest.mark.parametrize("name,connector", CONNECTORS, ids=[n for n, _ in CONNECTORS])
    def test_volatile_mutations_keep_the_hash(self, name, connector):
        """The other direction: churn in volatile fields must not look like a
        change, or nothing is ever dropped (N1)."""
        exercised = 0
        for sample in connector.self_check_samples():
            base_hash = connector.hash_projection(sample)
            for path in _leaf_paths(sample.content):
                if not set(map(str, path)) & set(connector.volatile_fields):
                    continue
                exercised += 1
                assert connector.hash_projection(_mutate(sample, path)) == base_hash, (
                    f"{name}: {'.'.join(map(str, path))} is declared volatile but moves the hash"
                )
        assert exercised, f"{name}: samples exercise no declared volatile field"


class TestEndpointClosure:
    """The only path by which the graph ever shrinks (FR-6.18, M8)."""

    SPEC_TWO = {
        "openapi": "3.1.0",
        "info": {"title": "ledger-api", "version": "2.0.0"},
        "paths": {
            "/accounts/{id}/entries": {"get": {"summary": "List entries"}},
            "/legacy-export": {"get": {"summary": "Legacy CSV export"}},
        },
    }
    SPEC_ONE = {
        "openapi": "3.1.0",
        "info": {"title": "ledger-api", "version": "2.1.0"},
        "paths": {"/accounts/{id}/entries": {"get": {"summary": "List entries"}}},
    }

    def _spec(self, spec: dict, when: str) -> SourceObject:
        return SourceObject(
            source_system="git", source_instance="closure",
            source_id="acme/ledger-api:openapi.json", container="acme/ledger-api",
            content={"path": "openapi.json", "committed_at": when},
            raw=json.dumps(spec),
        )

    def test_removed_endpoint_closes_node_and_edge(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(self._spec(self.SPEC_TWO, "2026-04-01T00:00:00+00:00"))
        GraphProjector(store).run_once()

        legacy = next(
            node for node in store.nodes_by_type("Endpoint")
            if node.value("path") == "/legacy-export"
        )
        api = next(node for node in store.nodes_by_type("API"))
        edge_id = ids.edge_id(legacy.id, api.id, "PART_OF")
        assert store.get_edge(edge_id).valid_to is None

        result = fast.ingest_object(self._spec(self.SPEC_ONE, "2026-05-01T00:00:00+00:00"))
        assert result.closed > 0, "the claim-set diff must close what the source dropped"
        GraphProjector(store).run_once()

        # The node stops being live, the edge gets a valid_to, and both remain.
        assert store.get_node(legacy.id).live is False
        closed_edge = store.get_edge(edge_id)
        assert closed_edge is not None
        assert closed_edge.valid_to is not None

        surviving = next(
            node for node in store.nodes_by_type("Endpoint")
            if node.value("path") == "/accounts/{id}/entries"
        )
        assert surviving.live is True
        assert store.get_edge(ids.edge_id(surviving.id, api.id, "PART_OF")).valid_to is None

    def test_closed_endpoint_leaves_the_serve_path(self, store: Store):
        from kc.serve.pipeline import query

        fast = FastPath(store)
        fast.ingest_object(self._spec(self.SPEC_TWO, "2026-04-01T00:00:00+00:00"))
        GraphProjector(store).run_once()
        api = next(node for node in store.nodes_by_type("API"))
        before = query(store, user="alice@bu-a", application="kc-cli",
                       intent="lookup", anchor=api.id)
        assert len(before["entities"]) == 3  # the API and both endpoints

        fast.ingest_object(self._spec(self.SPEC_ONE, "2026-05-01T00:00:00+00:00"))
        GraphProjector(store).run_once()
        after = query(store, user="alice@bu-a", application="kc-cli",
                      intent="lookup", anchor=api.id)
        paths = {
            (e["props"].get("path") or {}).get("value")
            for e in after["entities"] if e["type"] == "Endpoint"
        }
        assert paths == {"/accounts/{id}/entries"}


class TestRebuildDeterminism:
    """Property folding picks by authority rank, so arrival order must not
    matter for independent events. If it does, the fold has a hidden dependency
    on insertion sequence and it surfaces later as unexplained drift.

    One thing genuinely is causal: a `close_claim` names a claim an earlier
    event created. Projectors consume the log in sequence (FR-7.9), so that is
    a legitimate dependency — and it is asserted here as a halt rather than
    left to look like order independence that happens to work.
    """

    @staticmethod
    def _replay_into(target: Store, events: list[dict]) -> str:
        with target.transaction() as conn:
            for event in events:
                conn.execute(
                    "INSERT INTO change_log (event_id, op, identity, tier, payload, run_id,"
                    " context_id, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (event["event_id"], event["op"], event["identity"], event["tier"],
                     event["payload"], event["run_id"], event["context_id"],
                     event["recorded_at"]),
                )
        GraphProjector(target).run_once()
        return target.graph_fingerprint()

    def _events(self, store: Store) -> list[dict]:
        return [dict(row) for row in store.read_events(0, limit=10_000)]

    # Operations that name something an earlier event created. Permuting these
    # is not a scenario the system must satisfy; halting on them is (below).
    CAUSAL_OPS = {"close_claim", "supersede_claim_cross_source", "merge_nodes",
                  "visibility_tighten", "visibility_loosen"}

    def _independent(self, store: Store) -> tuple[list[dict], str]:
        """The independent prefix of the log, and the fingerprint it produces."""
        events = [e for e in self._events(store) if e["op"] not in self.CAUSAL_OPS]
        return events, ""

    def test_reversed_arrival_order_gives_the_same_graph(self, loaded, tmp_path):
        store, _ = loaded
        events, _ = self._independent(store)

        forward = Store(tmp_path / "forward.db")
        forward.init_schema()
        expected = self._replay_into(forward, events)

        reverse = Store(tmp_path / "reversed.db")
        reverse.init_schema()
        assert self._replay_into(reverse, list(reversed(events))) == expected

    def test_several_permutations_agree(self, loaded, tmp_path):
        store, _ = loaded
        events, _ = self._independent(store)

        forward = Store(tmp_path / "perm_forward.db")
        forward.init_schema()
        expected = self._replay_into(forward, events)

        # Deterministic permutations rather than random ones, so a failure is
        # reproducible from the test name alone.
        orders = [events[i:] + events[:i] for i in (1, 3, len(events) - 1)]
        interleaved = list(
            itertools.chain.from_iterable(
                itertools.zip_longest(events[::2], events[1::2], fillvalue=None)
            )
        )
        orders.append([e for e in interleaved if e is not None])

        for index, order in enumerate(orders):
            other = Store(tmp_path / f"perm_{index}.db")
            other.init_schema()
            assert self._replay_into(other, order) == expected, f"permutation {index} diverged"

    def test_a_visibility_change_before_its_node_halts(self, loaded, tmp_path):
        """The divergence this class originally caught: replayed before the
        node existed, a tighten was silently lost and the fold reset the class
        to the type default. It halts now."""
        store, _ = loaded
        events = self._events(store)
        tighten = [e for e in events if e["op"] == "visibility_tighten"]
        assert tighten, "the fixture should tighten one node"

        other = Store(tmp_path / "vis_first.db")
        other.init_schema()
        with other.transaction() as conn:
            for event in tighten + [e for e in events if e["op"] != "visibility_tighten"]:
                conn.execute(
                    "INSERT INTO change_log (event_id, op, identity, tier, payload, run_id,"
                    " context_id, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (event["event_id"], event["op"], event["identity"], event["tier"],
                     event["payload"], event["run_id"], event["context_id"],
                     event["recorded_at"]),
                )
        result = GraphProjector(other).run_once()
        assert result["halted"] is True
        assert "sequence order" in (result.get("error") or "")

    def test_claim_order_within_an_event_does_not_matter(self, loaded, tmp_path):
        """The fold sorts by authority, valid_from then claim_id, so the order
        claims appear in a payload must not change the result."""
        store, _ = loaded
        expected = store.graph_fingerprint()
        events = self._events(store)
        shuffled = []
        for event in events:
            payload = json.loads(event["payload"])
            if payload.get("claims"):
                payload["claims"] = list(reversed(payload["claims"]))
            shuffled.append(dict(event, payload=json.dumps(payload)))

        other = Store(tmp_path / "claims_reversed.db")
        other.init_schema()
        assert self._replay_into(other, shuffled) == expected

    def test_a_close_replayed_before_its_claim_halts(self, store: Store, tmp_path):
        """Causality, asserted. Out-of-sequence replay must stop, not diverge."""
        from kc.core.model import Claim, now as _now

        claim = Claim(
            kind="identity", subject_id="n_causal", node_type="Service", bu_owner="bu-a",
            source_system="git", source_instance="t", source_id="acme/ledger-api",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        change = ChangeAPI(store)
        change.submit("kc-ingest", "add_claim", claims=[claim])
        change.submit("kc-ingest", "close_claim", closes=[claim.claim_id],
                      payload={"valid_to": _now()})
        events = self._events(store)

        other = Store(tmp_path / "causal.db")
        other.init_schema()
        with other.transaction() as conn:
            for event in reversed(events):
                conn.execute(
                    "INSERT INTO change_log (event_id, op, identity, tier, payload, run_id,"
                    " context_id, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (event["event_id"], event["op"], event["identity"], event["tier"],
                     event["payload"], event["run_id"], event["context_id"],
                     event["recorded_at"]),
                )
        result = GraphProjector(other).run_once()
        assert result["halted"] is True
        assert "sequence order" in (result.get("error") or "")

    def test_replay_in_batches_matches_replay_in_one_pass(self, loaded, tmp_path):
        store, _ = loaded
        expected = store.graph_fingerprint()
        events = self._events(store)

        other = Store(tmp_path / "batched.db")
        other.init_schema()
        with other.transaction() as conn:
            for event in events:
                conn.execute(
                    "INSERT INTO change_log (event_id, op, identity, tier, payload, run_id,"
                    " context_id, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (event["event_id"], event["op"], event["identity"], event["tier"],
                     event["payload"], event["run_id"], event["context_id"],
                     event["recorded_at"]),
                )
        projector = GraphProjector(other)
        while projector.run_once(limit=1)["applied"]:
            pass
        assert other.graph_fingerprint() == expected


class TestMemoryScoping:
    """Code-enforced BU scoping, so the invariant survives the PostgreSQL move
    even if a schema grant is misconfigured (FR-12.6)."""

    def _write(self, store: Store, memory_id: str, bus: list[str], note: str) -> None:
        store.put_memory(memory_id=memory_id, kind="episodic", agent="resolver",
                         task_id=None, bus=bus, body={"note": note})

    def test_recall_through_the_tool_surface_is_scoped(self, loaded):
        store, _ = loaded
        self._write(store, "m_a", ["bu-a"], "bu-a evidence")
        self._write(store, "m_b", ["bu-b"], "bu-b evidence")
        self._write(store, "m_shared", ["commons"], "commons heuristic")

        tools = Tools(ToolContext(store, "resolver", ("bu-a", "commons"), "run_x", []))
        notes = {m["body"]["note"] for m in tools.recall()}
        assert notes == {"bu-a evidence", "commons heuristic"}

    def test_cross_bu_task_sees_both_but_only_when_scoped_to_both(self, loaded):
        store, _ = loaded
        self._write(store, "m_a", ["bu-a"], "bu-a evidence")
        self._write(store, "m_b", ["bu-b"], "bu-b evidence")

        narrow = Tools(ToolContext(store, "resolver", ("bu-b", "commons"), "run_y", []))
        assert {m["body"]["note"] for m in narrow.recall()} == {"bu-b evidence"}

        wide = Tools(ToolContext(store, "resolver", ("bu-a", "bu-b", "commons"), "run_z", []))
        assert {m["body"]["note"] for m in wide.recall()} == {"bu-a evidence", "bu-b evidence"}

    def test_a_memory_spanning_bus_needs_the_whole_scope(self, loaded):
        store, _ = loaded
        self._write(store, "m_cross", ["bu-a", "bu-b"], "cross-BU reconciliation")
        narrow = Tools(ToolContext(store, "resolver", ("bu-a", "commons"), "run_n", []))
        assert narrow.recall() == []

    def test_agent_writes_land_in_its_own_scope(self, loaded):
        store, _ = loaded
        tools = Tools(ToolContext(store, "resolver", ("bu-a", "commons"), "run_w", []))
        tools.remember({"note": "written in a bu-a task"})
        other = Tools(ToolContext(store, "resolver", ("bu-b", "commons"), "run_o", []))
        assert other.recall() == []

    def test_memory_is_not_reachable_as_enterprise_fact(self, loaded):
        """Agent beliefs must not be retrievable as graph content (FR-12.1)."""
        store, _ = loaded
        self._write(store, "m_secret", ["bu-a"], "the resolver believes ledger-api is stale")
        hits = store.bm25_search("resolver believes ledger-api stale", ["bu-a", "commons"])
        assert hits == []


class TestChunkRevivalGuard:
    """The bug: liveness sync ran in both directions, so a chunk retracted
    because its source became restricted came back on the next event."""

    def test_retraction_survives_later_events(self, loaded):
        store, built = loaded
        endpoint = built["anchors"]["bu-a"]["endpoint"]
        change = ChangeAPI(store)
        change.submit("kc-ingest", "chunk_retract", payload={"node_id": endpoint})
        GraphProjector(store).run_once()

        # Any later event touching the same node used to revive the chunk.
        change.submit("kc-maintenance", "quality_signal",
                      payload={"node_id": endpoint, "last_verified_at": "2026-09-18T00:00:00+00:00"})
        GraphProjector(store).run_once()

        conn = store.connect()
        try:
            rows = conn.execute(
                "SELECT live FROM chunks WHERE node_id=?", (endpoint,)
            ).fetchall()
        finally:
            conn.close()
        assert rows and all(row["live"] == 0 for row in rows)

    def test_a_re_added_chunk_is_live_again(self, loaded):
        store, built = loaded
        endpoint = built["anchors"]["bu-a"]["endpoint"]
        change = ChangeAPI(store)
        change.submit("kc-ingest", "chunk_retract", payload={"node_id": endpoint})
        GraphProjector(store).run_once()
        change.submit(
            "kc-ingest", "chunk_add",
            payload={"chunk_id": "k_readded", "node_id": endpoint, "bu_owner": "bu-a",
                     "kind": "openapi", "section_path": "GET /accounts/{id}/entries",
                     "content": "GET /accounts/{id}/entries. Restored after restriction lifted."},
        )
        GraphProjector(store).run_once()
        hits = store.bm25_search("restored after restriction lifted", ["bu-a"])
        assert [h["chunk_id"] for h in hits] == ["k_readded"]
