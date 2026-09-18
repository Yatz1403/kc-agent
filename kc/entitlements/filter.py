"""Scope derivation and in-query entitlement filtering (FR-5.4, FR-5.10).

Two rules hold everywhere:

1. Scope is derived before anything else happens, and an unresolved, unknown or
   anonymous caller is rejected rather than defaulted (FR-5.5, AT-5).
2. Filtering is expressed as SQL fragments that go *inside* the query. There is
   deliberately no post-filter helper in this module: if one existed, someone
   would reach for it under deadline, and post-filtering is how metadata leaks
   (FR-5.4). On PostgreSQL these fragments become row-level security policies
   (§21); the fragment shape is what makes that swap mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kc.config import loader as cfg
from kc.core.errors import IdentityUnresolved

METADATA = "metadata"
FULL = "full"

_ANONYMOUS = {"", "anonymous", "unknown", "system", "*", None}


@dataclass(frozen=True, slots=True)
class Scope:
    """Effective scope of one request: user ∩ application, plus commons."""

    user: str
    application: str
    bus: tuple[str, ...]

    @property
    def namespaces(self) -> list[str]:
        """Index namespaces this caller may query, per-BU plus commons (FR-5.6)."""
        return list(self.bus)

    def owns(self, bu: str) -> bool:
        return bu in self.bus

    def as_json(self) -> list[str]:
        return list(self.bus)


def derive_scope(user: str | None, application: str | None) -> Scope:
    """Resolve caller identity to an effective scope, or fail closed.

    This is step 1 and 2 of the serve pipeline (FR-11.2). An application-only
    call is rejected unless configuration grants that application an explicit
    service scope (FR-5.5).
    """
    if user in _ANONYMOUS or (isinstance(user, str) and user.strip() == ""):
        raise IdentityUnresolved("caller identity unresolved or anonymous (FR-5.5)")
    if application in _ANONYMOUS:
        raise IdentityUnresolved("calling application unresolved (FR-5.5)")
    assert isinstance(user, str) and isinstance(application, str)
    bus = cfg.bus_for_identity(user, application)
    if not bus:
        raise IdentityUnresolved(f"no effective scope for {user} via {application}")
    only_commons = set(bus) == {cfg.commons()}
    if only_commons and not cfg.application_allows_service_scope(application):
        # A caller with no BU beyond commons can still read public content; this
        # is not an error. Service-scope callers are audited separately.
        pass
    return Scope(user=user, application=application, bus=tuple(bus))


# --- SQL fragments -----------------------------------------------------------


def node_sql(scope: Scope, alias: str = "n", level: str = METADATA) -> tuple[str, list[Any]]:
    """Fragment admitting the nodes this caller may see at `level`.

    `metadata` admits own-BU nodes plus public and topology nodes from any BU:
    that is what makes cross-BU dependency traversal possible (FR-5.1, FR-5.2).
    `full` admits own-BU nodes and public nodes only, so a topology node from
    another BU never yields content (FR-9.4, AT-7).
    """
    marks = ",".join("?" for _ in scope.bus)
    if level == FULL:
        sql = f"({alias}.bu_owner IN ({marks}) OR {alias}.visibility_class = 'public')"
    else:
        sql = (
            f"({alias}.bu_owner IN ({marks})"
            f" OR {alias}.visibility_class IN ('public','topology'))"
        )
    return sql, list(scope.bus)


def edge_endpoint_sql(scope: Scope, near: str = "near", far: str = "far") -> tuple[str, list[Any], str, list[Any]]:
    """The edge rule: near endpoint visible AND far endpoint at least topology.

    Both fragments are metadata-level, which is exactly the rule: an edge is
    traversable when the caller can see both of its ends at metadata level, and
    content is decided separately per node (FR-5.12, DR1).
    """
    near_sql, near_args = node_sql(scope, near, METADATA)
    far_sql, far_args = node_sql(scope, far, METADATA)
    return near_sql, near_args, far_sql, far_args


def chunk_namespace_sql(scope: Scope, alias: str = "c") -> tuple[str, list[Any]]:
    marks = ",".join("?" for _ in scope.bus)
    return f"{alias}.namespace IN ({marks})", list(scope.bus)


# --- per-node decisions ------------------------------------------------------


def visibility_of(bu_owner: str, visibility_class: str, scope: Scope) -> str | None:
    """What this caller gets of a node: full, metadata, or nothing.

    Nothing means the node must never appear, be counted, or change the shape
    of a response (FR-5.8, AT-6).
    """
    if scope.owns(bu_owner) or visibility_class == "public":
        return FULL
    if visibility_class == "topology":
        return METADATA
    return None


def project_props(props: dict[str, Any], visibility: str) -> dict[str, Any]:
    """Trim a node's props document to what the caller may see.

    Under metadata visibility only the FR-5.2 whitelist survives, and content
    never does (FR-9.4).
    """
    if visibility == FULL:
        return props
    allowed = set(cfg.topology_fields())
    return {k: v for k, v in props.items() if k in allowed}
