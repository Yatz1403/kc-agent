"""Projector framework and the graph projection (FR-4.7, FR-7.8 to FR-7.11).

Properties of this module that the spec turns on:

* sequence numbers and a per-projector high-water mark, so replaying an applied
  range is a no-op (FR-7.9, AT-30);
* on failure the projector halts and dead-letters the event; it never skips it,
  because a skipped event diverges silently (FR-7.8, AT-27);
* the cache epoch bumps when an event is *applied*, not when it is appended
  (FR-11.6);
* rebuild() reconstructs every projection from the log alone (NFR-5, AT-28).

Property folding lives here: the graph holds the value of the highest-authority
open claim, with a contested flag when more than one open claim disagrees
(FR-4.7, FR-6.16).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from kc.config import loader as cfg
from kc.core.errors import ProjectorHalted
from kc.core.model import Claim, Node, now
from kc.stores.sqlite_store import Store, normalise_name

GRAPH = "graph"


class GraphProjector:
    """Projects the change log into nodes, edges, aliases and chunks."""

    name = GRAPH

    def __init__(self, store: Store) -> None:
        self.store = store

    # --- event dispatch ------------------------------------------------------

    def apply(self, conn: sqlite3.Connection, event: sqlite3.Row) -> None:
        payload = json.loads(event["payload"])
        op = event["op"]
        claims = [Claim.from_row(row) for row in payload.get("claims", [])]

        subjects: set[str] = set()
        edges: set[str] = set()
        for claim in claims:
            if claim.kind == "relationship":
                edges.add(claim.subject_id)
            else:
                subjects.add(claim.subject_id)

        for claim_id in payload.get("closes", []):
            closed = self.store.get_claim(claim_id)
            if closed is None:
                continue
            if closed.kind == "relationship":
                edges.add(closed.subject_id)
            else:
                subjects.add(closed.subject_id)

        for node_id in subjects:
            self._fold_node(conn, node_id)
        for edge_id in edges:
            self.store.fold_edge(conn, edge_id)

        if op == "source_key_mapping":
            self.store.put_alias(
                conn,
                alias_key=payload["alias_key"],
                node_id=payload["node_id"],
                bu_owner=payload.get("bu_owner", ""),
                visibility_class=payload.get("visibility_class", "restricted"),
                kind="source_key",
            )
        elif op == "alias_rule":
            self.store.put_alias(
                conn,
                alias_key=normalise_name(payload["alias_key"]),
                node_id=payload["node_id"],
                bu_owner=payload.get("bu_owner", ""),
                visibility_class=payload.get("visibility_class", "restricted"),
                kind="name",
            )
        elif op in {"visibility_tighten", "visibility_loosen"}:
            self.store.set_node_visibility(conn, payload["node_id"], payload["visibility_class"])
            self._refold_edges_of(conn, payload["node_id"])
        elif op == "chunk_add":
            self.store.put_chunk(
                conn,
                chunk_id=payload["chunk_id"],
                node_id=payload["node_id"],
                bu_owner=payload["bu_owner"],
                kind=payload["kind"],
                section_path=payload.get("section_path", ""),
                content=payload["content"],
            )
        elif op == "chunk_retract":
            self.store.retract_chunks_for_node(conn, payload["node_id"])
        elif op == "merge_nodes":
            self._merge(conn, payload["surviving_id"], payload["absorbed_id"])

        # A closed identity claim can end a node's life; chunk liveness follows
        # node liveness so an index can never outlive its node (FR-11.10).
        if subjects or op in {"merge_nodes", "chunk_retract"}:
            self.store.set_chunk_liveness_from_nodes(conn)

    # --- folding -------------------------------------------------------------

    def _fold_node(self, conn: sqlite3.Connection, node_id: str) -> None:
        """Rebuild one node from its open claims (FR-4.7)."""
        identity_claims = self.store.open_claims(node_id, kind="identity", conn=conn)
        all_identity = [
            Claim.from_row(r)
            for r in conn.execute(
                "SELECT * FROM claims WHERE subject_id=? AND kind='identity'", (node_id,)
            ).fetchall()
        ]
        if not all_identity:
            return
        template = max(
            identity_claims or all_identity, key=lambda c: (c.authority(), c.valid_from)
        )
        node_type = str(template.node_type)
        existing = self.store.get_node(node_id, conn)
        visibility = (
            existing.visibility_class
            if existing
            else cfg.default_visibility(node_type, template.subtype)
        )

        props: dict[str, Any] = {}
        prop_claims = [
            Claim.from_row(r)
            for r in conn.execute(
                "SELECT * FROM claims WHERE subject_id=? AND kind='property'"
                " AND valid_to IS NULL",
                (node_id,),
            ).fetchall()
        ]
        by_prop: dict[str, list[Claim]] = {}
        for claim in prop_claims:
            by_prop.setdefault(str(claim.prop), []).append(claim)

        for prop, group in by_prop.items():
            entries = [self._prop_entry(c) for c in group]
            if cfg.is_single_valued(node_type, prop):
                winner = max(group, key=lambda c: (c.authority(), c.valid_from, c.claim_id))
                entry = self._prop_entry(winner)
                distinct = {json.dumps(c.value, sort_keys=True) for c in group}
                entry["contested"] = len(distinct) > 1
                if entry["contested"]:
                    entry["other_claim_ids"] = sorted(
                        c.claim_id for c in group if c.claim_id != winner.claim_id
                    )
                props[prop] = entry
            else:
                props[prop] = sorted(entries, key=lambda e: json.dumps(e["value"], sort_keys=True))

        node = Node(
            id=node_id,
            type=node_type,
            subtype=template.subtype,
            bu_owner=template.bu_owner,
            visibility_class=visibility,
            live=bool(identity_claims),
            source_key=template.source_key,
            props=props,
        )
        self.store.put_node(conn, node)

        name = node.value("name")
        if name:
            self.store.put_alias(
                conn,
                alias_key=normalise_name(str(name)),
                node_id=node_id,
                bu_owner=node.bu_owner,
                visibility_class=node.visibility_class,
                kind="name",
            )
        if not node.live:
            self.store.retract_chunks_for_node(conn, node_id)

    @staticmethod
    def _prop_entry(claim: Claim) -> dict[str, Any]:
        return {
            "value": claim.value,
            "claim_id": claim.claim_id,
            "source_system": claim.source_system,
            "source_id": claim.source_id,
            "provenance_class": claim.provenance_class,
            "extraction_method": claim.extraction_method,
            "confidence": claim.confidence,
            "valid_from": claim.valid_from,
            "contested": False,
        }

    def _refold_edges_of(self, conn: sqlite3.Connection, node_id: str) -> None:
        rows = conn.execute(
            "SELECT id FROM edges WHERE from_id=? OR to_id=?", (node_id, node_id)
        ).fetchall()
        for row in rows:
            self.store.fold_edge(conn, row["id"])

    def _merge(self, conn: sqlite3.Connection, surviving: str, absorbed: str) -> None:
        """A merge carries its redirect in the same event, so the two cannot
        diverge (FR-4.11)."""
        node = self.store.get_node(absorbed, conn)
        if node is not None:
            self.store.set_node_live(conn, absorbed, False)
            self.store.put_alias(
                conn,
                alias_key=absorbed,
                node_id=surviving,
                bu_owner=node.bu_owner,
                visibility_class=node.visibility_class,
                kind="redirect",
            )
        self._fold_node(conn, surviving)

    # --- runner --------------------------------------------------------------

    def run_once(self, limit: int = 500) -> dict[str, Any]:
        """Apply pending events. Halts on the first failure (FR-7.8)."""
        state = self.store.projector_state(self.name)
        if state["halted"]:
            return {"applied": 0, "halted": True, "high_water": state["high_water"],
                    "error": state["halt_error"]}
        applied = 0
        events = self.store.read_events(after_seq=state["high_water"], limit=limit)
        for event in events:
            seq = int(event["seq"])
            try:
                with self.store.transaction() as conn:
                    self.apply(conn, event)
                    self.store.set_high_water(conn, self.name, seq)
                    self.store.bump_cache_epoch(conn)
            except Exception as exc:  # noqa: BLE001 - halt on anything, never skip
                self.store.halt_projector(self.name, seq, f"{type(exc).__name__}: {exc}")
                return {"applied": applied, "halted": True, "high_water": seq - 1,
                        "error": str(exc)}
            applied += 1
        return {"applied": applied, "halted": False,
                "high_water": self.store.projector_state(self.name)["high_water"]}

    def rebuild(self) -> dict[str, Any]:
        """Rebuild from the log alone: the raw store is needed only to
        re-extract under a new extraction_version (NFR-5, AT-28)."""
        with self.store.transaction() as conn:
            self.store.clear_projection(conn)
            self.store.set_high_water(conn, self.name, 0)
            conn.execute(
                "UPDATE projector_state SET halted=0, halt_seq=NULL, halt_error=NULL,"
                " updated_at=? WHERE projector=?",
                (now(), self.name),
            )
        total = 0
        while True:
            result = self.run_once()
            total += int(result["applied"])
            if result["halted"]:
                raise ProjectorHalted(f"rebuild halted at {result['high_water']}: {result.get('error')}")
            if not result["applied"]:
                break
        return {"applied": total, "fingerprint": self.store.graph_fingerprint()}


def run_projectors(store: Store, limit: int = 500) -> dict[str, Any]:
    """One place to add the claim-store, index and alias projectors when they
    stop sharing the graph projector's transaction."""
    return {GRAPH: GraphProjector(store).run_once(limit=limit)}
