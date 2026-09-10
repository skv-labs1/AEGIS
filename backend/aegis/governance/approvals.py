"""The approval gate.

A write that policy says needs a human must not execute until a human with a
sufficient role says so. This module defines that contract. The console
implements it against real approvers; until one is attached, the gate refuses
rather than assuming consent, because a gate that defaults to yes is not a gate.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .policy import PolicyDecision


class ApprovalState(str, enum.Enum):
    PENDING = "pending"
    GRANTED = "granted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"  # nothing is listening for approvals


@dataclass
class ApprovalRequest:
    """What a human is being asked to approve, in terms they can judge."""

    tool: str
    arguments: dict[str, Any]
    decision: PolicyDecision
    rationale: str
    expected_effect: str | None = None
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.tool,
            "arguments": self.arguments,
            "risk_class": self.decision.risk.value,
            "policy_reason": self.decision.reason,
            "agent_rationale": self.rationale,
            "expected_effect": self.expected_effect or self.decision.expected_effect,
            "approver_roles": list(self.decision.approver_roles),
            "requested_at": self.requested_at.isoformat(),
        }


@dataclass
class ApprovalOutcome:
    state: ApprovalState
    approver: str | None = None
    approver_role: str | None = None
    note: str | None = None
    decided_at: datetime | None = None

    @property
    def granted(self) -> bool:
        return self.state is ApprovalState.GRANTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "approver": self.approver,
            "approver_role": self.approver_role,
            "note": self.note,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


class ApprovalService(Protocol):
    """Anything that can put a decision in front of a human."""

    async def request(self, request: ApprovalRequest) -> ApprovalOutcome: ...


class NoApproverConfigured:
    """Default gate: refuse, and say plainly why.

    Used when no console or approver channel is attached. Refusing is the only
    safe behaviour; silently allowing would turn a governed action into an
    ungoverned one the moment the approval channel went missing.
    """

    async def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        return ApprovalOutcome(
            state=ApprovalState.UNAVAILABLE,
            note=(
                f"{request.tool} requires approval from one of "
                f"{', '.join(request.decision.approver_roles) or 'an authorised approver'}, "
                "but no approval channel is connected to this gateway. The action was not "
                "performed."
            ),
        )


class AutoApproveForTesting:
    """Grants everything. Test fixtures only; never wire this to a real gateway."""

    def __init__(self, approver: str = "test-approver", role: str = "it_admin") -> None:
        self.approver = approver
        self.role = role
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        self.requests.append(request)
        return ApprovalOutcome(
            state=ApprovalState.GRANTED,
            approver=self.approver,
            approver_role=self.role,
            note="Auto-approved by a test fixture.",
            decided_at=datetime.now(UTC).replace(microsecond=0),
        )
