"""The agent tool surface (FR-8.3, FR-5.7, FR-13.1).

Every read here is scoped to the task's BUs, not to the agent's identity: a
background task has no human caller, so its scope comes from its subjects
(FR-5.7). Results carry their provenance class, because the gate needs it to
enforce the inbreeding guard (FR-13.1).

No tool in this module writes to the change log or a projection. `escalate` and
`create_task` write to the task queue, and `remember` to agent memory, which are
not knowledge stores (FR-8.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kc.config import loader as cfg
from kc.connectors import registry
from kc.core import ids
from kc.entitlements import filter as ent
from kc.stores.sqlite_store import Store, normalise_name


@dataclass(slots=True)
class ToolContext:
    store: Store
    agent: str
    task_scope: tuple[str, ...]
    run_id: str
    trace: list[dict[str, Any]]

    def log(self, tool: str, **fields: Any) -> None:
        self.trace.append({"tool": tool, **fields})


class Tools:
    """Bound to one task. Construct per run, never shared across scopes."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    # --- shared base tools ---------------------------------------------------

    def graph_query(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Nodes within the task scope, each labelled with provenance class."""
        nodes = self.ctx.store.nodes_by_ids(node_ids)
        out = []
        for node in nodes.values():
            if node.bu_owner not in self.ctx.task_scope and node.visibility_class == "restricted":
                continue
            out.append(
                {
                    "id": node.id,
                    "type": node.type,
                    "bu": node.bu_owner,
                    "live": node.live,
                    "props": node.props,
                    "provenance_classes": sorted(
                        {
                            entry.get("provenance_class", "source-derived")
                            for entry in _entries(node.props)
                        }
                    ),
                }
            )
        self.ctx.log("graph.query", requested=len(node_ids), returned=len(out))
        return out

    def claim_fetch(self, subject_id: str, prop: str | None = None) -> list[dict[str, Any]]:
        claims = self.ctx.store.open_claims(subject_id, prop)
        out = [
            {
                "claim_id": c.claim_id,
                "property": c.prop,
                "value": c.value,
                "provenance_class": c.provenance_class,
                "source_system": c.source_system,
                "authority": c.authority(),
                "confidence": c.confidence,
                "valid_from": c.valid_from,
            }
            for c in claims
            if c.bu_owner in self.ctx.task_scope
        ]
        self.ctx.log("claim.fetch", subject=subject_id, returned=len(out))
        return out

    def alias_lookup(self, name: str) -> list[dict[str, Any]]:
        scope = ent.Scope(user=f"agent:{self.ctx.agent}", application="kc-batch",
                          bus=tuple(self.ctx.task_scope))
        sql, args = ent.node_sql(scope, "n", ent.METADATA)
        hits = self.ctx.store.resolve_alias(name, sql, args)
        self.ctx.log("alias.lookup", name=normalise_name(name), hits=len(hits))
        return hits

    def similarity(self, name: str, node_type: str | None = None) -> list[dict[str, Any]]:
        hits = self.ctx.store.similar_nodes(name, node_type, list(self.ctx.task_scope))
        self.ctx.log("embedding.similarity", name=name, hits=len(hits))
        return hits[:10]

    def fingerprint_compare(self, left_id: str, right_id: str) -> dict[str, Any]:
        from kc.resolve.fingerprint import fingerprint

        left = fingerprint(self.ctx.store, left_id)
        right = fingerprint(self.ctx.store, right_id)
        if left is None or right is None:
            self.ctx.log("fingerprint.compare", result="missing")
            return {}
        features = left.compare(right)
        self.ctx.log("fingerprint.compare", left=left_id, right=right_id, **features)
        return features

    def source_fetch(self, source_system: str, source_id: str) -> dict[str, Any] | None:
        """Live source read. Returns None when the connector has no credentials
        on this machine, which is a fact for the agent to escalate on rather
        than guess around (connectors are deferred by design)."""
        connector = registry.get(source_system)
        if connector is None:
            self.ctx.log("source.fetch", source_system=source_system, result="no_connector")
            return None
        try:
            obj = connector.fetch(source_id)
        except NotImplementedError as exc:
            self.ctx.log("source.fetch", source_system=source_system, result="not_wired",
                         detail=str(exc))
            return None
        self.ctx.log("source.fetch", source_system=source_system, result="ok")
        return obj

    def recall(self, kind: str = "episodic", limit: int = 20) -> list[dict[str, Any]]:
        out = self.ctx.store.recall_memory(
            agent=self.ctx.agent, task_scope=list(self.ctx.task_scope), kind=kind, limit=limit
        )
        self.ctx.log("memory.episodic.recall", kind=kind, returned=len(out))
        return out

    def remember(self, body: dict[str, Any], kind: str = "episodic") -> None:
        self.ctx.store.put_memory(
            memory_id="m_" + ids.content_hash(f"{self.ctx.run_id}{kind}{body}")[:16],
            kind=kind,
            agent=self.ctx.agent,
            task_id=None,
            bus=list(self.ctx.task_scope),
            body=body,
        )
        self.ctx.log("memory.episodic.write", kind=kind)

    def create_task(
        self, kind: str, payload: dict[str, Any], *, reason: str, state: str = "open"
    ) -> str:
        task_id = "t_" + ids.content_hash(f"{kind}{payload}{reason}")[:16]
        self.ctx.store.put_task(
            task_id=task_id,
            kind=kind,
            state=state,
            scope=list(self.ctx.task_scope),
            payload=payload,
            reason=reason,
            run_id=self.ctx.run_id,
        )
        self.ctx.log("task.create", kind=kind, task_id=task_id)
        return task_id

    def budget_check(self, budget: Any) -> dict[str, Any]:
        remaining = budget.remaining()
        self.ctx.log("budget.check", **remaining)
        return remaining

    def authority_rank(self, source_system: str, node_type: str, prop: str | None = None) -> int:
        return cfg.authority_rank(source_system, node_type, prop)


def _entries(props: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in props.values():
        out.extend(entry if isinstance(entry, list) else [entry])
    return [e for e in out if isinstance(e, dict)]
