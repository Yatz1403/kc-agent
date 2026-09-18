"""The five deferred spokes, declared rather than pretended (§17).

Each one carries its real goal, tool surface, write scope and model tier, and a
loop that terminates as `escalate` with reason `STUBBED`. They are wired into
the Curator and the human queue from day one on purpose: the queue is the
instrument that tells you which hard cases actually occur, before anyone builds
an agent for the wrong ones.

A stub that returned "no action" would be worse than nothing, because a clean
health metric would hide the work. Escalating makes the backlog visible.
"""

from __future__ import annotations

from typing import Any

from kc.agents.base import Agent, Budget, Outcome


class _Stub(Agent):
    stub_note = "declared, not implemented"

    def step(
        self,
        task: dict[str, Any],
        budget: Budget,
        recalled: list[dict[str, Any]],
        trace: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> Outcome:
        budget.check()
        trace.append({"step": "stub", "agent": self.name, "note": self.stub_note})
        evidence.extend({"subject": s.get("id")} for s in task.get("subjects", []))
        tools = Tools(  # noqa: F821 - imported lazily below to keep the surface honest
            ToolContext(self.store, self.name, tuple(task.get("task_scope", [])),
                        task.get("run_id", "run_unknown"), trace)
        )
        tools.create_task(
            kind=f"{self.name}-backlog",
            payload={"task": task, "declared_tools": list(self.tools)},
            reason=f"{self.name} is declared but not implemented (§17)",
        )
        return self.escalate(task, "STUBBED", evidence, trace)


from kc.agents.tools import ToolContext, Tools  # noqa: E402  (used by _Stub.step)


class Reconciler(_Stub):
    name = "reconciler"
    goal = "one coherent truth per claim"
    tools = (
        "claim.fetch", "source.current_state", "authority.rank", "graph.temporal_query",
        "owner.lookup", "human.ask_owner", "supersede.propose", "coexist.propose",
    )
    write_scope = "none: supersede and coexist proposals only"
    model_tier = "large"
    stub_note = (
        "equal-rank contradictions only reach here; different-rank ones are resolved "
        "deterministically by the fast path (FR-6.16)"
    )


class Verifier(_Stub):
    name = "verifier"
    goal = "graph trustworthiness"
    tools = (
        "staleness.list", "retrieval.traffic", "blast_radius", "source.fetch",
        "hash.compare", "verification.propose",
    )
    write_scope = "none: T1 verification proposals only (renamed from verification.record, N9)"
    model_tier = "small"
    stub_note = "needs retrieval traffic, which exists only once a consumer is wired (Phase 3)"


class Prospector(_Stub):
    name = "prospector"
    goal = "close knowledge gaps"
    tools = (
        "gap_query.run", "source.discover", "code.parse_imports", "iac.parse",
        "graph.diff", "hypothesis.test", "task.create",
    )
    write_scope = "none: files tasks only, no claim scope at all"
    model_tier = "small"
    stub_note = "must corroborate against a second independent source before filing (FR-8.11)"


class RetrievalCritic(_Stub):
    name = "retrieval-critic"
    goal = "reduce bad answers"
    tools = (
        "retrieval.replay", "retrieval.replay_variant", "answer.feedback_fetch",
        "trace.fetch", "node.attribute_blame", "task.create",
    )
    write_scope = "none: repair tasks only"
    model_tier = "small"
    stub_note = "replays run under the original caller's effective scope, never the agent's (FR-8.12)"


class SchemaSteward(_Stub):
    name = "schema-steward"
    goal = "schema fits reality"
    tools = (
        "extraction.failure_sample", "cluster", "schema.read", "schema.simulate",
        "coverage.estimate", "proposal.submit",
    )
    write_scope = "none: advisory; schema changes are T5 and human-applied"
    model_tier = "large"
    stub_note = "needs a failure sample, which arrives with Confluence extraction (Phase 4)"


ALL_STUBS = (Reconciler, Verifier, Prospector, RetrievalCritic, SchemaSteward)
