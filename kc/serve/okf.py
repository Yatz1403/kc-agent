"""The OKF message family, versioned together (FR-9.1 to FR-9.14).

One module on purpose: v0.1 froze one message out of eleven, and the fix is
that the whole family moves as a unit. `OKF_VERSION` is the wire contract and
is independent of the document version — document 0.3 defines contract 0.2.

Nothing here generates prose. Gaps and errors are codes (FR-9.6).
"""

from __future__ import annotations

from typing import Any

from kc.config import loader as cfg
from kc.core.errors import InvalidRequest, UnsupportedVersion

OKF_VERSION = "0.2"
SUPPORTED_VERSIONS = frozenset({"0.2"})

CONTEXT_REQUEST = "context_request"
CONTEXT_RESPONSE = "context_response"
DISAMBIGUATION = "disambiguation"
DISAMBIGUATION_CHOICE = "disambiguation_choice"
ERROR = "error"
FEEDBACK = "feedback"
SPOKE_TASK = "spoke_task"
PROPOSAL = "proposal"
ESCALATION = "escalation"
NO_ACTION = "no_action"
REVIEW_DECISION = "review_decision"

MESSAGE_TYPES = (
    CONTEXT_REQUEST, CONTEXT_RESPONSE, DISAMBIGUATION, DISAMBIGUATION_CHOICE, ERROR,
    FEEDBACK, SPOKE_TASK, PROPOSAL, ESCALATION, NO_ACTION, REVIEW_DECISION,
)

FEEDBACK_REASONS = (
    "MISSING_ENTITY", "WRONG_ENTITY", "STALE", "IRRELEVANT_ARTIFACT", "TRUNCATED",
    "AMBIGUOUS_ANCHOR",
)
ESCALATION_REASONS = (
    "BUDGET_EXHAUSTED", "AMBIGUOUS", "NO_AUTHORITY", "STUBBED", "APPROVAL_EXPIRED",
    "STALE_PROPOSAL", "NO_LLM_CLIENT",
)


def _envelope(message_type: str) -> dict[str, Any]:
    return {"okf_version": OKF_VERSION, "message_type": message_type}


# --- FR-9.8: request ---------------------------------------------------------


def context_request(
    *,
    request_id: str,
    intent: str,
    anchors: list[dict[str, Any]],
    query_text: str | None = None,
    as_of: str | None = None,
    include_artifacts: bool = True,
    max_tokens: int | None = None,
    max_hops: int | None = None,
    okf_version: str = OKF_VERSION,
) -> dict[str, Any]:
    return {
        "okf_version": okf_version,
        "message_type": CONTEXT_REQUEST,
        "request_id": request_id,
        "intent": intent,
        "anchors": anchors,
        "query_text": query_text,
        "as_of": as_of,
        "include_artifacts": include_artifacts,
        "max_tokens": max_tokens,
        "max_hops": max_hops,
    }


