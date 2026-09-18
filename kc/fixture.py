"""Synthetic BU-A / BU-B fixture (§17, FR-5.4).

Built from Phase 1 on purpose: with one BU, AT-6, AT-7, AT-11 and the red-team
suite have nothing to test against, and retrofitting entitlement enforcement is
the most expensive change in this design.

What the fixture deliberately contains:

* the same name — "ledger" — in BU-A (topology) and BU-B (restricted), so a
  disambiguation that reveals the BU-B node fails a test rather than a customer
  (AT-6, AT-19);
* a cross-BU DEPENDS_ON from BU-B's recon-engine to BU-A's ledger-api, asserted
  by two independent sources, so the topology exception and edge corroboration
  are exercised from day one (FR-4.7 item 5, FR-5.12);
* restricted requirements on both sides, a public standard in commons, and a
  test case with COVERS and VALIDATES so `test_planning` has something to find.
"""

from __future__ import annotations

from typing import Any

from kc.core import ids
from kc.core.model import Claim
from kc.ingest import FastPath
from kc.stores.sqlite_store import Store

WHEN = "2026-01-06T09:00:00+00:00"


def _claim(**kwargs: Any) -> Claim:
    kwargs.setdefault("valid_from", WHEN)
    kwargs.setdefault("confidence", 0.9)
    return Claim(**kwargs)


def _node(
    node_type: str,
    key: str,
    name: str,
    bu: str,
    source_system: str,
    *,
    status: str = "active",
    subtype: str | None = None,
    extra: dict[str, Any] | None = None,
    confidence: float = 0.9,
) -> tuple[str, list[Claim]]:
    source_key = ids.source_key(source_system, "fixture", key)
    node_id = ids.node_id(node_type, source_key)
    common = {
        "bu_owner": bu,
        "source_system": source_system,
        "source_instance": "fixture",
        "source_id": key,
        "node_type": node_type,
        "confidence": confidence,
    }
    claims = [
        _claim(kind="identity", subject_id=node_id, subtype=subtype, **common),
        _claim(kind="property", subject_id=node_id, prop="name", value=name, **common),
        _claim(kind="property", subject_id=node_id, prop="status", value=status, **common),
    ]
    for prop, value in (extra or {}).items():
        claims.append(
            _claim(kind="property", subject_id=node_id, prop=prop, value=value, **common)
        )
    return node_id, claims


def _edge(
    from_id: str,
    to_id: str,
    edge_type: str,
    bu: str,
    source_system: str,
    source_id: str,
    confidence: float = 0.9,
) -> Claim:
    return _claim(
        kind="relationship",
        subject_id=ids.edge_id(from_id, to_id, edge_type),
        from_id=from_id,
        to_id=to_id,
        edge_type=edge_type,
        bu_owner=bu,
        source_system=source_system,
        source_instance="fixture",
        source_id=source_id,
        confidence=confidence,
    )


