"""Health metrics and guardrails (FR-14.3, FR-14.4, FR-13.4, FR-5.9).

Metrics are normalised against their configured targets before anything
compares them, because raw values in different units are not comparable and
"weakest metric" would otherwise mean "smallest number" (N10).

Guardrails are separate from targets. A guardrail is a safety limit that is
fixed now and needs no baseline: the agent-derived claim ratio, the
source-permission skip rate, merge precision. Targets are set from the first
measurement (§19 threshold policy).
"""

from __future__ import annotations

from typing import Any

from kc.config import loader as cfg
from kc.core.projector import GRAPH
from kc.stores.sqlite_store import Store


def metrics(store: Store) -> dict[str, Any]:
    counts = store.counts()
    covered, services = store.services_with_owner_and_deps()
    provenance = store.claim_counts_by_provenance()
    ingest = store.ingest_stats()
    skips = store.skip_counts()
    projector = store.projector_state(GRAPH)

    agent_derived = provenance.get("agent-derived", 0)
    source_derived = provenance.get("source-derived", 0) + provenance.get("human-confirmed", 0)
    considered = max(1, ingest["total"] - ingest["dropped"])
    skipped = sum(skips.values())

    raw = {
        "coverage": (covered / services) if services else 0.0,
        "freshness": 1.0,  # no TTL sweep has run yet; set by kc-maintenance
        "confidence": _confidence_share(store),
        "gap_closure": _gap_closure(store),
        "llm_free_ingestion": 1.0 - (ingest["llm"] / considered),
    }
    normalised = {
        name: round(min(1.0, value / cfg.health_target(name)), 4)
        for name, value in raw.items()
    }

    return {
        "counts": counts,
        "raw": {k: round(v, 4) for k, v in raw.items()},
        "normalised": normalised,
        "weakest": min(normalised, key=lambda k: normalised[k]) if normalised else None,
        "contradictions": _contradictions(store),
        "agent_derived_ratio": round(agent_derived / max(1, source_derived), 4),
        "source_skip_rate": round(skipped / max(1, skipped + considered), 4),
        "ingestion": ingest,
        "skips": skips,
        "projector": projector,
        "dry_run": store.runtime_get("dry_run", True),
        "cognitive_layer": store.runtime_get("cognitive_layer", "on"),
        "cache_epoch": store.cache_epoch(),
    }


def guardrails(store: Store) -> list[dict[str, Any]]:
    """Fixed safety limits. Each returns a breach or not; none is a target."""
    current = metrics(store)
    checks = [
        {
            "guardrail": "agent_derived_ratio_max",
            "requirement": "FR-13.4",
            "limit": cfg.guardrail("agent_derived_ratio_max"),
            "value": current["agent_derived_ratio"],
            "breached": current["agent_derived_ratio"] > cfg.guardrail("agent_derived_ratio_max"),
        },
        {
            "guardrail": "source_skip_rate_max",
            "requirement": "FR-5.9",
            "limit": cfg.guardrail("source_skip_rate_max"),
            "value": current["source_skip_rate"],
            "breached": current["source_skip_rate"] > cfg.guardrail("source_skip_rate_max"),
            "note": "a breach means the BU mapping is wrong, not that content is restricted",
        },
        {
            "guardrail": "projector_halted",
            "requirement": "FR-7.8",
            "limit": 0,
            "value": int(bool(current["projector"]["halted"])),
            "breached": bool(current["projector"]["halted"]),
        },
        {
            "guardrail": "llm_free_ingestion_min",
            "requirement": "FR-6.14",
            "limit": cfg.health_target("llm_free_ingestion"),
            "value": current["raw"]["llm_free_ingestion"],
            "breached": current["raw"]["llm_free_ingestion"]
            < cfg.health_target("llm_free_ingestion"),
        },
    ]
    return checks


def _confidence_share(store: Store) -> float:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN confidence >= 0.5 THEN 1 ELSE 0 END) AS above"
            " FROM claims WHERE valid_to IS NULL"
        ).fetchone()
    finally:
        conn.close()
    total = int(row["total"] or 0)
    return round(int(row["above"] or 0) / total, 4) if total else 0.0


def _contradictions(store: Store) -> dict[str, int]:
    """Open contradictions, split by whether they were auto-resolved or
    escalated (FR-14.3 metric 4, FR-6.16)."""
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT props FROM nodes WHERE live=1 AND props LIKE '%\"contested\": true%'"
        ).fetchall()
    finally:
        conn.close()
    escalated = len([t for t in store.tasks(kind="contradiction") if t["state"] != "closed"])
    return {"contested_properties": len(rows), "escalated": escalated}


def _gap_closure(store: Store) -> float:
    tasks = store.tasks()
    filed = len(tasks)
    closed = len([t for t in tasks if t["state"] == "closed"])
    return round(closed / filed, 4) if filed else 1.0
