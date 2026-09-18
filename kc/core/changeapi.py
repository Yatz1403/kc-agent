"""The single write path (FR-7.1, FR-7.2, FR-7.3, FR-7.8).

Order is fixed and enforced here, not by convention:

    identity check  ->  tier gate  ->  schema validation  ->  log append
                                                          ->  claim write
                                                             (same transaction)

The log is the system of record, so nothing invalid may reach it: an invalid
event would break every future rebuild (FR-7.8). Projections are updated later
by projectors, never here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from kc.config import loader as cfg
from kc.core import ids
from kc.core.errors import (
    IdentityNotPermitted,
    SchemaViolation,
    StaleProposal,
    TierGateBlocked,
)
from kc.core.model import Claim, now, validate_claim
from kc.stores.sqlite_store import Store


@dataclass(frozen=True, slots=True)
class GateDecision:
    """What `gate.evaluate` decided (FR-7.14). Confidence is computed from
    evidence by the gate; a value reported by an agent is ignored."""

    confidence: float
    evidence: tuple[str, ...] = ()
    single_bu: bool = True
    proposal_id: str | None = None
    base_versions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Receipt:
    applied: bool
    tier: str
    seq: int | None = None
    claim_ids: tuple[str, ...] = ()
    task_id: str | None = None
    reason: str | None = None


class ChangeAPI:
    def __init__(self, store: Store) -> None:
        self.store = store

    # --- flags ---------------------------------------------------------------

    @property
    def dry_run(self) -> bool:
        return bool(self.store.runtime_get("dry_run", True))

    def set_dry_run(self, value: bool) -> None:
        self.store.runtime_set("dry_run", bool(value))

    @property
    def cognitive_layer_on(self) -> bool:
        return self.store.runtime_get("cognitive_layer", "on") == "on"

    def set_cognitive_layer(self, on: bool) -> None:
        """Kill switch. The fast path and serve path are unaffected: they do not
        consult this flag (FR-7.6, AT-18)."""
        self.store.runtime_set("cognitive_layer", "on" if on else "off")

    # --- the write path ------------------------------------------------------

    def submit(
        self,
        identity: str,
        op: str,
        *,
        claims: list[Claim] | tuple[Claim, ...] = (),
        closes: list[str] | tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
        cross_bu: bool = False,
        gate: GateDecision | None = None,
        run_id: str | None = None,
        context_id: str | None = None,
    ) -> Receipt:
        payload = dict(payload or {})

        # 1. identity (FR-7.2). Unknown identities and spokes reach no further.
        if not cfg.may(identity, op):
            raise IdentityNotPermitted(
                f"{identity} may not {op} (FR-7.2)"
            )

        # 2. tier gate (FR-7.3). Only gated identities are gated; the fast path
        #    and maintenance are not, which is what lets ingestion run under the
        #    kill switch and in dry-run (B1).
        tier = cfg.tier_for(op, cross_bu=cross_bu)
        if cfg.is_tier_gated(identity):
            blocked = self._tier_gate(identity, op, tier, gate, claims, payload, run_id, context_id)
            if blocked is not None:
                return blocked

        # 3. schema validation (FR-4.3, FR-4.9). Nothing off-schema is appended.
        for claim in claims:
            validate_claim(claim)
        self._validate_relationships(claims)
        self._validate_op_payload(op, payload, closes)

        # 4. log append and claim write, one transaction (FR-7.1).
        with self.store.transaction() as conn:
            if gate and gate.base_versions:
                self._check_base_versions(conn, gate.base_versions)
            seq = self.store.append_event(
                conn,
                op=op,
                identity=identity,
                tier=tier,
                payload=self._event_payload(claims, closes, payload),
                run_id=run_id,
                context_id=context_id,
            )
            for claim in claims:
                self.store.insert_claim(conn, claim, seq)
            when = payload.get("valid_to") or now()
            for claim_id in closes:
                self.store.close_claim(conn, claim_id, when)

        return Receipt(
            applied=True,
            tier=tier,
            seq=seq,
            claim_ids=tuple(c.claim_id for c in claims),
        )

    # --- gate ----------------------------------------------------------------

    def _tier_gate(
        self,
        identity: str,
        op: str,
        tier: str,
        gate: GateDecision | None,
        claims: Any,
        payload: dict[str, Any],
        run_id: str | None,
        context_id: str | None,
    ) -> Receipt | None:
        policy = cfg.tier_policy()
        if gate is None:
            raise TierGateBlocked(f"{identity} needs a gate decision for {op} (FR-7.14)")

        if tier in policy["human_always"]:
            task_id = self._queue_for_human(
                tier, op, gate, claims, payload, run_id, context_id,
                reason=f"{tier} requires human approval (FR-7.3)",
            )
            return Receipt(applied=False, tier=tier, task_id=task_id,
                           reason="awaiting human approval")

        if tier in policy["threshold_gated"]:
            threshold = float(policy["t3_confidence_threshold"])
            if not gate.single_bu or gate.confidence <= threshold:
                task_id = self._queue_for_human(
                    tier, op, gate, claims, payload, run_id, context_id,
                    reason=(
                        f"T3 below threshold ({gate.confidence:.2f} <= {threshold})"
                        if gate.single_bu
                        else "T3 spans business units"
                    ),
                )
                return Receipt(applied=False, tier=tier, task_id=task_id,
                               reason="awaiting human approval")

        if self.dry_run:
            task_id = self._queue_for_human(
                tier, op, gate, claims, payload, run_id, context_id,
                reason="dry-run: recorded for human application (FR-7.7)",
            )
            return Receipt(applied=False, tier=tier, task_id=task_id, reason="dry-run")
        return None

    def _queue_for_human(
        self,
        tier: str,
        op: str,
        gate: GateDecision,
        claims: Any,
        payload: dict[str, Any],
        run_id: str | None,
        context_id: str | None,
        *,
        reason: str,
    ) -> str:
        task_id = "t_" + ids.content_hash(
            f"{gate.proposal_id or ''}{op}{tier}{run_id or ''}{reason}"
        )[:16]
        scope = sorted({c.bu_owner for c in claims}) or payload.get("scope", [])
        self.store.put_task(
            task_id=task_id,
            kind="approval",
            state="input-required",
            scope=list(scope),
            payload={
                "op": op,
                "tier": tier,
                "reason": reason,
                "confidence": gate.confidence,
                "evidence": list(gate.evidence),
                "proposal_id": gate.proposal_id,
                "claims": [c.as_row() for c in claims],
                "closes": list(payload.get("closes", [])),
                "payload": payload,
                "base_versions": gate.base_versions,
            },
            reason=reason,
            context_id=context_id,
            run_id=run_id,
        )
        return task_id

    # --- validation helpers --------------------------------------------------

    def _validate_relationships(self, claims: Any) -> None:
        """Edge pairs are checked against the cardinality table (FR-4.9)."""
        pending_types = {
            c.subject_id: c.node_type for c in claims if c.kind == "identity" and c.node_type
        }
        for claim in claims:
            if claim.kind != "relationship":
                continue
            from_type = pending_types.get(str(claim.from_id)) or self._node_type(str(claim.from_id))
            to_type = pending_types.get(str(claim.to_id)) or self._node_type(str(claim.to_id))
            if from_type is None or to_type is None:
                raise SchemaViolation(
                    f"{claim.edge_type} references an unknown node "
                    f"({claim.from_id} -> {claim.to_id})"
                )
            if not cfg.edge_permitted(str(claim.edge_type), from_type, to_type):
                raise SchemaViolation(
                    f"{claim.edge_type} may not connect {from_type} to {to_type} (FR-4.9)"
                )

    def _node_type(self, node_id: str) -> str | None:
        node = self.store.get_node(node_id)
        return node.type if node else None

    def _validate_op_payload(self, op: str, payload: dict[str, Any], closes: Any) -> None:
        if op in {"close_claim", "supersede_claim_cross_source"} and not closes:
            raise SchemaViolation(f"{op} needs at least one claim to close")
        if op == "merge_nodes":
            for key in ("surviving_id", "absorbed_id"):
                if key not in payload:
                    raise SchemaViolation(f"merge_nodes needs {key}")
        if op in {"visibility_tighten", "visibility_loosen"}:
            for key in ("node_id", "visibility_class"):
                if key not in payload:
                    raise SchemaViolation(f"{op} needs {key}")
            if payload["visibility_class"] not in ("public", "topology", "restricted"):
                raise SchemaViolation("unknown visibility_class")
        if op == "source_key_mapping":
            for key in ("alias_key", "node_id"):
                if key not in payload:
                    raise SchemaViolation(f"source_key_mapping needs {key}")

    def _check_base_versions(self, conn: sqlite3.Connection, base: dict[str, int]) -> None:
        """A proposal applied later than it was built is stale, not applied
        (FR-7.7, AT-54)."""
        for node_id, version in base.items():
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM claims WHERE subject_id=?",
                (node_id,),
            ).fetchone()
            if int(row["seq"]) > int(version):
                raise StaleProposal(
                    f"{node_id} changed since the proposal was built "
                    f"(seq {row['seq']} > {version})"
                )

    @staticmethod
    def _event_payload(claims: Any, closes: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "claims": [c.as_row() for c in claims],
            "closes": list(closes),
            **payload,
        }
