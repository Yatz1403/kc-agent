"""The knowledge model in memory: claims, events and the projected node/edge.

A claim is the unit of provenance, confidence, contradiction, supersession,
evidence and rollback (FR-4.6). Nodes hold identity only; their property values
are folded from open claims by the projector (FR-4.7), which is why `Node.props`
is a document of value-plus-claim-metadata rather than bare values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from kc.config import loader as cfg
from kc.core import ids
from kc.core.errors import SchemaViolation

IDENTITY = "identity"
PROPERTY = "property"
RELATIONSHIP = "relationship"
CLAIM_KINDS = (IDENTITY, PROPERTY, RELATIONSHIP)

PROVENANCE_CLASSES = ("source-derived", "agent-derived", "human-confirmed")
EXTRACTION_METHODS = ("parser", "llm", "agent", "human")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Claim:
    """One assertion made by one source at one time (FR-4.3, FR-4.6)."""

    kind: str
    subject_id: str
    bu_owner: str
    source_system: str
    source_instance: str
    source_id: str
    valid_from: str
    claim_id: str = ""
    prop: str | None = None
    value: Any = None
    from_id: str | None = None
    to_id: str | None = None
    edge_type: str | None = None
    node_type: str | None = None
    subtype: str | None = None
    provenance_class: str = "source-derived"
    extraction_method: str = "parser"
    valid_to: str | None = None
    recorded_at: str = ""
    confidence: float = 0.5
    last_verified_at: str = ""
    extraction_version: str = "1"
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_id:
            object.__setattr__(
                self,
                "claim_id",
                ids.claim_id(self.kind, self.subject_id, self.prop, self.source_key, self.valid_from),
            )
        if not self.recorded_at:
            object.__setattr__(self, "recorded_at", now())
        if not self.last_verified_at:
            object.__setattr__(self, "last_verified_at", self.recorded_at)

    @property
    def source_key(self) -> str:
        return ids.source_key(self.source_system, self.source_instance, self.source_id)

    @property
    def is_open(self) -> bool:
        return self.valid_to is None

    def authority(self) -> int:
        return cfg.authority_rank(
            self.source_system, self.node_type or "", self.prop, self.provenance_class
        )

    def closed_at(self, when: str) -> Claim:
        return replace(self, valid_to=when)

    def as_row(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "property": self.prop,
            "value": json.dumps(self.value),
            "from_id": self.from_id,
            "to_id": self.to_id,
            "edge_type": self.edge_type,
            "node_type": self.node_type,
            "subtype": self.subtype,
            "bu_owner": self.bu_owner,
            "provenance_class": self.provenance_class,
            "extraction_method": self.extraction_method,
            "source_system": self.source_system,
            "source_instance": self.source_instance,
            "source_id": self.source_id,
            "source_key": self.source_key,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "recorded_at": self.recorded_at,
            "confidence": self.confidence,
            "last_verified_at": self.last_verified_at,
            "extraction_version": self.extraction_version,
            "evidence": json.dumps(list(self.evidence)),
        }

    @classmethod
    def from_row(cls, row: Any) -> Claim:
        data = dict(row)
        data.pop("source_key", None)
        data.pop("seq", None)
        return cls(
            claim_id=data["claim_id"],
            kind=data["kind"],
            subject_id=data["subject_id"],
            prop=data["property"],
            value=json.loads(data["value"]) if data["value"] is not None else None,
            from_id=data["from_id"],
            to_id=data["to_id"],
            edge_type=data["edge_type"],
            node_type=data["node_type"],
            subtype=data["subtype"],
            bu_owner=data["bu_owner"],
            provenance_class=data["provenance_class"],
            extraction_method=data["extraction_method"],
            source_system=data["source_system"],
            source_instance=data["source_instance"],
            source_id=data["source_id"],
            valid_from=data["valid_from"],
            valid_to=data["valid_to"],
            recorded_at=data["recorded_at"],
            confidence=data["confidence"],
            last_verified_at=data["last_verified_at"],
            extraction_version=data["extraction_version"],
            evidence=tuple(json.loads(data["evidence"] or "[]")),
        )


def validate_claim(claim: Claim) -> None:
    """Schema validation, before anything is appended (FR-4.3, FR-4.9, FR-7.8).

    Raises SchemaViolation. This is the only place claim shape is judged; the
    rules themselves come from config, never from literals here.
    """
    if claim.kind not in CLAIM_KINDS:
        raise SchemaViolation(f"unknown claim kind {claim.kind}")
    if not claim.bu_owner:
        raise SchemaViolation("bu_owner is mandatory (AT-3)")
    if claim.provenance_class not in PROVENANCE_CLASSES:
        raise SchemaViolation(f"unknown provenance_class {claim.provenance_class}")
    if claim.extraction_method not in EXTRACTION_METHODS:
        raise SchemaViolation(f"unknown extraction_method {claim.extraction_method}")
    if not 0.0 <= claim.confidence <= 1.0:
        raise SchemaViolation(f"confidence {claim.confidence} outside 0.0-1.0")
    if claim.valid_to is not None and claim.valid_to < claim.valid_from:
        raise SchemaViolation("valid_to precedes valid_from")
    if claim.provenance_class == "agent-derived" and not claim.evidence:
        raise SchemaViolation("agent-derived claim must cite evidence (FR-4.3)")

    if claim.kind == IDENTITY:
        if not claim.node_type or not cfg.is_node_type(claim.node_type):
            raise SchemaViolation(f"unknown node type {claim.node_type}")
        if claim.subtype and claim.subtype not in cfg.document_subtypes():
            raise SchemaViolation(f"unknown subtype {claim.subtype}")
    elif claim.kind == PROPERTY:
        if not claim.node_type:
            raise SchemaViolation("property claim needs node_type for its rule")
        if cfg.property_rule(claim.node_type, claim.prop or "") is None:
            raise SchemaViolation(f"off-schema property {claim.node_type}.{claim.prop}")
        rule = cfg.property_rule(claim.node_type, claim.prop or "") or {}
        limit = rule.get("max_length")
        if limit and isinstance(claim.value, str) and len(claim.value) > int(limit):
            raise SchemaViolation(f"{claim.prop} longer than {limit} chars")
    else:
        if not (claim.from_id and claim.to_id and claim.edge_type):
            raise SchemaViolation("relationship claim needs from_id, to_id and edge_type")


@dataclass(slots=True)
class Node:
    """Graph projection of a node: identity plus folded current values.

    `props` is one document per node, keyed by property name, each value
    carrying its claim metadata. One column per field per property would
    migrate the table every time the model grows (FR-4.7, §20 DR2).
    """

    id: str
    type: str
    bu_owner: str
    visibility_class: str
    subtype: str | None = None
    live: bool = True
    source_key: str = ""
    props: dict[str, Any] = field(default_factory=dict)

    def value(self, prop: str) -> Any:
        entry = self.props.get(prop)
        if entry is None:
            return None
        if isinstance(entry, list):
            return [e["value"] for e in entry]
        return entry["value"]

    def claim_ids(self) -> list[str]:
        out: list[str] = []
        for entry in self.props.values():
            out.extend(e["claim_id"] for e in (entry if isinstance(entry, list) else [entry]))
        return out

    def contested(self) -> list[str]:
        return [
            prop
            for prop, entry in self.props.items()
            if isinstance(entry, dict) and entry.get("contested")
        ]


@dataclass(slots=True)
class Edge:
    """Graph projection of an edge. Several sources asserting the same edge
    raise its corroboration count and its confidence (FR-4.7 item 5)."""

    id: str
    edge_type: str
    from_id: str
    to_id: str
    bu_owner: str
    visibility_class: str
    claim_id: str
    confidence: float
    valid_from: str
    valid_to: str | None = None
    source_systems: tuple[str, ...] = ()
    corroboration: int = 1
    provenance_class: str = "source-derived"

    @property
    def live(self) -> bool:
        return self.valid_to is None


def corroborated_confidence(confidences: list[float], independent_sources: int) -> float:
    """Confidence of an edge asserted by several independent sources.

    Deterministic and monotonic: never below the best single claim, never 1.0,
    and rising with the number of distinct source systems. Not a model output
    (FR-6.12).
    """
    if not confidences:
        return 0.0
    best = max(confidences)
    if independent_sources <= 1:
        return round(best, 4)
    residual = 1.0 - best
    return round(min(0.99, 1.0 - residual * (0.5 ** (independent_sources - 1))), 4)
