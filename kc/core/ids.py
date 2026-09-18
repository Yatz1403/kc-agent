"""Deterministic identifiers (FR-4.10).

Replaying the log, or re-extracting a source object, must not create
duplicates, so every ID is a hash of the thing it names rather than a counter
or a UUID. `source_key` is the full natural key of the source object; it is
stored alongside the node so that a collision — the same ID arriving from a
different key — is detectable and raised rather than merged (FR-4.11,
IdCollision).
"""

from __future__ import annotations

import hashlib
import uuid

_DIGEST_CHARS = 20


def _hash(*parts: str) -> str:
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def source_key(source_system: str, source_instance: str, source_id: str) -> str:
    """The natural key of a source object, stable across re-ingestion."""
    return f"{source_system}:{source_instance}:{source_id}"


def node_id(node_type: str, source_key_value: str, discriminator: str = "") -> str:
    """A node's ID. `discriminator` separates several entities extracted from
    one source object, e.g. each endpoint of one OpenAPI document."""
    return "n_" + _hash("node", node_type, source_key_value, discriminator)


def edge_id(from_id: str, to_id: str, edge_type: str) -> str:
    """One edge per (from, to, type): parallel assertions of the same edge from
    different sources aggregate onto it (FR-4.7 item 5)."""
    return "e_" + _hash("edge", from_id, to_id, edge_type)


def claim_id(
    kind: str,
    subject_id: str,
    prop: str | None,
    source_key_value: str,
    valid_from: str,
) -> str:
    """A claim's ID. Same assertion, same source object, same valid_from is the
    same claim, so a replayed event is idempotent (FR-7.9)."""
    return "c_" + _hash("claim", kind, subject_id, prop or "", source_key_value, valid_from)


def chunk_id(node_id_value: str, section_path: str, version_hash: str) -> str:
    return "k_" + _hash("chunk", node_id_value, section_path, version_hash)


def event_id(identity: str, op: str, body_digest: str) -> str:
    """Identifier for one log append.

    Unlike node and claim IDs this is deliberately *not* content-derived: two
    genuinely separate appends can carry identical bodies in the same second,
    and the log must record both. Idempotency comes from deterministic claim
    IDs plus per-projector high-water marks (FR-7.9), not from this value.
    """
    return "ev_" + _hash("event", identity, op, body_digest, uuid.uuid4().hex)


def content_hash(text: str) -> str:
    """Hash of a normalised projection of source content (FR-6.2)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(parts: dict[str, object]) -> str:
    """Serve cache key (FR-11.6). The cache epoch and the effective scope are
    part of `parts`, so one caller's entry can never serve another's."""
    flat = "\x1f".join(f"{k}={parts[k]!r}" for k in sorted(parts))
    return "q_" + _hash("cache", flat)
