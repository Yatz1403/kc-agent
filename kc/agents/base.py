"""The generic agent contract (FR-8.1 to FR-8.4, NFR-7).

Every agent is a standing goal plus a bounded loop with three terminal states.
`escalate` and `no_action` are successes, not failures: an agent that hands a
hard case to a human has done its job (FR-8.1).

The loop is enforced here rather than trusted to each agent: budgets are
checked before every step, and exhaustion terminates with `escalate` plus
partial evidence rather than one more attempt (FR-8.4, AT-9).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from kc.agents import llm as llm_seam
from kc.core import ids
from kc.core.errors import BudgetExhausted
from kc.serve import okf
from kc.stores.sqlite_store import Store

PROPOSAL = "proposal"
NO_ACTION = "no_action_needed"
ESCALATE = "escalate"


@dataclass(slots=True)
class Budget:
    """Hard caps. Wall clock is included because a stuck tool call is the most
    common way a loop overruns (NFR-7)."""

    tool_calls: int = 40
    tokens: int = 60_000
    wall_clock_s: float = 120.0
    started_at: float = field(default_factory=time.monotonic)
    used_tool_calls: int = 0
    used_tokens: int = 0

    def check(self) -> None:
        if self.used_tool_calls >= self.tool_calls:
            raise BudgetExhausted("tool call budget exhausted")
        if self.used_tokens >= self.tokens:
            raise BudgetExhausted("token budget exhausted")
        if time.monotonic() - self.started_at >= self.wall_clock_s:
            raise BudgetExhausted("wall clock budget exhausted")

    def spend(self, tool_calls: int = 1, tokens: int = 0) -> None:
        self.used_tool_calls += tool_calls
        self.used_tokens += tokens

    def remaining(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls - self.used_tool_calls,
            "tokens": self.tokens - self.used_tokens,
            "wall_clock_s": round(
                max(0.0, self.wall_clock_s - (time.monotonic() - self.started_at)), 2
            ),
        }


@dataclass(slots=True)
class Outcome:
    """What a run produced. `message` is an OKF proposal, escalation or
    no_action (FR-9.13), so the Curator handles every agent the same way."""

    terminal_state: str
    message: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    """Base class. Subclasses implement `step`, declare their goal, tool surface
    and write scope, and never hold a write tool (FR-8.2)."""

    name = "agent"
    goal = ""
    tools: tuple[str, ...] = ()
    write_scope = "none: proposals only (FR-8.2)"
    model_tier = "small"

    def __init__(self, store: Store, *, client: llm_seam.LLMClient | None = None) -> None:
        self.store = store
        self.client = llm_seam.get_client(client)

    # --- loop ----------------------------------------------------------------

    def run(self, task: dict[str, Any], *, budget: Budget | None = None) -> Outcome:
        budget = budget or Budget(**task.get("budget", {}))
        run_id = task.get("run_id") or "run_" + ids.content_hash(str(task))[:12]
        scope = list(task.get("task_scope", []))
        trace: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []

        recalled = self.store.recall_memory(agent=self.name, task_scope=scope, kind="episodic")
        trace.append({"step": "episodic_recall", "count": len(recalled)})

        try:
            outcome = self.step(task, budget, recalled, trace, evidence)
        except BudgetExhausted as exc:
            outcome = Outcome(
                terminal_state=ESCALATE,
                message=okf.escalation(
                    run_id=run_id,
                    agent=self.name,
                    reason="BUDGET_EXHAUSTED",
                    subjects=task.get("subjects", []),
                    partial_evidence=evidence,
                ),
                evidence=evidence,
                trace=trace + [{"step": "budget", "error": str(exc)}],
            )

        self.store.put_memory(
            memory_id="m_" + ids.content_hash(f"{run_id}{self.name}{outcome.terminal_state}")[:16],
            kind="episodic",
            agent=self.name,
            task_id=task.get("task_id"),
            bus=scope,
            body={
                "goal": self.goal,
                "terminal_state": outcome.terminal_state,
                "subjects": task.get("subjects", []),
                "evidence": outcome.evidence,
                "trace": outcome.trace,
                "budget_remaining": budget.remaining(),
            },
        )
        return outcome

    # --- to implement --------------------------------------------------------

    def step(
        self,
        task: dict[str, Any],
        budget: Budget,
        recalled: list[dict[str, Any]],
        trace: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> Outcome:
        raise NotImplementedError

    # --- helpers -------------------------------------------------------------

    def escalate(
        self, task: dict[str, Any], reason: str, evidence: list[dict[str, Any]],
        trace: list[dict[str, Any]],
    ) -> Outcome:
        return Outcome(
            terminal_state=ESCALATE,
            message=okf.escalation(
                run_id=task.get("run_id", "run_unknown"),
                agent=self.name,
                reason=reason,
                subjects=task.get("subjects", []),
                partial_evidence=evidence,
            ),
            evidence=evidence,
            trace=trace,
        )

    def nothing_to_do(
        self, task: dict[str, Any], reason: str, trace: list[dict[str, Any]]
    ) -> Outcome:
        return Outcome(
            terminal_state=NO_ACTION,
            message=okf.no_action(
                run_id=task.get("run_id", "run_unknown"),
                agent=self.name,
                reason=reason,
                subjects=task.get("subjects", []),
            ),
            trace=trace,
        )

    def declaration(self) -> dict[str, Any]:
        """What this agent is allowed to be. Printed by `kc.cli health` so the
        surface is reviewable without reading the code."""
        return {
            "agent": self.name,
            "goal": self.goal,
            "tools": list(self.tools),
            "write_scope": self.write_scope,
            "model_tier": self.model_tier,
            "has_llm_client": self.client is not None,
        }
