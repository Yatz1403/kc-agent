"""The only module that reads kc/config/*.json.

Callers ask questions; they never see the files. Adding a caller that opens a
config file directly defeats NFR-4, because the shape of the table then leaks
into code that nobody re-reads when the table changes.

Requirements: FR-4.9 (edge and property rules), FR-5.2 (visibility defaults),
FR-5.3 and FR-5.10 (BU mappings and scope), FR-6.8 (embedding classes),
FR-6.13 (authority), FR-7.2 and FR-7.3 (identities and tiers), FR-11.4
(traversal budgets), FR-14.1 (TTLs).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent
WILDCARD = "*"


@functools.cache
def _table(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / f"{name}.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def reload() -> None:
    """Drop cached tables. Tests use this after writing a temporary config."""
    _table.cache_clear()


# --- FR-4.1, FR-4.9: node, edge and property rules ---------------------------


def node_types() -> list[str]:
    return [t for t in _table("schema")["node_types"]]


def edge_types() -> list[str]:
    return [t for t in _table("schema")["edges"]]


def is_node_type(node_type: str) -> bool:
    return node_type in _table("schema")["node_types"]


def document_subtypes() -> list[str]:
    return list(_table("schema")["document_subtypes"])


def edge_permitted(edge_type: str, from_type: str, to_type: str) -> bool:
    """True when this edge type may connect these node types (FR-4.9, AT-17)."""
    rule = _table("schema")["edges"].get(edge_type)
    if rule is None:
        return False
    if "pairs" in rule:
        if [from_type, to_type] not in [list(p) for p in rule["pairs"]]:
            return False
    elif from_type not in rule.get("from", ()) or to_type not in rule.get("to", ()):
        return False
    if rule.get("same_type_only") and from_type != to_type:
        return False
    return True


def edge_cardinality(edge_type: str) -> str:
    """'one' means a second open claim with a different target is a contradiction."""
    return _table("schema")["edges"][edge_type]["cardinality"]


def property_rule(node_type: str, prop: str) -> dict[str, Any] | None:
    props = _table("schema")["properties"]
    return props.get(node_type, {}).get(prop) or props[WILDCARD].get(prop)


def is_single_valued(node_type: str, prop: str) -> bool:
    rule = property_rule(node_type, prop)
    if rule is None:
        raise KeyError(f"off-schema property {node_type}.{prop}")
    return rule["cardinality"] == "single"


def indexed_properties() -> list[str]:
    """Properties worth an index; the rest live unindexed in the props document."""
    return list(_table("schema")["indexed_properties"])


# --- FR-5.2: visibility ------------------------------------------------------


def default_visibility(node_type: str, subtype: str | None = None) -> str:
    if subtype:
        if node_type != "Document":
            raise ValueError(f"{node_type} has no subtypes")
        return _table("schema")["document_subtypes"][subtype]["visibility"]
    return _table("schema")["node_types"][node_type]["visibility"]


def topology_fields() -> list[str]:
    return list(_table("schema")["topology_whitelist"]["fields"])


def topology_edges() -> list[str]:
    return list(_table("schema")["topology_whitelist"]["edges"])


# --- FR-6.8, FR-14.1 ---------------------------------------------------------


def is_embedded(node_type: str, subtype: str | None) -> bool:
    return node_type == "Document" and subtype in _table("schema")["embedded_document_subtypes"]


def ttl_days(node_type: str) -> int:
    return _table("schema")["node_types"][node_type]["ttl_days"]


# --- FR-6.13: authority ------------------------------------------------------


def authority_rank(
    source_system: str,
    node_type: str,
    prop: str | None = None,
    provenance_class: str = "source-derived",
) -> int:
    """Rank of a claim. Whether human-confirmed actually wins also depends on
    valid_from, which the caller compares (FR-6.13)."""
    auth = _table("schema")["authority"]
    if provenance_class == "human-confirmed":
        return int(auth["human_confirmed_rank"])
    rank = 0
    if prop:
        override = auth["overrides"].get(f"{node_type}.{prop}")
        if override and source_system in override:
            rank = int(override[source_system])
    if not rank:
        rank = int(auth["default_order"].get(source_system, 0))
    if provenance_class == "agent-derived":
        rank = max(0, rank - int(auth.get("agent_penalty", 0)))
    return rank


# --- FR-7.2, FR-7.3: write identities and tiers ------------------------------


def may(identity: str, operation: str) -> bool:
    """Unknown identities are denied; there is no fall-through (AT-4)."""
    rule = _table("identities")["identities"].get(identity)
    return bool(rule and operation in rule["may"])


def is_tier_gated(identity: str) -> bool:
    rule = _table("identities")["identities"].get(identity)
    return bool(rule and rule["tier_gated"])


def dry_run_applies(identity: str) -> bool:
    rule = _table("identities")["identities"].get(identity)
    return bool(rule and rule["dry_run_applies"])


def identity_constraint(identity: str, name: str) -> Any:
    rule = _table("identities")["identities"].get(identity)
    return rule["constraints"].get(name) if rule else None


def known_operations() -> list[str]:
    return list(_table("identities")["operations"])


def denied_identities() -> list[str]:
    return list(_table("identities")["denied"])


def tier_for(operation: str, cross_bu: bool = False) -> str:
    tiers = _table("identities")["tiers"]
    if cross_bu:
        return tiers["cross_bu_edge"]
    return tiers.get(operation, "T3")


def tier_policy() -> dict[str, Any]:
    return dict(_table("identities")["tier_policy"])


def approval_policy() -> dict[str, Any]:
    return dict(_table("identities")["approval"])


# --- FR-5.3, FR-5.10: BU mappings and caller scope ---------------------------


def commons() -> str:
    return _table("entitlements")["commons"]


def business_units() -> list[str]:
    return list(_table("entitlements")["business_units"])


def bus_for_identity(user: str, application: str | None = None) -> list[str]:
    """Effective scope: user scope ∩ application scope, plus commons (FR-5.10).

    Returns [] for an unknown or anonymous user so callers fail closed (FR-5.5).
    """
    ent = _table("entitlements")
    groups = ent["users"].get(user)
    if not groups:
        return []
    user_bus = {bu for g in groups for bu in ent["idp_groups"].get(g, [])}
    if application is not None:
        app = ent["applications"].get(application)
        if not app:
            return []
        user_bus &= set(app["scope"])
    user_bus.add(ent["commons"])
    return sorted(user_bus)


def application_allows_service_scope(application: str) -> bool:
    app = _table("entitlements")["applications"].get(application)
    return bool(app and app.get("service_scope_allowed"))


def bu_for_source(source_system: str, container: str) -> str | None:
    """BU of a source container, or None when unmapped (quarantine, FR-5.3)."""
    return _table("entitlements")["source_containers"].get(source_system, {}).get(container)


def team_bu(team_id: str) -> str | None:
    team = _table("entitlements")["teams"].get(team_id)
    return team["bu"] if team else None


def team_manager(team_id: str) -> str | None:
    """Approval expiry escalates to the owning team's manager (FR-10.5)."""
    team = _table("entitlements")["teams"].get(team_id)
    return team["manager"] if team else None


# --- FR-11.2, FR-11.4, FR-11.6: serve-path shapes ----------------------------


def intents() -> list[str]:
    return list(_table("retrieval")["intents"])


def intent_budget(intent: str) -> dict[str, Any]:
    budget = _table("retrieval")["intents"].get(intent)
    if budget is None:
        raise KeyError(f"unknown intent {intent}")
    return dict(budget)


def edge_criticality(edge_type: str) -> float:
    return float(_table("retrieval")["edge_criticality"].get(edge_type, 0.1))


def retrieval_setting(*path: str) -> Any:
    value: Any = _table("retrieval")
    for key in path:
        value = value[key]
    return value


def health_target(metric: str) -> float:
    return float(_table("retrieval")["health_targets"][metric])


def guardrail(name: str) -> float:
    return float(_table("retrieval")["guardrails"][name])
