"""The connector seam (FR-6.1 to FR-6.6, FR-5.9).

Every connector is a documented seam, not a stub that silently returns nothing.
`discover`, `fetch` and `object_permissions_narrower_than_bu` raise
NotImplementedError with a TODO(office) naming the exact API call to wire, so a
missing credential is a loud failure with an address, not an empty result set.

Two methods are implemented now, everywhere, because they are pure functions
over content and they decide whether the rest works at all:

* `hash_projection` — the normalised projection change detection hashes. If
  this keeps volatile fields, every webhook looks like a change and nothing is
  ever dropped (FR-6.2, N1).
* `extract` — pure over fetched content, so parsers are testable without
  credentials. Git implements it fully; the others raise until wired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from kc.core import ids
from kc.core.model import Claim

# Separator between the metadata projection and the artefact body in a hash.
_SEP = chr(31)  # unit separator: never appears in source content


@dataclass(slots=True)
class SourceObject:
    """One fetched source object, before normalisation."""

    source_system: str
    source_instance: str
    source_id: str
    container: str
    content: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    deleted: bool = False

    @property
    def source_key(self) -> str:
        return ids.source_key(self.source_system, self.source_instance, self.source_id)


@dataclass(slots=True)
class Extraction:
    """What a parser produced: claims plus chunks plus unresolved mentions.

    `mentions` exist so prose extraction can link without creating: an
    unresolved mention becomes a Resolver candidate, never a node (FR-6.15).
    """

    claims: list[Claim] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    mentions: list[dict[str, Any]] = field(default_factory=list)
    llm_used: bool = False


class Connector:
    """Base class. `creates_nodes = False` means extraction may only link to
    existing anchor nodes and create Document nodes (FR-6.15)."""

    source_system = "override-me"
    # Environment variable holding this system's API base URL. `kc.cli doctor`
    # reports it as configured-and-reachable, configured-and-blocked, or unset;
    # the skeleton itself needs no network.
    api_base_env = ""
    creates_nodes = True
    creatable_types: tuple[str, ...] = ()
    volatile_fields: tuple[str, ...] = ()
    extraction_version = "1"

    def __init__(self, instance: str = "default") -> None:
        self.instance = instance

    # --- to wire on the enterprise laptop ------------------------------------

    def discover(self, since: str | None = None) -> list[str]:
        """Source IDs changed since `since`, for the reconciliation sweep."""
        raise NotImplementedError(
            f"TODO(office): {self.source_system}.discover — see subclass docstring"
        )

    def fetch(self, source_id: str) -> SourceObject:
        """Fetch one object in full (FR-6.2 step 1)."""
        raise NotImplementedError(
            f"TODO(office): {self.source_system}.fetch — see subclass docstring"
        )

    def object_permissions_narrower_than_bu(self, obj: SourceObject) -> bool:
        """Object-level permission check (FR-5.9, D5).

        Container-level permissions are deliberately ignored: narrower-than-BU
        spaces and projects are the normal case, and checking them would skip
        most of the corpus. Only object-level restrictions cause a skip.
        """
        raise NotImplementedError(
            f"TODO(office): {self.source_system}.object_permissions_narrower_than_bu"
        )

    # --- implemented now -----------------------------------------------------

    def hash_projection(self, obj: SourceObject) -> str:
        """Hash of the extraction-relevant projection of an object (FR-6.2).

        Volatile fields are dropped before hashing. This is the difference
        between hash-diff dropping replays and never dropping anything: Jira
        touches `updated`, ranks and watcher lists on almost every write.

        The invariant, in both directions: a change to anything `extract` reads
        MUST change this hash, and a change to a declared volatile field MUST
        NOT. Getting the first direction wrong fails silently — content
        changes, the projection does not, the event is dropped, nothing is
        closed, and ingestion reports healthy forever — so it is enforced by a
        derived test over `self_check_samples`, not by review.
        """
        projected = _strip(obj.content, self.volatile_fields)
        # The body counts. Git keeps the artefact in `raw`, so hashing the
        # metadata dict alone would make a changed OpenAPI spec look unchanged.
        return ids.content_hash(
            json.dumps(projected, sort_keys=True, default=str) + _SEP + (obj.raw or "")
        )

    def extract(self, obj: SourceObject, bu: str) -> Extraction:
        raise NotImplementedError(
            f"TODO(office): {self.source_system}.extract — see subclass docstring"
        )

    def self_check_samples(self) -> list[SourceObject]:
        """Representative payloads for the hash-projection invariant.

        Not test fixtures bolted on from outside: the invariant test enumerates
        the registry and fails for any connector that ships without samples, so
        a new connector cannot reach the log without declaring what its content
        looks like. `kc.cli doctor` uses the same samples to prove extraction
        works offline.

        The invariant they support (FR-6.2): if a mutation changes anything
        `extract` reads, `hash_projection` MUST change. The failure mode is
        silent — content changes, the projection does not, the event is dropped,
        nothing is ever closed, and it looks like ingestion is working.
        """
        raise NotImplementedError(
            f"TODO: {self.source_system}.self_check_samples — see base class docstring"
        )

    # --- helpers for subclasses ---------------------------------------------

    def identity_claim(
        self,
        *,
        node_id: str,
        node_type: str,
        bu: str,
        obj: SourceObject,
        valid_from: str,
        subtype: str | None = None,
        confidence: float = 0.8,
    ) -> Claim:
        return Claim(
            kind="identity",
            subject_id=node_id,
            node_type=node_type,
            subtype=subtype,
            bu_owner=bu,
            source_system=obj.source_system,
            source_instance=obj.source_instance,
            source_id=obj.source_id,
            valid_from=valid_from,
            confidence=confidence,
            extraction_version=self.extraction_version,
        )

    def property_claim(
        self,
        *,
        node_id: str,
        node_type: str,
        prop: str,
        value: Any,
        bu: str,
        obj: SourceObject,
        valid_from: str,
        confidence: float = 0.8,
    ) -> Claim:
        return Claim(
            kind="property",
            subject_id=node_id,
            node_type=node_type,
            prop=prop,
            value=value,
            bu_owner=bu,
            source_system=obj.source_system,
            source_instance=obj.source_instance,
            source_id=obj.source_id,
            valid_from=valid_from,
            confidence=confidence,
            extraction_version=self.extraction_version,
        )

    def edge_claim(
        self,
        *,
        from_id: str,
        to_id: str,
        edge_type: str,
        bu: str,
        obj: SourceObject,
        valid_from: str,
        confidence: float = 0.8,
    ) -> Claim:
        return Claim(
            kind="relationship",
            subject_id=ids.edge_id(from_id, to_id, edge_type),
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            bu_owner=bu,
            source_system=obj.source_system,
            source_instance=obj.source_instance,
            source_id=obj.source_id,
            valid_from=valid_from,
            confidence=confidence,
            extraction_version=self.extraction_version,
        )


def _strip(value: Any, volatile: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip(v, volatile) for k, v in sorted(value.items()) if k not in volatile
        }
    if isinstance(value, list):
        return [_strip(v, volatile) for v in value]
    return value
