"""Curator: orchestration, budget allocation and the only write authority
among the agents (FR-8.7, FR-7.2).

Two things are deterministic here by requirement, not by preference:

* **Triage routing** is a lookup table, not a model call (FR-8.7). A task kind
  maps to exactly one spoke.
* **Budget allocation** weights the weakest health metric, each normalised
  against its configured target, because raw metric values are not comparable
  (FR-14.4, N10).

The Curator applies what the gate accepted, through the Change API as
`kc-curator`. Under dry-run — the MVP default — the Change API records the
proposal for a human instead of applying it, and that is a normal outcome
rather than a failure (FR-7.7).
"""

from __future__ import annotations

from typing import Any

from kc.agents import stubs
from kc.agents.base import ESCALATE, NO_ACTION, PROPOSAL, Budget
from kc.agents.gate import Gate
from kc.agents.resolver import Resolver
from kc.core import ids
from kc.core.changeapi import ChangeAPI
from kc.core.errors import InbreedingBlocked, WriteRejected
from kc.core.model import Claim, now
from kc.stores.sqlite_store import Store

# Deterministic triage: task kind -> spoke. Adding a row is a config decision,
# not a judgement call at runtime (FR-8.7).
ROUTING: dict[str, str] = {
    "resolve": "resolver",
    "mention_candidate": "resolver",
    "merge_candidate": "resolver",
    "contradiction": "reconciler",
    "staleness": "verifier",
    "gap": "prospector",
    "bad_answer": "retrieval-critic",
    "extraction_failure": "schema-steward",
}

AGENTS = {
    "resolver": Resolver,
    "reconciler": stubs.Reconciler,
    "verifier": stubs.Verifier,
    "prospector": stubs.Prospector,
    "retrieval-critic": stubs.RetrievalCritic,
    "schema-steward": stubs.SchemaSteward,
}

# Which health metric each spoke moves. Used to weight the daily budget.
METRIC_OWNER = {
    "resolver": "coverage",
    "reconciler": "confidence",
    "verifier": "freshness",
    "prospector": "gap_closure",
    "retrieval-critic": "coverage",
    "schema-steward": "llm_free_ingestion",
}


class Curator:
    def __init__(self, store: Store, *, client: Any | None = None) -> None:
        self.store = store
        self.change = ChangeAPI(store)
        self.gate = Gate(store)
        self.client = client

    # --- routing and allocation ---------------------------------------------

    @staticmethod
    def route(task_kind: str) -> str | None:
        return ROUTING.get(task_kind)

    def allocate(self, total_tool_calls: int = 120) -> dict[str, int]:
        """Weight the daily budget toward the weakest normalised metric."""
        from kc.health import metrics

        current = metrics(self.store)
        weights: dict[str, float] = {}
        for agent, metric in METRIC_OWNER.items():
            normalised = current["normalised"].get(metric, 1.0)
            weights[agent] = max(0.05, 1.0 - float(normalised))
        total_weight = sum(weights.values()) or 1.0
        return {
            agent: max(1, int(round(total_tool_calls * weight / total_weight)))
            for agent, weight in weights.items()
        }

    # --- the work loop -------------------------------------------------------

    def work(self, limit: int = 10) -> list[dict[str, Any]]:
        """Dispatch open tasks to their spoke and handle each outcome.

        Respects the kill switch: with the cognitive layer off, nothing is
        dispatched and the fast path and serve path are untouched (FR-7.6).
        """
        if not self.change.cognitive_layer_on:
            return [{"skipped": "cognitive layer off (FR-7.6)"}]

        allocation = self.allocate()
        results: list[dict[str, Any]] = []
        for task in self.store.tasks(state="open")[:limit]:
            agent_name = self.route(task["kind"]) or self.route(
                task["kind"].split("-")[0]
            )
            if agent_name is None:
                results.append({"task_id": task["task_id"], "routed": None})
                continue
            agent_cls = AGENTS[agent_name]
            agent = agent_cls(self.store, client=self.client)
            run_id = "run_" + ids.content_hash(f"{task['task_id']}{now()}")[:12]
            spoke_task = {
                "task_id": task["task_id"],
                "run_id": run_id,
                "task_scope": task["scope"],
                "subjects": task["payload"].get("subjects", []),
                "candidates": task["payload"].get("candidates", []),
                "budget": {"tool_calls": allocation.get(agent_name, 10)},
            }
            outcome = agent.run(spoke_task, budget=Budget(tool_calls=allocation.get(agent_name, 10)))
            results.append(self._handle(task, agent_name, outcome))
        return results

    def _handle(self, task: dict[str, Any], agent_name: str, outcome: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "task_id": task["task_id"],
            "agent": agent_name,
            "terminal_state": outcome.terminal_state,
        }
        if outcome.terminal_state == NO_ACTION:
            self.store.put_task(
                task_id=task["task_id"], kind=task["kind"], state="closed",
                scope=task["scope"], payload=task["payload"],
                reason=outcome.message.get("reason"),
            )
            return record

        if outcome.terminal_state == ESCALATE:
            self.store.put_task(
                task_id=task["task_id"], kind=task["kind"], state="escalated",
                scope=task["scope"], payload={**task["payload"], "escalation": outcome.message},
                reason=outcome.message.get("reason"),
            )
            record["reason"] = outcome.message.get("reason")
            return record

        if outcome.terminal_state == PROPOSAL:
            return {**record, **self.apply_proposal(outcome.message, task)}
        return record

    # --- applying proposals --------------------------------------------------

    def apply_proposal(
        self, proposal: dict[str, Any], task: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Gate, then write. The gate computes confidence and enforces the
        inbreeding guard; the Change API enforces tier and dry-run."""
        try:
            gate_result = self.gate.evaluate(proposal)
        except InbreedingBlocked as exc:
            return {"applied": False, "rejected": "inbreeding", "detail": str(exc)}

        changes = proposal.get("changes", [])
        op = changes[0]["op"] if changes else "add_claim"
        payload = {k: v for k, v in (changes[0] if changes else {}).items() if k != "op"}
        claims: list[Claim] = []
        if op == "add_claim":
            claims = [Claim(**payload["claim"])] if "claim" in payload else []

        try:
            receipt = self.change.submit(
                "kc-curator",
                op,
                claims=claims,
                payload=payload,
                gate=gate_result.tier_input,
                cross_bu=not gate_result.tier_input.single_bu,
                run_id=proposal.get("run_id"),
                context_id=(task or {}).get("context_id"),
            )
        except WriteRejected as exc:
            return {"applied": False, "rejected": type(exc).__name__, "detail": str(exc)}

        return {
            "applied": receipt.applied,
            "tier": receipt.tier,
            "confidence": gate_result.tier_input.confidence,
            "task_id": receipt.task_id,
            "reason": receipt.reason,
        }

    # --- declarations --------------------------------------------------------

    def declarations(self) -> list[dict[str, Any]]:
        return [AGENTS[name](self.store, client=self.client).declaration() for name in AGENTS]
