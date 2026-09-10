"""The investigation state machine.

Orchestration in Aegis is enforced, not requested. A prompt asking an agent to
diagnose before it acts is a suggestion; this is the thing that makes it true.

    opened → investigating → diagnosed → proposed → awaiting_approval
           → approved → executed → verified → resolved
                      ↘ rejected            ↘ verification_failed → escalated

Every transition below is checked against the record in the database, so the
order holds regardless of which agent host is connected or what it was told.
"""

from __future__ import annotations

import enum


class InvestigationState(str, enum.Enum):
    OPENED = "opened"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = {
    InvestigationState.RESOLVED,
    InvestigationState.ESCALATED,
    InvestigationState.ABANDONED,
}

# What each state may become. Anything not listed is refused.
_ALLOWED: dict[InvestigationState, set[InvestigationState]] = {
    InvestigationState.OPENED: {
        InvestigationState.INVESTIGATING,
        InvestigationState.ABANDONED,
    },
    InvestigationState.INVESTIGATING: {
        InvestigationState.DIAGNOSED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.DIAGNOSED: {
        # A diagnosis that needs no action can close without a remediation.
        InvestigationState.PROPOSED,
        InvestigationState.RESOLVED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
        # Re-diagnosing after new evidence is legitimate.
        InvestigationState.DIAGNOSED,
    },
    InvestigationState.PROPOSED: {
        InvestigationState.AWAITING_APPROVAL,
        InvestigationState.DIAGNOSED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.AWAITING_APPROVAL: {
        InvestigationState.APPROVED,
        InvestigationState.REJECTED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.APPROVED: {
        InvestigationState.EXECUTED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.REJECTED: {
        # A rejected action means propose something else, or close honestly.
        InvestigationState.DIAGNOSED,
        InvestigationState.PROPOSED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.EXECUTED: {
        InvestigationState.VERIFIED,
        InvestigationState.VERIFICATION_FAILED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.VERIFIED: {
        InvestigationState.RESOLVED,
        # More than one remediation may be needed.
        InvestigationState.PROPOSED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.VERIFICATION_FAILED: {
        # A failed fix may never be resolved as fixed. It escalates or is retried.
        InvestigationState.PROPOSED,
        InvestigationState.ESCALATED,
        InvestigationState.ABANDONED,
    },
    InvestigationState.RESOLVED: set(),
    InvestigationState.ESCALATED: set(),
    InvestigationState.ABANDONED: set(),
}


class TransitionError(RuntimeError):
    """Raised when the workflow is asked to skip a step."""

    def __init__(self, current: InvestigationState, requested: InvestigationState, hint: str = ""):
        self.current = current
        self.requested = requested
        message = f"An investigation in state {current.value!r} cannot move to {requested.value!r}."
        if hint:
            message = f"{message} {hint}"
        super().__init__(message)


# Guidance the agent gets back when it tries to skip ahead. Written for the
# reader, because a refusal that does not say what to do next just gets retried.
_HINTS: dict[tuple[InvestigationState, InvestigationState], str] = {
    (InvestigationState.INVESTIGATING, InvestigationState.PROPOSED): (
        "Record a diagnosis first: a remediation must follow from a stated root cause."
    ),
    (InvestigationState.OPENED, InvestigationState.PROPOSED): (
        "Gather evidence and record a diagnosis before proposing an action."
    ),
    (InvestigationState.PROPOSED, InvestigationState.EXECUTED): (
        "The proposal has not been approved. Request approval before executing."
    ),
    (InvestigationState.AWAITING_APPROVAL, InvestigationState.EXECUTED): (
        "Approval is still pending. Wait for a decision rather than proceeding."
    ),
    (InvestigationState.EXECUTED, InvestigationState.RESOLVED): (
        "Verify the remediation before resolving. Aegis compares your verdict "
        "against the measured before and after state."
    ),
    (InvestigationState.VERIFICATION_FAILED, InvestigationState.RESOLVED): (
        "Verification did not show the expected improvement, so this cannot be "
        "resolved as fixed. Propose another remediation or escalate."
    ),
}


def can_transition(current: InvestigationState, requested: InvestigationState) -> bool:
    return requested in _ALLOWED.get(current, set())


def assert_transition(current: InvestigationState, requested: InvestigationState) -> None:
    """Raise unless the move is legal."""
    if not can_transition(current, requested):
        raise TransitionError(current, requested, _HINTS.get((current, requested), ""))


def next_states(current: InvestigationState) -> list[str]:
    return sorted(s.value for s in _ALLOWED.get(current, set()))
