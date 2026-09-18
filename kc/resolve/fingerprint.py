"""Structural fingerprints for entity resolution (FR-6.9 step 2, FR-8.8).

A fingerprint is what an entity looks like in the graph, independent of its
name: its type, its edge shape, its source systems. Two nodes with the same
name and incompatible shapes are usually different things; two nodes with
different names and the same shape are worth a Resolver look.

Pure functions over data the caller already holds, so the Resolver can compare
candidates without a model and without a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kc.stores.sqlite_store import Store, normalise_name


@dataclass(frozen=True, slots=True)
class Fingerprint:
    node_id: str
    node_type: str
    name_tokens: frozenset[str]
    edge_shape: frozenset[tuple[str, str]]
    source_systems: frozenset[str]
    bu: str

    def compare(self, other: Fingerprint) -> dict[str, Any]:
        """Evidence features, not a verdict. The gate turns these into
        confidence (FR-7.14); the agent never reports a number of its own."""
        name_overlap = _jaccard(self.name_tokens, other.name_tokens)
        shape_overlap = _jaccard(self.edge_shape, other.edge_shape)
        shared_sources = self.source_systems & other.source_systems
        return {
            "same_type": self.node_type == other.node_type,
            "same_bu": self.bu == other.bu,
            "name_overlap": name_overlap,
            "shape_overlap": shape_overlap,
            "independent_sources": len(self.source_systems | other.source_systems),
            "shared_source_systems": sorted(shared_sources),
            "fingerprint_score": round((name_overlap + shape_overlap) / 2, 4),
        }


def _jaccard(left: frozenset, right: frozenset) -> float:
    if not left or not right:
        return 0.0
    return round(len(left & right) / len(left | right), 4)


def fingerprint(store: Store, node_id: str) -> Fingerprint | None:
    node = store.get_node(node_id)
    if node is None:
        return None
    name = node.value("name") or ""
    edges = store.edges_between([node_id]) or []
    shape: set[tuple[str, str]] = set()
    sources: set[str] = set()
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT edge_type, from_id, to_id, source_systems FROM edges"
            " WHERE valid_to IS NULL AND (from_id=? OR to_id=?)",
            (node_id, node_id),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        direction = "out" if row["from_id"] == node_id else "in"
        shape.add((row["edge_type"], direction))
        import json as _json

        sources.update(_json.loads(row["source_systems"]))
    for claim in store.open_claims(node_id, kind="identity"):
        sources.add(claim.source_system)
    return Fingerprint(
        node_id=node_id,
        node_type=node.type,
        name_tokens=frozenset(normalise_name(str(name)).split("-")) if name else frozenset(),
        edge_shape=frozenset(shape | {(e["edge_type"], "out") for e in edges}),
        source_systems=frozenset(sources),
        bu=node.bu_owner,
    )
