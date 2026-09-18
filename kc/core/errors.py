"""Failure modes with requirement numbers attached.

Every error here is a fail-closed path (NFR-3). The OKF error codes are
deliberately coarse: NotFound and NotEntitled both surface as NOT_FOUND so
that absence and denial are indistinguishable to a caller (FR-5.8, AT-6).
"""

from __future__ import annotations


class KCError(Exception):
    """Base class. `code` is the OKF error code where one applies (FR-9.10)."""

    code = "UNAVAILABLE"
    requirement = ""


class IdentityUnresolved(KCError):
    """No resolved caller, or an anonymous placeholder (FR-5.5, AT-5)."""

    code = "UNAUTHENTICATED"
    requirement = "FR-5.5"


class NotEntitled(KCError):
    """Caller may not see this. Reported as NOT_FOUND (FR-5.8)."""

    code = "NOT_FOUND"
    requirement = "FR-5.8"


class NotFound(KCError):
    code = "NOT_FOUND"
    requirement = "FR-5.8"


class InvalidRequest(KCError):
    code = "INVALID_REQUEST"
    requirement = "FR-9.8"


class UnsupportedVersion(KCError):
    code = "UNSUPPORTED_VERSION"
    requirement = "FR-9.2"


class Ambiguous(KCError):
    """Anchor name matches several in-scope nodes; caller gets a
    disambiguation, never a guess (FR-11.3, AT-8)."""

    code = "INVALID_REQUEST"
    requirement = "FR-11.3"

    def __init__(self, anchor_index: int, name: str, candidates: list[dict]) -> None:
        super().__init__(f"anchor {anchor_index} ({name}) is ambiguous")
        self.anchor_index = anchor_index
        self.name = name
        self.candidates = candidates


# --- write path --------------------------------------------------------------


class WriteRejected(KCError):
    """Base for every Change API rejection (FR-7.8)."""

    requirement = "FR-7.8"


class IdentityNotPermitted(WriteRejected):
    """Identity may not perform this operation. Spokes may perform none (AT-4)."""

    requirement = "FR-7.2"


class TierGateBlocked(WriteRejected):
    """Needs human approval, or failed the T3 threshold (FR-7.3, AT-11)."""

    requirement = "FR-7.3"


class SchemaViolation(WriteRejected):
    """Off-schema claim: unknown type, bad edge pair, missing field, bad range
    (FR-4.3, FR-4.9, AT-3, AT-17)."""

    requirement = "FR-4.9"


class IdCollision(WriteRejected):
    """Same node ID from a different source key. Never merged silently: a false
    merge is a segregation breach (FR-4.10, FR-6.10)."""

    requirement = "FR-4.10"


class StaleProposal(WriteRejected):
    """Subject changed since the proposal was built (FR-7.7, AT-54)."""

    requirement = "FR-7.7"


class Quarantined(WriteRejected):
    """Source container has no BU mapping (FR-5.3, AT-24)."""

    requirement = "FR-5.3"


class SourceRestricted(WriteRejected):
    """Object-level permissions narrower than its BU (FR-5.9, AT-25)."""

    requirement = "FR-5.9"


class InbreedingBlocked(WriteRejected):
    """Agent-derived evidence for an agent-derived claim (FR-13.1, AT-10)."""

    requirement = "FR-13.1"


class ProjectorHalted(KCError):
    """A projector failed to apply an event and stopped rather than skipping it
    (FR-7.8, AT-27)."""

    requirement = "FR-7.8"


class BudgetExhausted(KCError):
    """Agent loop hit a hard cap; terminal state is escalate (FR-8.4, AT-9)."""

    code = "BUDGET_EXCEEDED"
    requirement = "FR-8.4"
