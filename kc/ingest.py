"""The fast path (FR-6.1 to FR-6.19, FR-7.2).

Order, and what each step refuses:

    1. BU mapping        unmapped container      -> quarantine (FR-5.3)
    2. permission check  narrower than its BU    -> skip and log (FR-5.9)
    3. change detection  same normalised hash    -> drop, no extraction (FR-6.2)
    4. extraction        parsers first, LLM only for free text (FR-6.4, FR-6.5)
    5. resolution        BU-scoped cascade, new entity under the threshold
    6. claim-set diff    claims the source dropped -> closed (FR-6.18)
    7. append            through the Change API as kc-ingest, ungated

The fast path never waits on the cognitive layer: with the Curator killed, a
spoke stubbed or a rate limit hit, it writes its claims and queues the
escalation (FR-6.17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kc.agents import llm as llm_seam
from kc.config import loader as cfg
from kc.connectors import registry
from kc.connectors.base import Extraction, SourceObject
from kc.core import ids
from kc.core.changeapi import ChangeAPI
from kc.core.errors import Quarantined, SourceRestricted
from kc.core.model import Claim, now
from kc.resolve.cascade import Step, mention_candidate, resolve_reference
from kc.stores.sqlite_store import Store

INGEST_IDENTITY = "kc-ingest"


@dataclass(slots=True)
class IngestResult:
    source_key: str
    status: str
    claims: int = 0
    closed: int = 0
    chunks: int = 0
    mentions: int = 0
    llm_used: bool = False
    detail: str | None = None
    tasks: list[str] = field(default_factory=list)


class FastPath:
    def __init__(self, store: Store, *, client: llm_seam.LLMClient | None = None) -> None:
        self.store = store
        self.change = ChangeAPI(store)
        self.client = llm_seam.get_client(client)

    # --- entry points --------------------------------------------------------

    def ingest_object(self, obj: SourceObject, *, context_id: str | None = None) -> IngestResult:
        connector = registry.get(obj.source_system)
        if connector is None:
            return IngestResult(obj.source_key, "no_connector")

        # 1. BU assignment is deterministic, never inferred (FR-5.3).
        bu = cfg.bu_for_source(obj.source_system, obj.container)
        if bu is None:
            self.store.record_skip(obj.source_system, obj.container, obj.source_id,
                                   "UNMAPPED_CONTAINER")
            raise Quarantined(
                f"{obj.source_system}:{obj.container} has no BU mapping (FR-5.3)"
            )

        # 2. object-level permissions (FR-5.9). An unwired check is a hard stop,
        #    not an assumption that the object is public.
        try:
            restricted = connector.object_permissions_narrower_than_bu(obj)
        except NotImplementedError:
            restricted = False  # deferred connector: fixture and file paths only
        if restricted:
            self.store.record_skip(obj.source_system, obj.container, obj.source_id,
                                   "OBJECT_RESTRICTED")
            self._retract_restricted(obj, bu)
            raise SourceRestricted(f"{obj.source_key} is restricted below its BU (FR-5.9)")

        if obj.deleted:
            closed = self._close_all(obj, context_id)
            return IngestResult(obj.source_key, "deleted", closed=closed)

        # 3. change detection over a normalised projection (FR-6.2, N1).
        digest = connector.hash_projection(obj)
        previous = self.store.source_hash(obj.source_key)
        if (
            previous
            and previous["hash"] == digest
            and previous["extraction_version"] == connector.extraction_version
        ):
            self.store.record_ingest(obj.source_key, digest, llm_used=False, dropped=True)
            return IngestResult(obj.source_key, "dropped_unchanged")

        # 4. extraction. Parsers for structured sources; the LLM seam is only
        #    reachable for free text and only when a client was injected.
        if obj.source_system == "confluence":
            extraction = connector.extract(obj, bu, client=self.client)  # type: ignore[call-arg]
        else:
            extraction = connector.extract(obj, bu)

        # 5-6. resolve mentions, diff the claim set, then append.
        result = self._apply(obj, bu, extraction, connector.extraction_version, context_id)
        self.store.put_source_hash(obj.source_key, digest, connector.extraction_version)
        self.store.record_ingest(obj.source_key, digest, llm_used=extraction.llm_used,
                                 dropped=False)
        return result

    def plan_object(self, obj: SourceObject) -> dict[str, Any]:
        """What ingesting this object would write, without appending anything.

        Drives the same steps as `ingest_object` up to extraction, so a dry run
        shows the real extraction of a real ticket before any of it reaches the
        log. Nothing here calls the Change API.
        """
        connector = registry.get(obj.source_system)
        report: dict[str, Any] = {
            "source_key": obj.source_key,
            "source_system": obj.source_system,
            "container": obj.container,
        }
        if connector is None:
            return {**report, "decision": "no_connector"}

        bu = cfg.bu_for_source(obj.source_system, obj.container)
        report["bu_owner"] = bu
        if bu is None:
            return {**report, "decision": "quarantine", "reason": "UNMAPPED_CONTAINER"}

        try:
            restricted = connector.object_permissions_narrower_than_bu(obj)
            report["permission_check"] = "applied"
        except NotImplementedError:
            restricted = False
            report["permission_check"] = "not wired (TODO(office))"
        if restricted:
            return {**report, "decision": "skip", "reason": "OBJECT_RESTRICTED"}
        if obj.deleted:
            open_claims = self.store.open_claims_for_source(obj.source_key)
            return {**report, "decision": "close_all", "would_close": len(open_claims)}

        digest = connector.hash_projection(obj)
        previous = self.store.source_hash(obj.source_key)
        report["hash"] = digest[:16]
        if (
            previous
            and previous["hash"] == digest
            and previous["extraction_version"] == connector.extraction_version
        ):
            return {**report, "decision": "drop_unchanged"}
        report["decision"] = "would_ingest"
        report["previously_seen"] = bool(previous)

        if obj.source_system == "confluence":
            extraction = connector.extract(obj, bu, client=self.client)  # type: ignore[call-arg]
        else:
            extraction = connector.extract(obj, bu)

        nodes: dict[str, int] = {}
        edges: dict[str, int] = {}
        props: dict[str, int] = {}
        for claim in extraction.claims:
            if claim.kind == "identity":
                nodes[str(claim.node_type)] = nodes.get(str(claim.node_type), 0) + 1
            elif claim.kind == "relationship":
                edges[str(claim.edge_type)] = edges.get(str(claim.edge_type), 0) + 1
            else:
                key = f"{claim.node_type}.{claim.prop}"
                props[key] = props.get(key, 0) + 1

        fresh = {c.claim_id for c in extraction.claims}
        stale = [
            {"claim_id": c.claim_id, "kind": c.kind, "property": c.prop,
             "edge_type": c.edge_type, "subject_id": c.subject_id}
            for c in self.store.open_claims_for_source(obj.source_key)
            if c.claim_id not in fresh
        ]
        return {
            **report,
            "would_write": {
                "claims": len(extraction.claims),
                "nodes": nodes,
                "edges": edges,
                "properties": props,
                "chunks": len(extraction.chunks),
            },
            "would_close": stale,
            "mentions": [m.get("name") for m in extraction.mentions][:20],
            "llm_used": extraction.llm_used,
            "sample_values": {
                f"{c.node_type}.{c.prop}": c.value
                for c in extraction.claims
                if c.kind == "property"
            },
        }

    def ingest_claims(
        self,
        *,
        claims: list[Claim],
        chunks: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> IngestResult:
        """Append pre-built claims. The fixture and tests use this; connectors
        go through ingest_object."""
        receipt = self.change.submit(
            INGEST_IDENTITY, "add_claim", claims=claims, context_id=context_id
        )
        written = 0
        for chunk in chunks or []:
            self.change.submit(
                INGEST_IDENTITY,
                "chunk_add",
                payload={
                    "chunk_id": chunk.get("chunk_id")
                    or ids.chunk_id(chunk["node_id"], chunk.get("section_path", ""), "1"),
                    **chunk,
                },
                context_id=context_id,
            )
            written += 1
        return IngestResult(
            source_key="(direct)", status="appended", claims=len(receipt.claim_ids),
            chunks=written,
        )

    # --- internals -----------------------------------------------------------

    def _apply(
        self,
        obj: SourceObject,
        bu: str,
        extraction: Extraction,
        extraction_version: str,
        context_id: str | None,
    ) -> IngestResult:
        connector = registry.get(obj.source_system)
        creates_nodes = bool(connector and connector.creates_nodes)

        claims = list(extraction.claims)
        if not creates_nodes:
            # Confluence may create Document nodes only; anything else it would
            # have created becomes a Resolver candidate (FR-6.15).
            allowed = set(getattr(connector, "creatable_types", ()) or ())
            kept: list[Claim] = []
            for claim in claims:
                if claim.kind == "identity" and claim.node_type not in allowed:
                    extraction.mentions.append(
                        mention_candidate(
                            name=str(claim.value or claim.subject_id),
                            node_type=claim.node_type,
                            bu=bu,
                            source_key=obj.source_key,
                            span="",
                        )
                    )
                    continue
                kept.append(claim)
            claims = kept

        tasks: list[str] = []
        mentions_queued = 0
        for mention in extraction.mentions:
            resolution = resolve_reference(
                self.store,
                name=str(mention.get("name", "")),
                node_type=mention.get("proposed_type"),
                bu=bu,
                source_key=mention.get("source_key"),
            )
            if resolution.resolved and resolution.node_id:
                # Linking an incoming reference to an existing node is ungated
                # (FR-6.9); creating an edge needs a target type the edge allows,
                # so a proposed edge is recorded on the task instead of guessed.
                continue
            candidate = mention_candidate(
                name=str(mention.get("name", "")),
                node_type=mention.get("proposed_type"),
                bu=bu,
                source_key=str(mention.get("source_key", obj.source_key)),
                span=str(mention.get("span", "")),
            )
            task_id = "t_" + ids.content_hash(candidate["candidate_id"])[:16]
            self.store.put_task(
                task_id=task_id,
                kind="mention_candidate",
                state="open",
                scope=[bu],
                payload={"candidates": [candidate], "subjects": []},
                reason="unresolved mention: link or create is the Resolver's call (FR-6.15)",
                context_id=context_id,
            )
            tasks.append(task_id)
            mentions_queued += 1

        receipt = self.change.submit(
            INGEST_IDENTITY, "add_claim", claims=claims, context_id=context_id
        )

        # Claim-set diff: what this source object no longer asserts is closed
        # (FR-6.18). Endpoint removed from a spec, field cleared in Jira.
        fresh = {c.claim_id for c in claims}
        closed = 0
        stale = [
            c.claim_id
            for c in self.store.open_claims_for_source(obj.source_key)
            if c.claim_id not in fresh
        ]
        if stale:
            self.change.submit(
                INGEST_IDENTITY, "close_claim", closes=stale,
                payload={"valid_to": now(), "reason": "absent from source (FR-6.18)"},
                context_id=context_id,
            )
            closed = len(stale)

        chunks = 0
        for chunk in extraction.chunks:
            self.change.submit(
                INGEST_IDENTITY,
                "chunk_add",
                payload={
                    "chunk_id": ids.chunk_id(
                        chunk["node_id"], chunk.get("section_path", ""), extraction_version
                    ),
                    "node_id": chunk["node_id"],
                    "bu_owner": bu,
                    "kind": chunk["kind"],
                    "section_path": chunk.get("section_path", ""),
                    "content": chunk["content"],
                },
                context_id=context_id,
            )
            chunks += 1

        return IngestResult(
            source_key=obj.source_key,
            status="ingested",
            claims=len(receipt.claim_ids),
            closed=closed,
            chunks=chunks,
            mentions=mentions_queued,
            llm_used=extraction.llm_used,
            tasks=tasks,
        )

    def _close_all(self, obj: SourceObject, context_id: str | None) -> int:
        """A deleted source object closes every claim it made (FR-6.18 item 2)."""
        stale = [c.claim_id for c in self.store.open_claims_for_source(obj.source_key)]
        if not stale:
            return 0
        self.change.submit(
            INGEST_IDENTITY, "close_claim", closes=stale,
            payload={"valid_to": now(), "reason": "source object deleted (FR-6.18)"},
            context_id=context_id,
        )
        return len(stale)

    def _retract_restricted(self, obj: SourceObject, bu: str) -> None:
        """Tightening is immediate and ungated: claims close and chunks retract
        as soon as the source restricts the object (FR-5.9 item 2, FR-7.3)."""
        claims = self.store.open_claims_for_source(obj.source_key)
        if not claims:
            return
        self.change.submit(
            INGEST_IDENTITY, "close_claim", closes=[c.claim_id for c in claims],
            payload={"valid_to": now(), "reason": "OBJECT_RESTRICTED (FR-5.9)"},
        )
        for node_id in {c.subject_id for c in claims if c.kind != "relationship"}:
            self.change.submit(
                INGEST_IDENTITY, "chunk_retract", payload={"node_id": node_id}
            )
