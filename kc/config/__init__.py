"""Executable configuration for the knowledge model and write identities.

The Change API validator reads these tables rather than restating them in code
(docs/kc-agent-frs.md v0.3, FR-4.9, FR-5.2, FR-6.13, FR-7.2, FR-14.1, NFR-4).
"""

from kc.config.loader import (
    Cardinality,
    EdgeRule,
    IdentityRule,
    IdentityTable,
    KnowledgeSchema,
    PropertyRule,
    load_identities,
    load_schema,
)

__all__ = [
    "Cardinality",
    "EdgeRule",
    "IdentityRule",
    "IdentityTable",
    "KnowledgeSchema",
    "PropertyRule",
    "load_identities",
    "load_schema",
]