def validate_request(message: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a request (FR-9.8, AT-48).

    Budgets may only be lowered, never raised: a caller cannot buy more hops
    than its intent allows (FR-11.4).
    """
    if message.get("okf_version") not in SUPPORTED_VERSIONS:
        raise UnsupportedVersion(f"okf_version {message.get('okf_version')} not supported")
    if message.get("message_type") != CONTEXT_REQUEST:
        raise InvalidRequest("message_type must be context_request")
    if not message.get("request_id"):
        raise InvalidRequest("request_id is required")
    intent = message.get("intent")
    if intent not in cfg.intents():
        raise InvalidRequest(f"unknown intent {intent}")
    anchors = message.get("anchors") or []
    if not anchors:
        raise InvalidRequest("at least one anchor is required")
    if len(anchors) > int(cfg.retrieval_setting("max_anchors")):
        raise InvalidRequest("too many anchors")
    for anchor in anchors:
        if not (anchor.get("id") or anchor.get("name")):
            raise InvalidRequest("each anchor needs an id or a name")

    budget = cfg.intent_budget(intent)
    hops = budget["hops"]
    if message.get("max_hops") is not None:
        hops = min(int(message["max_hops"]), hops)
    tokens = budget["max_tokens"]
    if message.get("max_tokens") is not None:
        tokens = min(int(message["max_tokens"]), tokens)

    return {
        "request_id": message["request_id"],
        "intent": intent,
        "anchors": anchors,
        "query_text": message.get("query_text"),
        "as_of": message.get("as_of"),
        "include_artifacts": bool(message.get("include_artifacts", True)),
        "hops": int(hops),
        "max_tokens": int(tokens),
        "fanout": int(budget["fanout"]),
        "tracks": list(budget["tracks"]),
    }


# --- FR-9.1: response --------------------------------------------------------


def context_response(
    *,
    request_id: str,
    intent: str,
    anchors: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
    budget: dict[str, Any],
    as_of: str | None = None,
) -> dict[str, Any]:
    return {
        **_envelope(CONTEXT_RESPONSE),
        "request_id": request_id,
        "intent": intent,
        "as_of": as_of,
        "anchors": anchors,
        "entities": entities,
        "relations": relations,
        "paths": paths,
        "artifacts": artifacts,
        "gaps": gaps,
        "provenance": provenance,
        "budget": budget,
    }


def gap(code: str, node_id: str | None = None) -> dict[str, Any]:
    if code not in cfg.retrieval_setting("gap_codes"):
        raise InvalidRequest(f"unknown gap code {code}")
    return {"code": code, "node_id": node_id}


# --- FR-9.9: disambiguation --------------------------------------------------


def disambiguation(
    *, request_id: str, ambiguous_anchors: list[dict[str, Any]]
) -> dict[str, Any]:
    """Candidates are already scope-filtered by the caller: a node outside
    scope must not appear, be counted, or trigger this message (AT-19)."""
    return {
        **_envelope(DISAMBIGUATION),
        "request_id": request_id,
        "ambiguous_anchors": ambiguous_anchors,
        "task_state": "input-required",
    }


def disambiguation_choice(*, request_id: str, choices: list[dict[str, Any]]) -> dict[str, Any]:
    return {**_envelope(DISAMBIGUATION_CHOICE), "request_id": request_id, "choices": choices}


# --- FR-9.10: error ----------------------------------------------------------


def error(code: str, request_id: str | None = None, retryable: bool = False) -> dict[str, Any]:
    """No free text, no hint, no partial result: absence and denial must be
    indistinguishable (FR-5.8, AT-20)."""
    return {
        **_envelope(ERROR),
        "request_id": request_id,
        "code": code,
        "retryable": retryable,
        "task_state": "failed",
    }


# --- FR-9.11: feedback ------------------------------------------------------


def feedback(
    *,
    request_id: str,
    rating: str,
    reasons: list[str] | None = None,
    node_ids: list[str] | None = None,
) -> dict[str, Any]:
    bad = set(reasons or []) - set(FEEDBACK_REASONS)
    if bad:
        raise InvalidRequest(f"unknown feedback reasons {sorted(bad)}")
    if rating not in ("poor", "acceptable", "good"):
        raise InvalidRequest("rating must be poor, acceptable or good")
    return {
        **_envelope(FEEDBACK),
        "request_id": request_id,
        "rating": rating,
        "reasons": list(reasons or []),
        "node_ids": list(node_ids or []),
    }


# --- FR-9.12: spoke task -----------------------------------------------------


def spoke_task(
    *,
    task_type: str,
    run_id: str,
    task_scope: list[str],
    subjects: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    source_context_ids: list[str] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **_envelope(SPOKE_TASK),
        "task_type": task_type,
        "run_id": run_id,
        "task_scope": list(task_scope),
        "source_context_ids": list(source_context_ids or []),
        "subjects": subjects,
        "candidates": list(candidates or []),
        "budget": budget or {"tool_calls": 40, "tokens": 60000, "wall_clock_s": 120},
    }


# --- FR-9.13: proposal, escalation, no action --------------------------------


def proposal(
    *,
    proposal_id: str,
    run_id: str,
    agent: str,
    action: str,
    subjects: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Carries no tier and no confidence: the gate computes both (FR-7.14)."""
    if not evidence:
        raise InvalidRequest("a proposal must cite evidence (FR-9.13)")
    return {
        **_envelope(PROPOSAL),
        "proposal_id": proposal_id,
        "run_id": run_id,
        "agent": agent,
        "action": action,
        "subjects": subjects,
        "changes": changes,
        "evidence": evidence,
    }


def escalation(
    *,
    run_id: str,
    agent: str,
    reason: str,
    subjects: list[dict[str, Any]] | None = None,
    partial_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if reason not in ESCALATION_REASONS:
        raise InvalidRequest(f"unknown escalation reason {reason}")
    return {
        **_envelope(ESCALATION),
        "run_id": run_id,
        "agent": agent,
        "reason": reason,
        "subjects": list(subjects or []),
        "partial_evidence": list(partial_evidence or []),
    }


def no_action(
    *, run_id: str, agent: str, reason: str, subjects: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        **_envelope(NO_ACTION),
        "run_id": run_id,
        "agent": agent,
        "reason": reason,
        "subjects": list(subjects or []),
    }


# --- FR-9.14: review decision ------------------------------------------------


def review_decision(
    *,
    proposal_id: str,
    decision: str,
    reason_code: str = "",
    rationale: str = "",
    modified_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The reviewer identity comes from the authenticated context, never the
    body. `rationale` is human free text and is untrusted data to agents."""
    if decision not in ("approve", "reject", "modify"):
        raise InvalidRequest("decision must be approve, reject or modify")
    return {
        **_envelope(REVIEW_DECISION),
        "proposal_id": proposal_id,
        "decision": decision,
        "reason_code": reason_code,
        "rationale": rationale,
        "modified_changes": list(modified_changes or []),
    }


# --- token accounting --------------------------------------------------------


def count_tokens(text: str) -> int:
    """Whitespace tokenizer, named in the response so a consumer knows what
    `tokens_returned` counted with (FR-9.1 rule 7)."""
    return len(text.split())


def tokenizer_name() -> str:
    return str(cfg.retrieval_setting("tokenizer"))
