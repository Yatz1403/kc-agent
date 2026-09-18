"""The serve path (FR-11.1 to FR-11.10).

Pipeline order is the order of the code below, and it starts with identity and
scope: v0.1 resolved names before it applied entitlements, which leaked the
existence of restricted nodes through disambiguation (B2).

There is no LLM here, and no reranker either. Ordering is reciprocal-rank
fusion over the tracks, which is deterministic and needs no model. The absence
is structural: nothing in this module can reach a model client, so there is no
fallback path that quietly acquires one (FR-11.1, AT-16).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from kc.config import loader as cfg
from kc.core import ids
from kc.core.errors import Ambiguous, NotFound
from kc.entitlements import filter as ent
from kc.serve import okf
from kc.stores.sqlite_store import Store, normalise_name


@dataclass(slots=True)
class _Entity:
    id: str
    type: str
    subtype: str | None
    bu: str
    visibility: str
    hop: int | None
    props: dict[str, Any]
    via: str = "traversal"
    contested: list[str] = field(default_factory=list)


class ServePipeline:
    def __init__(self, store: Store) -> None:
        self.store = store

    # --- entry point ---------------------------------------------------------

    def retrieve(
        self, message: dict[str, Any], *, user: str | None, application: str | None
    ) -> dict[str, Any]:
        # 1-2. identity, then effective scope. Fail closed (FR-5.5, AT-5).
        scope = ent.derive_scope(user, application)

        # 3. validate and clamp budgets (FR-9.8, FR-11.4).
        req = okf.validate_request(message)

        # 4. cache lookup. The epoch is part of the key, so an entry cannot
        #    survive a projector applying an event (FR-11.6).
        epoch = self.store.cache_epoch()
        key = ids.cache_key(
            {
                "okf": okf.OKF_VERSION,
                "intent": req["intent"],
                "anchors": json.dumps(req["anchors"], sort_keys=True),
                "as_of": req["as_of"],
                "hops": req["hops"],
                "tokens": req["max_tokens"],
                "query": req["query_text"],
                "artifacts": req["include_artifacts"],
                "scope": ",".join(scope.bus),
                "epoch": epoch,
            }
        )
        cached = self.store.cache_get(key, epoch)
        if cached is not None:
            response = dict(cached, request_id=req["request_id"])
            response["budget"] = dict(response["budget"], cache_hit=True)
            self._audit(req, scope, response, epoch)
            return response

        # 5. resolve anchors inside the scope (FR-11.3, AT-19).
        anchor_ids, anchors_out, ambiguous = self._resolve_anchors(req["anchors"], scope)
        if ambiguous:
            return okf.disambiguation(
                request_id=req["request_id"], ambiguous_anchors=ambiguous
            )

        # 6-7. anchor, then traverse within budget with the entitlement, edge
        #      and current-claim filters all in-query (FR-11.9, FR-5.12).
        entities, relations, paths, hop_budget_reached = self._traverse(
            anchor_ids, scope, req
        )

        # 8-10. multi-track fetch, liveness, RRF fusion (FR-11.10).
        artifacts = (
            self._artifacts(entities, scope, req) if req["include_artifacts"] else []
        )

        # 11-14. assemble, gap codes, trim (FR-9.1, FR-11.5).
        response = self._assemble(
            req, scope, anchors_out, entities, relations, paths, artifacts,
            hop_budget_reached, epoch,
        )

        self.store.cache_put(key, epoch, response)
        self._audit(req, scope, response, epoch)
        return response

    # --- anchors -------------------------------------------------------------

    def _resolve_anchors(
        self, anchors: list[dict[str, Any]], scope: ent.Scope
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        scope_sql, scope_args = ent.node_sql(scope, "n", ent.METADATA)
        resolved: list[str] = []
        out: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []

        for index, anchor in enumerate(anchors):
            if anchor.get("id"):
                node = self.store.get_node(anchor["id"])
                visibility = (
                    ent.visibility_of(node.bu_owner, node.visibility_class, scope)
                    if node and node.live
                    else None
                )
                if visibility is None:
                    # Not found and not entitled are the same answer (FR-5.8).
                    raise NotFound(anchor["id"])
                resolved.append(node.id)
                out.append({"id": node.id, "type": node.type, "bu": node.bu_owner})
                continue

            name = str(anchor.get("name"))
            candidates = self.store.resolve_alias(name, scope_sql, scope_args)
            if anchor.get("type"):
                candidates = [c for c in candidates if c["type"] == anchor["type"]]
            if not candidates:
                raise NotFound(name)
            if len(candidates) > 1:
                ambiguous.append(
                    {
                        "anchor_index": index,
                        "name": name,
                        "candidates": candidates,
                    }
                )
                continue
            chosen = candidates[0]
            resolved.append(chosen["id"])
            out.append({"id": chosen["id"], "type": chosen["type"], "bu": chosen["bu"]})

        return resolved, out, ambiguous

    # --- traversal -----------------------------------------------------------

    def _traverse(
        self, anchor_ids: list[str], scope: ent.Scope, req: dict[str, Any]
    ) -> tuple[dict[str, _Entity], list[dict[str, Any]], list[dict[str, Any]], bool]:
        near_sql, near_args, far_sql, far_args = ent.edge_endpoint_sql(scope)
        entities: dict[str, _Entity] = {}
        relations: dict[str, dict[str, Any]] = {}
        paths: list[dict[str, Any]] = []
        route: dict[str, list[str]] = {}

        for node_id, node in self.store.nodes_by_ids(anchor_ids).items():
            visibility = ent.visibility_of(node.bu_owner, node.visibility_class, scope)
            if visibility is None:
                continue
            entities[node_id] = self._entity(node, visibility, hop=0)
            route[node_id] = []

        frontier = list(entities)
        hop_budget_reached = False
        decay = float(cfg.retrieval_setting("hop_decay"))

        for hop in range(1, req["hops"] + 1):
            if not frontier:
                break
            rows = self.store.neighbours(
                frontier,
                near_sql=near_sql,
                far_sql=far_sql,
                args_near=near_args,
                args_far=far_args,
                as_of=req["as_of"],
                fanout=req["fanout"],
            )
            # Rank by edge criticality and decay before admitting the hop
            # (FR-11.4). Fan-out is capped in-query; this orders what survives.
            rows.sort(
                key=lambda r: -(
                    cfg.edge_criticality(r["edge_type"]) * float(r["confidence"]) * decay ** hop
                )
            )
            next_frontier: list[str] = []
            for row in rows:
                if row["edge_type"] == "SUPERSEDES":
                    continue  # entity succession, not a dependency (FR-4.2)
                far_id = row["far_id"]
                visibility = ent.visibility_of(
                    row["far_bu"], row["far_visibility"], scope
                )
                if visibility is None:
                    continue
                if far_id not in entities:
                    entities[far_id] = _Entity(
                        id=far_id,
                        type=row["far_type"],
                        subtype=row["far_subtype"],
                        bu=row["far_bu"],
                        visibility=visibility,
                        hop=hop,
                        props=ent.project_props(json.loads(row["far_props"]), visibility),
                    )
                    next_frontier.append(far_id)
                near_id = row["from_id"] if row["direction"] == "out" else row["to_id"]
                relations.setdefault(
                    row["edge_id"],
                    {
                        "id": row["edge_id"],
                        "from": row["from_id"],
                        "to": row["to_id"],
                        "type": row["edge_type"],
                        "hop": hop,
                        "claim_id": row["claim_id"],
                        "provenance_class": row["provenance_class"],
                        "confidence": round(float(row["confidence"]), 4),
                        "corroboration": int(row["corroboration"]),
                        "source_systems": json.loads(row["source_systems"]),
                    },
                )
                if far_id not in route and near_id in route:
                    route[far_id] = route[near_id] + [row["edge_id"]]
            frontier = next_frontier
            if frontier and hop == req["hops"]:
                hop_budget_reached = True

        for node_id, relation_ids in route.items():
            if relation_ids:
                paths.append(
                    {
                        "id": "p_" + ids.content_hash(node_id + "".join(relation_ids))[:12],
                        "anchor_id": anchor_ids[0] if anchor_ids else None,
                        "to": node_id,
                        "relation_ids": relation_ids,
                    }
                )
        return entities, list(relations.values()), paths, hop_budget_reached

    def _entity(self, node: Any, visibility: str, hop: int | None) -> _Entity:
        return _Entity(
            id=node.id,
            type=node.type,
            subtype=node.subtype,
            bu=node.bu_owner,
            visibility=visibility,
            hop=hop,
            props=ent.project_props(node.props, visibility),
            contested=node.contested() if visibility == ent.FULL else [],
        )

    # --- artifacts -----------------------------------------------------------

    def _artifacts(
        self, entities: dict[str, _Entity], scope: ent.Scope, req: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Targeted fetch plus lexical BM25, fused by RRF.

        Content requires full visibility: a topology node from another BU never
        contributes an artifact (FR-9.4, AT-7).
        """
        full_ids = [e.id for e in entities.values() if e.visibility == ent.FULL]
        tracks: list[list[dict[str, Any]]] = []

        if "targeted" in req["tracks"]:
            tracks.append(self.store.chunks_for_nodes(full_ids, scope.namespaces))
        if "lexical" in req["tracks"]:
            query = req["query_text"] or " ".join(
                str(e.props.get("name", {}).get("value", ""))
                for e in entities.values()
                if e.hop == 0
            )
            hits = self.store.bm25_search(query, scope.namespaces)
            tracks.append([h for h in hits if self._chunk_allowed(h, entities, scope)])

        fused = self._rrf(tracks)
        out = []
        for item in fused:
            out.append(
                {
                    "node_id": item["node_id"],
                    "chunk_id": item["chunk_id"],
                    "kind": item["kind"],
                    "section_path": item["section_path"].split("/") if item["section_path"] else [],
                    "content": item["content"],
                    "ref": f"{item['node_id']}#{item['section_path']}",
                    "score": item["_rrf"],
                }
            )
        return out

    def _chunk_allowed(
        self, hit: dict[str, Any], entities: dict[str, _Entity], scope: ent.Scope
    ) -> bool:
        """A lexical hit for a node outside the traversal set is admitted only
        when the caller could see that node's content anyway."""
        known = entities.get(hit["node_id"])
        if known is not None:
            return known.visibility == ent.FULL
        node = self.store.get_node(hit["node_id"])
        if node is None or not node.live:
            return False
        return ent.visibility_of(node.bu_owner, node.visibility_class, scope) == ent.FULL

    @staticmethod
    def _rrf(tracks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Reciprocal rank fusion (FR-11.2 step 10). No model involved."""
        k = float(cfg.retrieval_setting("rrf_k"))
        scores: dict[str, float] = {}
        seen: dict[str, dict[str, Any]] = {}
        for track in tracks:
            for rank, item in enumerate(track, start=1):
                cid = item["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                seen.setdefault(cid, item)
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [dict(seen[cid], _rrf=round(score, 6)) for cid, score in ordered]

    # --- assembly ------------------------------------------------------------

    def _assemble(
        self,
        req: dict[str, Any],
        scope: ent.Scope,
        anchors_out: list[dict[str, Any]],
        entities: dict[str, _Entity],
        relations: list[dict[str, Any]],
        paths: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        hop_budget_reached: bool,
        epoch: int,
    ) -> dict[str, Any]:
        gaps: list[dict[str, Any]] = []
        if hop_budget_reached:
            gaps.append(okf.gap("HOP_BUDGET_REACHED"))

        for entity in entities.values():
            if entity.visibility != ent.FULL:
                continue  # a gap code must never describe a node kept hidden
            for prop in entity.contested:
                gaps.append(okf.gap("CONTESTED_PROPERTY", entity.id))
                break
            if entity.type in ("Service", "API", "Endpoint"):
                if not self.store.has_incident_edge(entity.id, "OWNED_BY", "out"):
                    gaps.append(okf.gap("NO_OWNER", entity.id))
            if entity.type in ("API", "Endpoint"):
                if not self.store.has_incident_edge(entity.id, "COVERS", "in"):
                    gaps.append(okf.gap("NO_TEST_COVERAGE", entity.id))
            if entity.type == "TestCase":
                if not self.store.has_incident_edge(entity.id, "VALIDATES", "out"):
                    gaps.append(okf.gap("NO_REQUIREMENT_LINK", entity.id))

        entity_payload = [
            {
                "id": e.id,
                "type": e.type,
                "subtype": e.subtype,
                "visibility": e.visibility,
                "hop": e.hop,
                "via": e.via,
                "props": e.props,
            }
            for e in sorted(entities.values(), key=lambda x: (x.hop if x.hop is not None else 99, x.id))
        ]

        trimmed, entity_payload, relations, paths, artifacts, tokens = self._trim(
            req["max_tokens"], entity_payload, relations, paths, artifacts
        )
        if trimmed:
            gaps.append(okf.gap("TOKEN_CEILING_TRIMMED"))

        provenance = self._provenance(entity_payload, relations, artifacts)
        return okf.context_response(
            request_id=req["request_id"],
            intent=req["intent"],
            as_of=req["as_of"],
            anchors=anchors_out,
            entities=entity_payload,
            relations=relations,
            paths=paths,
            artifacts=artifacts,
            gaps=gaps,
            provenance=provenance,
            budget={
                "hops": req["hops"],
                "tokens_returned": tokens,
                "tokenizer": okf.tokenizer_name(),
                "cache_hit": False,
                "cache_epoch": epoch,
            },
        )

    @staticmethod
    def _trim(
        max_tokens: int,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        paths: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
    ) -> tuple[bool, list, list, list, list, int]:
        """Trim outermost hop first, then lowest score within a hop, and keep
        the result referentially intact (FR-11.2 step 14, N14)."""

        def total() -> int:
            return (
                sum(okf.count_tokens(a["content"]) for a in artifacts)
                + 5 * len(entities)
                + 3 * len(relations)
            )

        trimmed = False
        while total() > max_tokens and artifacts:
            artifacts.sort(key=lambda a: a["score"])
            artifacts.pop(0)
            trimmed = True
        while total() > max_tokens and len(entities) > 1:
            entities.sort(key=lambda e: (e["hop"] if e["hop"] is not None else 99, -0.0))
            victim = entities.pop()["id"]
            artifacts = [a for a in artifacts if a["node_id"] != victim]
            relations = [r for r in relations if victim not in (r["from"], r["to"])]
            kept = {r["id"] for r in relations}
            paths = [p for p in paths if set(p["relation_ids"]) <= kept and p["to"] != victim]
            trimmed = True
        return trimmed, entities, relations, paths, artifacts, total()

    @staticmethod
    def _provenance(
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """One entry per claim or chunk referenced anywhere in the response, so
        every value can be traced back (FR-9.5)."""
        out: dict[str, dict[str, Any]] = {}
        for entity in entities:
            for prop, entry in entity["props"].items():
                for value in entry if isinstance(entry, list) else [entry]:
                    if not isinstance(value, dict) or "claim_id" not in value:
                        continue
                    out[value["claim_id"]] = {
                        "id": value["claim_id"],
                        "kind": "claim",
                        "source_system": value.get("source_system"),
                        "source_id": value.get("source_id"),
                        "valid_from": value.get("valid_from"),
                        "valid_to": None,
                        "provenance_class": value.get("provenance_class"),
                        "extraction_method": value.get("extraction_method"),
                        "property": prop,
                    }
        for relation in relations:
            out[relation["claim_id"]] = {
                "id": relation["claim_id"],
                "kind": "claim",
                "provenance_class": relation["provenance_class"],
                "source_systems": relation.get("source_systems", []),
                "corroboration": relation.get("corroboration", 1),
            }
        for artifact in artifacts:
            out[artifact["chunk_id"]] = {
                "id": artifact["chunk_id"],
                "kind": "chunk",
                "node_id": artifact["node_id"],
            }
        return [out[key] for key in sorted(out)]

    # --- audit ---------------------------------------------------------------

    def _audit(
        self, req: dict[str, Any], scope: ent.Scope, response: dict[str, Any], epoch: int
    ) -> None:
        """No exclusion data. What the caller could not see is recomputed
        offline by replay at `seq` (FR-15.2, §20 DR7)."""
        self.store.put_audit(
            {
                "request_id": req["request_id"],
                "user_id": scope.user,
                "application": scope.application,
                "scope": scope.as_json(),
                "request": {
                    "intent": req["intent"],
                    "anchors": req["anchors"],
                    "as_of": req["as_of"],
                    "hops": req["hops"],
                },
                "seq": self.store.last_seq(),
                "cache_epoch": epoch,
                "node_ids": [e["id"] for e in response.get("entities", [])],
                "chunk_ids": [a["chunk_id"] for a in response.get("artifacts", [])],
            }
        )


def query(
    store: Store,
    *,
    user: str,
    application: str,
    intent: str,
    anchor: str,
    request_id: str | None = None,
    query_text: str | None = None,
    include_artifacts: bool = True,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper used by the CLI and the HTTP handler."""
    anchors = [{"id": anchor}] if anchor.startswith("n_") else [{"name": anchor}]
    message = okf.context_request(
        request_id=request_id or ("r_" + ids.content_hash(f"{user}{anchor}{intent}")[:12]),
        intent=intent,
        anchors=anchors,
        query_text=query_text or normalise_name(anchor).replace("-", " "),
        include_artifacts=include_artifacts,
        as_of=as_of,
    )
    return ServePipeline(store).retrieve(message, user=user, application=application)