def build(store: Store) -> dict[str, Any]:
    """Load the fixture. Idempotent: deterministic IDs mean a second run
    re-asserts the same claims rather than duplicating them (FR-4.10)."""
    fast = FastPath(store)
    claims: list[Claim] = []
    chunks: list[dict[str, Any]] = []

    # --- commons -------------------------------------------------------------
    group_id, group_claims = _node(
        "BusinessGroup", "acme-group", "Acme Group", "commons", "confluence"
    )
    standard_id, standard_claims = _node(
        "Document", "ARCH-1", "API Standards", "commons", "confluence",
        subtype="standard", extra={"url": "https://wiki/ARCH-1"},
    )
    claims += group_claims + standard_claims
    chunks.append(
        {
            "node_id": standard_id,
            "bu_owner": "commons",
            "kind": "doc",
            "section_path": "API Standards/Versioning",
            "content": (
                "API Standards / Versioning. Every public API must carry a major version in "
                "its base path. Breaking changes require a new major version and a six month "
                "overlap. Ledger and reconciliation services follow this standard."
            ),
        }
    )

    # --- BU-A ----------------------------------------------------------------
    team_a_id, team_a_claims = _node("Team", "team-ledger", "Team Ledger", "bu-a", "jira")
    svc_a_id, svc_a_claims = _node(
        "Service", "acme/ledger-api", "ledger", "bu-a", "git"
    )
    api_id, api_claims = _node(
        "API", "acme/ledger-api:openapi.json", "ledger-api", "bu-a", "git",
        extra={"version": "1.4.0", "base_path": "/v1"}, confidence=0.95,
    )
    ep_get_id, ep_get_claims = _node(
        "Endpoint", "acme/ledger-api:GET /accounts/{id}/entries",
        "GET /accounts/{id}/entries", "bu-a", "git",
        extra={"method": "GET", "path": "/accounts/{id}/entries"}, confidence=0.95,
    )
    ep_post_id, ep_post_claims = _node(
        "Endpoint", "acme/ledger-api:POST /entries", "POST /entries", "bu-a", "git",
        extra={"method": "POST", "path": "/entries"}, confidence=0.95,
    )
    topic_id, topic_claims = _node(
        "Topic", "ledger.entries.v1", "ledger.entries.v1", "bu-a", "git",
        extra={"broker": "kafka-prod"},
    )
    req_a_id, req_a_claims = _node(
        "Requirement", "LED-101", "Partial refunds post to the ledger within one hour",
        "bu-a", "jira", status="In Progress", extra={"key": "LED-101", "priority": "High"},
    )
    test_a_id, test_a_claims = _node(
        "TestCase", "acme/ledger-api:tests/test_entries.py::test_partial_refund_posts",
        "test_partial_refund_posts", "bu-a", "git",
        extra={"framework": "pytest", "file_path": "tests/test_entries.py"}, confidence=0.95,
    )
    adr_id, adr_claims = _node(
        "Document", "LEDGER-42", "ADR-7 Ledger entries are append-only", "bu-a",
        "confluence", subtype="adr", extra={"url": "https://wiki/LEDGER-42"},
    )
    comp_id, comp_claims = _node(
        "Component", "pypi:fastapi", "fastapi", "bu-a", "git",
        extra={"ecosystem": "pypi", "version_spec": ">=0.115"},
    )

    claims += (
        team_a_claims + svc_a_claims + api_claims + ep_get_claims + ep_post_claims
        + topic_claims + req_a_claims + test_a_claims + adr_claims + comp_claims
    )
    claims += [
        _edge(svc_a_id, api_id, "IMPLEMENTS", "bu-a", "git", "acme/ledger-api", 0.95),
        _edge(ep_get_id, api_id, "PART_OF", "bu-a", "git", "acme/ledger-api", 0.95),
        _edge(ep_post_id, api_id, "PART_OF", "bu-a", "git", "acme/ledger-api", 0.95),
        _edge(svc_a_id, team_a_id, "OWNED_BY", "bu-a", "jira", "LED"),
        _edge(api_id, team_a_id, "OWNED_BY", "bu-a", "jira", "LED"),
        _edge(svc_a_id, topic_id, "PUBLISHES_TO", "bu-a", "git", "acme/ledger-api"),
        _edge(svc_a_id, comp_id, "DEPENDS_ON", "bu-a", "git", "acme/ledger-api"),
        _edge(svc_a_id, req_a_id, "IMPLEMENTS", "bu-a", "jira", "LED-101", 0.8),
        _edge(test_a_id, ep_get_id, "COVERS", "bu-a", "git", "tests/test_entries.py", 0.95),
        _edge(test_a_id, req_a_id, "VALIDATES", "bu-a", "jira", "LED-101"),
        _edge(adr_id, svc_a_id, "DOCUMENTS", "bu-a", "confluence", "LEDGER-42", 0.7),
        _edge(team_a_id, group_id, "MEMBER_OF", "bu-a", "jira", "LED"),
    ]
    chunks += [
        {
            "node_id": ep_get_id,
            "bu_owner": "bu-a",
            "kind": "openapi",
            "section_path": "GET /accounts/{id}/entries",
            "content": (
                "GET /accounts/{id}/entries. Returns ledger entries for an account, newest "
                "first, paginated. Supports filtering by posted_at and entry_type. Partial "
                "refund entries appear as type REFUND_PARTIAL."
            ),
        },
        {
            "node_id": ep_post_id,
            "bu_owner": "bu-a",
            "kind": "openapi",
            "section_path": "POST /entries",
            "content": (
                "POST /entries. Posts a ledger entry. Idempotent on client_reference. "
                "Rejects entries whose amount exceeds the account limit."
            ),
        },
        {
            "node_id": req_a_id,
            "bu_owner": "bu-a",
            "kind": "requirement",
            "section_path": "description",
            "content": (
                "LED-101 Partial refunds post to the ledger within one hour. Acceptance: a "
                "partial refund raised in the claims system appears as a REFUND_PARTIAL entry "
                "on the account ledger within sixty minutes, and the reconciliation feed picks "
                "it up the same day."
            ),
        },
        {
            "node_id": test_a_id,
            "bu_owner": "bu-a",
            "kind": "test",
            "section_path": "tests/test_entries.py::test_partial_refund_posts",
            "content": (
                "def test_partial_refund_posts(client):\n"
                "    # LED-101\n"
                "    response = client.get('/accounts/12345/entries')\n"
                "    assert any(e['type'] == 'REFUND_PARTIAL' for e in response.json())"
            ),
        },
        {
            "node_id": adr_id,
            "bu_owner": "bu-a",
            "kind": "adr",
            "section_path": "ADR-7/Decision",
            "content": (
                "ADR-7 Ledger entries are append-only. Decision: corrections are posted as "
                "compensating entries, never as updates. Consequence: the reconciliation "
                "engine must treat the entry stream as immutable."
            ),
        },
    ]

    # --- BU-B ----------------------------------------------------------------
    team_b_id, team_b_claims = _node("Team", "team-recon", "Team Recon", "bu-b", "jira")
    # Same name as BU-A's service, and restricted: a caller in BU-A must never
    # learn it exists, not even through a disambiguation (AT-6, AT-19).
    svc_b_secret_id, svc_b_secret_claims = _node(
        "Service", "acme/recon-ledger-shadow", "ledger", "bu-b", "git",
    )
    svc_b_id, svc_b_claims = _node(
        "Service", "acme/recon-engine", "recon-engine", "bu-b", "git",
    )
    req_b_id, req_b_claims = _node(
        "Requirement", "REC-55", "Daily reconciliation completes before 06:00", "bu-b",
        "jira", status="Done", extra={"key": "REC-55", "priority": "High"},
    )
    claims += team_b_claims + svc_b_secret_claims + svc_b_claims + req_b_claims
    claims += [
        _edge(svc_b_id, team_b_id, "OWNED_BY", "bu-b", "jira", "REC"),
        _edge(svc_b_secret_id, team_b_id, "OWNED_BY", "bu-b", "jira", "REC"),
        _edge(svc_b_id, req_b_id, "IMPLEMENTS", "bu-b", "jira", "REC-55", 0.8),
        _edge(team_b_id, group_id, "MEMBER_OF", "bu-b", "jira", "REC"),
        _edge(svc_b_id, topic_id, "CONSUMES_FROM", "bu-b", "git", "acme/recon-engine"),
        # The cross-BU dependency, asserted twice by independent sources.
        _edge(svc_b_id, api_id, "DEPENDS_ON", "bu-b", "git", "acme/recon-engine", 0.9),
        _edge(svc_b_id, api_id, "DEPENDS_ON", "bu-b", "confluence", "RECON-9", 0.6),
        # A restricted BU-B service also depends on it: the edge is visible, the
        # endpoint is not.
        _edge(svc_b_secret_id, api_id, "DEPENDS_ON", "bu-b", "git",
              "acme/recon-ledger-shadow", 0.85),
    ]
    chunks += [
        {
            "node_id": req_b_id,
            "bu_owner": "bu-b",
            "kind": "requirement",
            "section_path": "description",
            "content": (
                "REC-55 Daily reconciliation completes before 06:00. The reconciliation engine "
                "consumes ledger.entries.v1 and the ledger-api entries endpoint, and must "
                "finish before the 06:00 regulatory cut-off."
            ),
        },
    ]

    result = fast.ingest_claims(claims=claims, chunks=chunks, context_id="ctx_fixture")

    # The shadow service is restricted, not merely BU-B's: Service defaults to
    # topology (FR-5.2), so the fixture tightens it explicitly. Tightening is
    # ungated and immediate, which is the FR-5.9 path a source restriction takes.
    fast.change.submit(
        "kc-ingest",
        "visibility_tighten",
        payload={"node_id": svc_b_secret_id, "visibility_class": "restricted",
                 "reason": "fixture: restricted BU-B node sharing a name with BU-A (AT-6)"},
        context_id="ctx_fixture",
    )

    from kc.core.projector import GraphProjector

    projected = GraphProjector(store).run_once()
    return {
        "claims": result.claims,
        "chunks": result.chunks,
        "projected": projected,
        "anchors": {
            "bu-a": {"service": svc_a_id, "api": api_id, "endpoint": ep_get_id,
                     "requirement": req_a_id, "test": test_a_id},
            "bu-b": {"service": svc_b_id, "restricted_service": svc_b_secret_id,
                     "requirement": req_b_id},
            "commons": {"standard": standard_id, "group": group_id},
        },
    }
