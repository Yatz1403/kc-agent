"""Loads and queries the knowledge-model and identity tables.

Every public method here answers a question the Change API asks before it
appends an event (FR-7.8). Nothing in this module reads the database, so it is
cheap to unit-test against the acceptance tests in §18.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONFIG_DIR = Path(__file__).parent

VisibilityClass = Literal["public", "topology", "restricted"]


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeTypeRule(_Strict):
    visibility: VisibilityClass
    ttl_days: int
    visibility_status: Literal["provisional"] | None = None
    ttl_status: Literal["provisional"] | None = None


class SubtypeRule(_Strict):
    visibility: VisibilityClass


class TopologyWhitelist(_Strict):
    fields: tuple[str, ...]
    edges: tuple[str, ...]


class EdgePair(_Strict):
    from_: str = Field(alias="from")
    to: str


class EdgeRule(_Strict):
    cardinality: Cardinality
    from_: tuple[str, ...] = Field(default=(), alias="from")
    to: tuple[str, ...] = ()
    pairs: tuple[EdgePair, ...] = ()
    same_type_only: bool = False

    def permits(self, from_type: str, to_type: str) -> bool:
        if self.pairs:
            if not any(p.from_ == from_type and p.to == to_type for p in self.pairs):
                return False
        else:
            if from_type not in self.from_ or to_type not in self.to:
                return False
        if self.same_type_only and from_type != to_type:
            return False
        return True


class PropertyRule(_Strict):
    cardinality: Literal["single", "multi"]
    kind: Literal["label", "enum", "timestamp", "number"]
    max_length: int | None = None


class Authority(_Strict):
    default_order: dict[str, int]
    overrides: dict[str, dict[str, int]] = {}
    human_confirmed_rank: int


class KnowledgeSchema(_Strict):
    version: int
    visibility_classes: tuple[VisibilityClass, ...]
    topology_whitelist: TopologyWhitelist
    node_types: dict[str, NodeTypeRule]
    document_subtypes: dict[str, SubtypeRule]
    embedded_document_subtypes: tuple[str, ...]
    edges: dict[str, EdgeRule]
    properties: dict[str, dict[str, PropertyRule]]
    indexed_properties: tuple[str, ...]
    authority: Authority

    # --- FR-4.9 -------------------------------------------------------------

    def edge_permitted(self, edge_type: str, from_type: str, to_type: str) -> bool:
        """True when this edge type may connect these node types (FR-4.9)."""
        rule = self.edges.get(edge_type)
        return bool(rule and rule.permits(from_type, to_type))

    def edge_cardinality(self, edge_type: str) -> Cardinality:
        return self.edges[edge_type].cardinality

    def property_rule(self, node_type: str, prop: str) -> PropertyRule | None:
        """The rule for a property, or None when it is off-schema (AT-17)."""
        return self.properties.get(node_type, {}).get(prop) or self.properties["*"].get(prop)

    def is_single_valued(self, node_type: str, prop: str) -> bool:
        """Single-valued properties are the ones that can be contradicted (FR-6.11)."""
        rule = self.property_rule(node_type, prop)
        if rule is None:
            raise KeyError(f"off-schema property {node_type}.{prop}")
        return rule.cardinality == "single"

    # --- FR-5.2 -------------------------------------------------------------

    def default_visibility(self, node_type: str, subtype: str | None = None) -> VisibilityClass:
        """Default visibility class, subtype overriding node type (FR-5.2)."""
        if subtype is not None:
            if node_type != "Document":
                raise ValueError(f"{node_type} has no subtypes")
            return self.document_subtypes[subtype].visibility
        return self.node_types[node_type].visibility

    def topology_fields(self) -> tuple[str, ...]:
        return self.topology_whitelist.fields

    # --- FR-14.1 ------------------------------------------------------------

    def ttl_days(self, node_type: str) -> int:
        return self.node_types[node_type].ttl_days

    # --- FR-6.8 -------------------------------------------------------------

    def is_embedded(self, node_type: str, subtype: str | None) -> bool:
        return node_type == "Document" and subtype in self.embedded_document_subtypes

    # --- FR-6.13 ------------------------------------------------------------

    def authority_rank(
        self,
        source_system: str,
        node_type: str,
        prop: str | None = None,
        provenance_class: str = "source-derived",
    ) -> int:
        """Authority of a claim, per (node type, property) then default order.

        `human-confirmed` outranks every source. Whether it outranks a
        particular claim also depends on valid_from, which the caller compares
        (FR-6.13); this returns the rank only.
        """
        if provenance_class == "human-confirmed":
            return self.authority.human_confirmed_rank
        if prop is not None:
            override = self.authority.overrides.get(f"{node_type}.{prop}")
            if override and source_system in override:
                return override[source_system]
        return self.authority.default_order.get(source_system, 0)


class IdentityRule(_Strict):
    description: str
    tier_gated: bool
    dry_run_applies: bool
    may: tuple[str, ...]
    constraints: dict[str, object] = {}


class IdentityTable(_Strict):
    version: int
    operations: tuple[str, ...]
    identities: dict[str, IdentityRule]
    denied: tuple[str, ...]

    def may(self, identity: str, operation: str) -> bool:
        """True when this write identity may append this operation (FR-7.2).

        Unknown identities are denied: there is no default-allow (AT-4).
        """
        rule = self.identities.get(identity)
        return bool(rule and operation in rule.may)

    def is_tier_gated(self, identity: str) -> bool:
        rule = self.identities.get(identity)
        return bool(rule and rule.tier_gated)

    def dry_run_applies(self, identity: str) -> bool:
        rule = self.identities.get(identity)
        return bool(rule and rule.dry_run_applies)

    def constraint(self, identity: str, name: str) -> object | None:
        rule = self.identities.get(identity)
        return rule.constraints.get(name) if rule else None


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@functools.cache
def load_schema(path: Path | None = None) -> KnowledgeSchema:
    return KnowledgeSchema.model_validate(_read_yaml(path or CONFIG_DIR / "schema.yaml"))


@functools.cache
def load_identities(path: Path | None = None) -> IdentityTable:
    return IdentityTable.model_validate(_read_yaml(path or CONFIG_DIR / "identities.yaml"))
