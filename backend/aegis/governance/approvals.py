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
    reference: int | None = None  # the proposal this decision belongs to
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.reference,
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


class DatabaseApprovalService:
    """Waits for a real person to decide, through whatever writes the decision.

    The console will write decisions directly; until it exists, the `aegis
    approve` command does. The agent's call blocks here while the proposal sits
    in front of a human, which is the behaviour that makes the gate real: the
    remediation genuinely does not happen until someone says so.

    A pending decision times out rather than blocking forever, and a timeout is
    not consent.
    """

    def __init__(self, *, timeout_seconds: float = 300.0, poll_seconds: float = 0.5) -> None:
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    async def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        import asyncio

        from ..db.models import Proposal
        from ..db.session import session_scope

        if request.reference is None:
            return ApprovalOutcome(
                state=ApprovalState.UNAVAILABLE,
                note=(
                    "This action requires approval, but it was not raised as a proposal, so "
                    "there is nothing for a person to decide on. Use propose_remediation."
                ),
            )

        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while True:
            with session_scope() as db:
                proposal = db.get(Proposal, request.reference)
                if proposal is None:
                    return ApprovalOutcome(
                        state=ApprovalState.UNAVAILABLE,
                        note=f"Proposal {request.reference} no longer exists.",
                    )
                state, approver = proposal.state, proposal.approved_by
                role, note = proposal.approver_role, proposal.approval_note
                decided_at = proposal.decided_at

            if state == "approved":
                return ApprovalOutcome(
                    state=ApprovalState.GRANTED,
                    approver=approver,
                    approver_role=role,
                    note=note,
                    decided_at=decided_at,
                )
            if state in ("rejected", "denied"):
                return ApprovalOutcome(
                    state=ApprovalState.REJECTED,
                    approver=approver,
                    approver_role=role,
                    note=note,
                    decided_at=decided_at,
                )

            if asyncio.get_running_loop().time() >= deadline:
                return ApprovalOutcome(
                    state=ApprovalState.EXPIRED,
                    note=(
                        f"No decision was made within {self.timeout_seconds:.0f} seconds. "
                        "The action did not run. A timeout is not approval."
                    ),
                )
            await asyncio.sleep(self.poll_seconds)


def decide(
    proposal_id: int,
    *,
    approve: bool,
    approver: str,
    role: str,
    note: str | None = None,
    policy=None,
) -> dict[str, Any]:
    """Record a human decision on a proposal.

    Checks that the person's role is allowed to approve that level of risk, so
    an auditor cannot approve their way around the separation of duties.
    """
    from ..db.models import Proposal
    from ..db.session import session_scope
    from .policy import RiskClass

    with session_scope() as db:
        proposal = db.get(Proposal, proposal_id)
        if proposal is None:
            raise KeyError(f"No proposal with id {proposal_id}")
        if proposal.state != "pending":
            raise ValueError(
                f"Proposal {proposal_id} is already {proposal.state!r} and cannot be decided again."
            )
        risk = RiskClass(proposal.policy_risk_class)

        if approve and policy is not None:
            role_record = policy.role(role)
            if role_record is None:
                raise ValueError(f"Unknown role {role!r}.")
            if not role_record.can_approve(risk):
                raise PermissionError(
                    f"Role {role!r} may not approve a {risk.value} action. "
                    f"It may approve: "
                    f"{', '.join(sorted(r.value for r in role_record.may_approve)) or 'nothing'}."
                )

        proposal.state = "approved" if approve else "rejected"
        proposal.approved_by = approver
        proposal.approver_role = role
        proposal.approval_note = note
        proposal.decided_at = datetime.now(UTC).replace(microsecond=0)
        return proposal.as_dict()
