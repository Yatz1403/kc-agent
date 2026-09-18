"""The gate: deterministic code, no model (FR-7.14, FR-13.1).

Two jobs, and both are refusals by default:

1. Compute the confidence the tier gate uses, from the proposal's cited
   evidence. A confidence an agent reported is ignored, because source text an
   agent read is attacker-controllable (M14).
2. Enforce the inbreeding guard here rather than in a read tool. v0.1 put it in
   "the evidence-fetch tool", which does not exist, and `graph.query` is a base
   tool every agent holds (FR-8.3, AT-10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kc.core.changeapi import GateDecision
from kc.core.errors import InbreedingBlocked
from kc.stores.sqlite_store import Store

# Evidence features and their weights. Deterministic, inspectable, and flat on
# purpose: a curve nobody can explain is a curve nobody will trust at review.
WEIGHTS = {
    "fingerprint_score": 0.45,
    "independent_sources": 0.25,
    "authority": 0.20,
    "same_type": 0.10,
}
MAX_AUTHORITY = 60.0


@dataclass(slots=True)
class GateResult:
    accepted: bool
    tier_input: GateDecision
    reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)


class Gate:
    def __init__(self, store: Store) -> None:
        self.store = store

    # --- evidence eligibility ------------------------------------------------

    def check_evidence(self, proposal: dict[str, Any]) -> None:
        """Agent-derived evidence may not support an agent-derived claim.

        Evaluated per claim, not per node: a node carrying both agent- and
        source-derived claims can still be supported by its source-derived ones
        (FR-13.1 item 4).
        """
        if not proposal.get("evidence"):
            raise InbreedingBlocked("proposal cites no evidence (FR-9.13)")

        produces_agent_derived = any(
            change.get("provenance_class", "agent-derived") == "agent-derived"
            for change in proposal.get("changes", [])
        ) or not proposal.get("changes")

        if not produces_agent_derived:
            return

        for item in proposal["evidence"]:
            claim_id = item.get("claim_id")
            if not claim_id:
                continue  # chunk evidence is source content by construction
            claim = self.store.get_claim(claim_id)
            if claim is None:
                raise InbreedingBlocked(f"evidence claim {claim_id} does not exist")
            if claim.provenance_class == "agent-derived":
                raise InbreedingBlocked(
                    f"agent-derived evidence {claim_id} cannot support an agent-derived "
                    "claim (FR-13.1, AT-10)"
                )

    # --- confidence ----------------------------------------------------------

    def evaluate(
        self,
        proposal: dict[str, Any],
        *,
        features: dict[str, Any] | None = None,
        base_versions: dict[str, int] | None = None,
    ) -> GateResult:
        self.check_evidence(proposal)
        features = dict(features or {})
        features.setdefault("independent_sources", self._independent_sources(proposal))
        features.setdefault("authority", self._max_authority(proposal))
        features.setdefault("fingerprint_score", 0.0)
        features.setdefault("same_type", True)

        score = (
            WEIGHTS["fingerprint_score"] * _clamp(float(features["fingerprint_score"]))
            + WEIGHTS["independent_sources"]
            * _clamp((float(features["independent_sources"]) - 1.0) / 2.0)
            + WEIGHTS["authority"] * _clamp(float(features["authority"]) / MAX_AUTHORITY)
            + WEIGHTS["same_type"] * (1.0 if features["same_type"] else 0.0)
        )
        confidence = round(min(0.99, score), 4)

        subjects = [s.get("id") for s in proposal.get("subjects", []) if s.get("id")]
        bus = {
            node.bu_owner
            for node in self.store.nodes_by_ids([s for s in subjects if s]).values()
        }
        decision = GateDecision(
            confidence=confidence,
            evidence=tuple(
                str(item.get("claim_id") or item.get("chunk_id"))
                for item in proposal.get("evidence", [])
            ),
            single_bu=len(bus) <= 1,
            proposal_id=proposal.get("proposal_id"),
            base_versions=dict(base_versions or {}),
        )
        reasons = []
        if not decision.single_bu:
            reasons.append("cross-BU: T4 regardless of confidence (FR-7.3)")
        return GateResult(accepted=True, tier_input=decision, reasons=reasons, features=features)

    # --- helpers -------------------------------------------------------------

    def _independent_sources(self, proposal: dict[str, Any]) -> int:
        systems: set[str] = set()
        for item in proposal.get("evidence", []):
            claim_id = item.get("claim_id")
            if not claim_id:
                continue
            claim = self.store.get_claim(claim_id)
            if claim is not None:
                systems.add(claim.source_system)
        return max(1, len(systems))

    def _max_authority(self, proposal: dict[str, Any]) -> int:
        best = 0
        for item in proposal.get("evidence", []):
            claim_id = item.get("claim_id")
            if not claim_id:
                continue
            claim = self.store.get_claim(claim_id)
            if claim is not None:
                best = max(best, claim.authority())
        return best


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
