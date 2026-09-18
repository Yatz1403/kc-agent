"""Resolver: the one full agent in the MVP (FR-8.8).

A real bounded loop over the cascade — alias, similarity, structural
fingerprint, live source — that ends in exactly one of three states. It holds
no write tool: a merge leaves here as a proposal and the gate decides
(FR-8.2, FR-7.14).

The live-source step is where the deferred connectors show up honestly: with no
credentials on this machine `source_fetch` returns None, and the loop escalates
with `NO_LLM_CLIENT` or `NO_AUTHORITY` rather than inventing a verdict.
"""

from __future__ import annotations

from typing import Any

from kc.agents.base import PROPOSAL, Agent, Budget, Outcome
from kc.agents.tools import ToolContext, Tools
from kc.core import ids
from kc.resolve.cascade import Step, resolve_reference
from kc.serve import okf


class Resolver(Agent):
    name = "resolver"
    goal = "correct entity identity"
    tools = (
        "alias.lookup", "alias.propose", "graph.neighbourhood", "source.fetch",
        "source.search", "embedding.similarity", "fingerprint.compare",
        "merge.propose", "split.propose", "memory.episodic.recall",
        "memory.episodic.write", "task.escalate", "budget.check", "trace.log",
    )
    write_scope = "none: merge and alias proposals only (FR-8.2)"
    model_tier = "small"

    def step(
        self,
        task: dict[str, Any],
        budget: Budget,
        recalled: list[dict[str, Any]],
        trace: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> Outcome:
        run_id = task.get("run_id", "run_unknown")
        scope = tuple(task.get("task_scope", []))
        tools = Tools(ToolContext(self.store, self.name, scope, run_id, trace))

        candidates = task.get("candidates") or []
        if not candidates:
            return self.nothing_to_do(task, "DUPLICATE_TASK", trace)

        # Batched candidates share one scope (FR-8.5); the loop is per candidate
        # but the budget is shared across the batch.
        proposals: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        for candidate in candidates:
            budget.check()
            name = str(candidate.get("name", ""))
            node_type = candidate.get("proposed_type")
            bu = candidate.get("bu") or (scope[0] if scope else "")

            prior = [
                m for m in recalled
                if m["body"].get("subjects") and name in str(m["body"].get("subjects"))
            ]
            if prior:
                trace.append({"step": "recall_hit", "name": name, "count": len(prior)})

            budget.spend(tool_calls=1)
            resolution = resolve_reference(
                self.store, name=name, node_type=node_type, bu=bu,
                source_key=candidate.get("source_key"),
            )
            trace.append({"step": "cascade", "name": name, "result": str(resolution.step)})

            if resolution.step in (Step.SOURCE_KEY, Step.ALIAS, Step.SIMILARITY):
                # Already resolvable without an agent: nothing to propose, and
                # saying so is a successful outcome.
                continue

            if resolution.step is Step.FINGERPRINT and resolution.candidates:
                budget.spend(tool_calls=1)
                best = resolution.candidates[0]
                features = tools.fingerprint_compare(best["id"], best["id"])
                evidence_items = self._evidence_for(best["id"])
                if not evidence_items:
                    unresolved.append(candidate)
                    continue
                proposals.append(
                    okf.proposal(
                        proposal_id="p_" + ids.content_hash(f"{run_id}{name}{best['id']}")[:16],
                        run_id=run_id,
                        agent=self.name,
                        action="alias_rule",
                        subjects=[{"id": best["id"], "base_version": self.store.last_seq()}],
                        changes=[
                            {
                                "op": "alias_rule",
                                "alias_key": name,
                                "node_id": best["id"],
                                "bu_owner": best.get("bu", bu),
                                "provenance_class": "agent-derived",
                            }
                        ],
                        evidence=evidence_items,
                    )
                )
                trace.append({"step": "propose", "action": "alias_rule", "node": best["id"],
                              "features": features or resolution.evidence})
                continue

            # Live source is the last cheap-to-describe step, and the one the
            # office wiring unlocks.
            budget.spend(tool_calls=1)
            fetched = tools.source_fetch(
                str(candidate.get("source_system", "jira")), str(candidate.get("source_key", ""))
            )
            if fetched is None:
                unresolved.append(candidate)
                continue
            unresolved.append(candidate)

        if proposals:
            first = proposals[0]
            return Outcome(
                terminal_state=PROPOSAL,
                message=first if len(proposals) == 1 else {**first, "batch": proposals},
                evidence=list(first["evidence"]),
                trace=trace,
            )
        if unresolved:
            evidence.extend({"candidate": c.get("candidate_id") or c.get("name")} for c in unresolved)
            reason = "NO_AUTHORITY" if self.client is not None else "NO_LLM_CLIENT"
            return self.escalate(task, reason, evidence, trace)
        return self.nothing_to_do(task, "ALREADY_CONSISTENT", trace)

    def _evidence_for(self, node_id: str) -> list[dict[str, Any]]:
        """Cite source-derived claims only: agent-derived evidence for an
        agent-derived proposal is rejected at the gate anyway (FR-13.1)."""
        return [
            {"claim_id": claim.claim_id}
            for claim in self.store.open_claims(node_id)
            if claim.provenance_class != "agent-derived"
        ][:5]
