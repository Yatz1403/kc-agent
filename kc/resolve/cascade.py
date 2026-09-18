"""The resolution cascade (FR-6.9, FR-6.10).

Four steps, stopping at the first confident result, and each step costs more
than the last:

    1. source-key mapping or alias exact match   (no cost)
    2. name similarity                            (no cost)
    3. structural fingerprint                     (no cost, graph reads)
    4. Resolver agent                             (cost: escalates)

Two rules that are easy to lose and expensive to recover:

* Candidates never leave the event's BU plus commons. An automatic cross-BU
  link is a false merge in effect (DR9).
* Below the threshold the fast path creates a new entity and queues a Resolver
  candidate; it does not merge and it does not block (FR-6.10, FR-6.17).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kc.config import loader as cfg
from kc.core import ids
from kc.stores.sqlite_store import Store, normalise_name

SIMILARITY_ACCEPT = 0.92
SIMILARITY_CANDIDATE = 0.55


class Step(StrEnum):
    SOURCE_KEY = "source_key"
    ALIAS = "alias"
    SIMILARITY = "similarity"
    FINGERPRINT = "fingerprint"
    ESCALATE = "escalate"
    NEW_ENTITY = "new_entity"


@dataclass(frozen=True, slots=True)
class Resolution:
    step: Step
    node_id: str | None
    candidates: tuple[dict[str, Any], ...] = ()
    evidence: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        return self.node_id is not None and self.step is not Step.NEW_ENTITY


def resolve_reference(
    store: Store,
    *,
    name: str,
    node_type: str | None,
    bu: str,
    source_key: str | None = None,
) -> Resolution:
    """Resolve an incoming reference to an existing node, or report candidates.

    Never creates anything: callers decide, so that the create path stays with
    the ingest identity (FR-7.2).
    """
    bus = sorted({bu, cfg.commons()})

    # 1. source-key mapping, then alias exact match.
    if source_key:
        hit = _alias_hit(store, source_key, bus, kind="source_key")
        if hit:
            return Resolution(Step.SOURCE_KEY, hit["id"], evidence={"source_key": source_key})
    alias_hit = _alias_hit(store, normalise_name(name), bus, kind="name")
    if alias_hit:
        return Resolution(Step.ALIAS, alias_hit["id"], evidence={"alias": normalise_name(name)})

    # 2. name similarity, in-BU only.
    candidates = store.similar_nodes(name, node_type, bus)
    if candidates and candidates[0]["similarity"] >= SIMILARITY_ACCEPT:
        best = candidates[0]
        return Resolution(
            Step.SIMILARITY, best["id"], candidates=tuple(candidates[:5]),
            evidence={"similarity": best["similarity"]},
        )

    # 3. structural fingerprint over the plausible candidates.
    plausible = [c for c in candidates if c["similarity"] >= SIMILARITY_CANDIDATE]
    if plausible:
        from kc.resolve.fingerprint import fingerprint

        scored = []
        for candidate in plausible[:5]:
            other = fingerprint(store, candidate["id"])
            if other is None:
                continue
            features = {
                "name_overlap": candidate["similarity"],
                "shape_overlap": 0.0 if not other.edge_shape else 0.5,
                "independent_sources": len(other.source_systems),
                "same_type": node_type is None or other.node_type == node_type,
                "same_bu": other.bu == bu,
            }
            features["fingerprint_score"] = round(
                (features["name_overlap"] + features["shape_overlap"]) / 2, 4
            )
            scored.append((candidate, features))
        if scored:
            scored.sort(key=lambda pair: -pair[1]["fingerprint_score"])
            candidate, features = scored[0]
            return Resolution(
                Step.FINGERPRINT,
                None,
                candidates=tuple(c for c, _ in scored),
                evidence=features,
            )

    # 4. nothing confident: the caller creates a new entity and queues a
    #    candidate for the Resolver (FR-6.10, FR-6.15).
    return Resolution(Step.NEW_ENTITY, None, candidates=tuple(candidates[:5]))


def _alias_hit(store: Store, alias_key: str, bus: list[str], kind: str) -> dict[str, Any] | None:
    marks = ",".join("?" for _ in bus)
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT a.node_id AS id FROM aliases a JOIN nodes n ON n.id=a.node_id"
            f" WHERE a.alias_key=? AND a.kind=? AND n.live=1 AND n.bu_owner IN ({marks})"
            " LIMIT 1",
            (alias_key, kind, *bus),
        ).fetchone()
    finally:
        conn.close()
    return {"id": row["id"]} if row else None


def mention_candidate(
    *, name: str, node_type: str | None, bu: str, source_key: str, span: str
) -> dict[str, Any]:
    """An unresolved prose mention. Queued for the Resolver, never turned into
    a node: that is how one service becomes five (FR-6.15)."""
    return {
        "candidate_id": "mc_" + ids.content_hash(f"{source_key}{name}{span}")[:16],
        "name": name,
        "proposed_type": node_type,
        "bu": bu,
        "source_key": source_key,
        "span": span,
    }
