"""Confluence connector: seam, and the one that may not create anchor nodes.

`creates_nodes = False` and `creatable_types = ("Document",)` are the guard for
M15: prose extraction links to existing Service, API, Endpoint and Team nodes,
and an unresolved mention becomes a Resolver candidate. It never creates an
anchor node, because "Payments", "payment-svc" and "the payments API" would
otherwise become three services (FR-6.15).

The LLM path stays behind kc/agents/llm.py and defaults to None: with no client
the page is still chunked and indexed, and its mentions are queued. Nothing is
invented (FR-6.5).
"""

from __future__ import annotations

import re
from typing import Any

from kc.agents import llm as llm_seam
from kc.connectors.base import Connector, Extraction, SourceObject
from kc.core import ids
from kc.core.model import now

ANCHOR_TYPES = ("Service", "API", "Endpoint", "Team", "Topic")

_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_SERVICE_ISH = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){1,3})\b")

# Subtype comes from labels, not from prose: FR-5.2 gives standards and
# glossary terms public visibility, so getting this wrong changes who can read.
LABEL_SUBTYPE = {
    "adr": "adr",
    "decision-record": "adr",
    "incident": "incident",
    "postmortem": "incident",
    "standard": "standard",
    "policy": "standard",
    "glossary": "glossary_term",
}


class ConfluenceConnector(Connector):
    """TODO(office): wire these three.

    discover  -> GET /wiki/rest/api/content/search?cql=
                 `space in (LEDGER, RECON, ARCH) and lastModified >= "{since}"`,
                 paginated on `_links.next`.
    fetch     -> GET /wiki/rest/api/content/{id}?expand=body.storage,version,
                 metadata.labels,space,restrictions.read.restrictions.user,
                 restrictions.read.restrictions.group
    object_permissions_narrower_than_bu
              -> `restrictions.read.restrictions.{user,group}` non-empty and
                 narrower than the space's BU group. Space permissions are
                 container-level and deliberately ignored (FR-5.9, D5).
    """

    source_system = "confluence"
    creates_nodes = False
    creatable_types = ("Document",)
    volatile_fields = (
        "version", "when", "lastModified", "lastUpdated", "friendlyLastModified",
        "_links", "_expandable", "extensions", "history", "childTypes", "viewCount",
        "likes", "macroRenderedOutput",
    )
    extraction_version = "1"

    def extract(
        self, obj: SourceObject, bu: str, *, client: llm_seam.LLMClient | None = None
    ) -> Extraction:
        """Chunk the page, link to anchors that already exist, queue the rest.

        Deterministic on its own. With an LLM client injected it may additionally
        propose property and relationship claims, each of which must quote a
        span verified against the source text (FR-6.5).
        """
        title = obj.content.get("title") or obj.source_id
        html = (
            (obj.content.get("body") or {}).get("storage", {}).get("value")
            if isinstance(obj.content.get("body"), dict)
            else obj.raw
        ) or obj.raw
        labels = [
            str(label.get("name", label)).lower()
            for label in (obj.content.get("metadata", {}) or {}).get("labels", {}).get(
                "results", []
            )
        ]
        subtype = next((LABEL_SUBTYPE[label] for label in labels if label in LABEL_SUBTYPE), "page")
        valid_from = obj.content.get("created") or now()

        document_id = ids.node_id("Document", obj.source_key)
        out = Extraction()
        out.claims.append(
            self.identity_claim(node_id=document_id, node_type="Document", bu=bu, obj=obj,
                                valid_from=valid_from, subtype=subtype, confidence=0.85)
        )
        for prop, value in (("name", title), ("status", "current")):
            out.claims.append(
                self.property_claim(node_id=document_id, node_type="Document", prop=prop,
                                    value=value, bu=bu, obj=obj, valid_from=valid_from,
                                    confidence=0.85)
            )
        if obj.content.get("url"):
            out.claims.append(
                self.property_claim(node_id=document_id, node_type="Document", prop="url",
                                    value=obj.content["url"], bu=bu, obj=obj,
                                    valid_from=valid_from, confidence=0.9)
            )

        for section, text in chunk_html(html, title):
            out.chunks.append(
                {
                    "node_id": document_id,
                    "kind": "adr" if subtype == "adr" else ("incident" if subtype == "incident" else "doc"),
                    "section_path": section,
                    "content": text,
                }
            )

        # Mentions: link only, never create (FR-6.15). Resolution against the
        # graph happens in ingest, which holds the store; here we report what the
        # page said and the span that said it.
        seen: set[str] = set()
        plain = _TAG.sub(" ", html)
        for candidate in _SERVICE_ISH.findall(plain):
            if candidate in seen or len(candidate) < 5:
                continue
            seen.add(candidate)
            out.mentions.append(
                {
                    "name": candidate,
                    "proposed_type": None,
                    "bu": bu,
                    "source_key": obj.source_key,
                    "span": candidate,
                    "anchor_types": list(ANCHOR_TYPES),
                }
            )

        client = llm_seam.get_client(client)
        if client is None:
            # No client on this machine: the page is indexed and its mentions
            # are queued. That is the whole degradation path (FR-6.17).
            return out

        schema = {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["from_name", "to_name", "edge_type", "span"],
                    },
                }
            },
        }
        system, user = llm_seam.extraction_prompt(schema, plain)
        result = client.complete_json(system=system, user=user, schema=schema)
        out.llm_used = True
        grounded: list[dict[str, Any]] = []
        for item in result.get("relationships", []) or []:
            factor = llm_seam.verify_span(str(item.get("span", "")), plain)
            if factor == 0.0:
                continue  # ungrounded item discarded, siblings kept (N6)
            grounded.append({**item, "grounding": factor})
        out.mentions.extend(
            {
                "name": item["from_name"],
                "proposed_type": None,
                "bu": bu,
                "source_key": obj.source_key,
                "span": item["span"],
                "proposed_edge": {"to_name": item["to_name"], "edge_type": item["edge_type"]},
                "grounding": item["grounding"],
            }
            for item in grounded
        )
        return out


def chunk_html(html: str, title: str) -> list[tuple[str, str]]:
    """Structure-aware chunking by heading, parent context in the header
    (FR-6.7). Pure function: testable without Confluence."""
    if not html:
        return []
    parts: list[tuple[str, str]] = []
    positions = [(m.start(), m.end(), m.group(2)) for m in _HEADING.finditer(html)]
    if not positions:
        text = _clean(html)
        return [(title, f"{title}\n{text}")] if text else []
    if positions[0][0] > 0:
        intro = _clean(html[: positions[0][0]])
        if intro:
            parts.append((title, f"{title}\n{intro}"))
    for index, (_, end, heading) in enumerate(positions):
        stop = positions[index + 1][0] if index + 1 < len(positions) else len(html)
        body = _clean(html[end:stop])
        if not body:
            continue
        section = f"{title}/{_clean(heading)}"
        parts.append((section, f"{section}\n{body}"))
    return parts


def _clean(html: str) -> str:
    return " ".join(_TAG.sub(" ", html).split())
