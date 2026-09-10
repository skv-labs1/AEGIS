"""Actions a person raises directly from the console.

A manual remediation takes exactly the same path as one the agent proposes:
policy classification, an approval by someone with a sufficient role, execution
through the gateway, and an audit record. Someone clicking a button in the
console is not a reason to skip the controls, and demonstrating that is the
point of routing it through here rather than calling the tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ..db.models import Actor, EventType, Proposal
from ..db.session import session_scope
from ..governance.audit import AuditLog
from ..governance.policy import Decision, RiskPolicy
from .service import WorkflowError


class ManualActionService:
    def __init__(
        self,
        *,
        invoke: Callable[..., Awaitable[dict[str, Any]]],
        policy: RiskPolicy,
        remediation_actions: Callable[[], dict[str, Any]],
    ) -> None:
        self._invoke = invoke
        self._policy = policy
        self._remediation_actions = remediation_actions

    def raise_proposal(
        self,
        action: str,
        arguments: dict[str, Any],
        rationale: str,
        raised_by: str,
    ) -> dict[str, Any]:
        catalogue = self._remediation_actions()
        if action not in catalogue:
            raise WorkflowError(
                f"{action!r} is not an available action. "
                f"Available: {', '.join(sorted(catalogue)) or 'none'}."
            )
        decision = self._policy.evaluate(action, catalogue[action].get("annotations"))
        if decision.decision is Decision.DENY:
            raise WorkflowError(f"{action} is refused by policy. {decision.reason}")

        with session_scope() as db:
            proposal = Proposal(
                investigation_id=None,
                raised_by=raised_by,
                action=action,
                arguments=arguments,
                rationale=rationale,
                expected_outcome=decision.expected_effect or "As described by the action.",
                policy_risk_class=decision.risk.value,
                state="pending" if decision.decision is Decision.REQUIRE_APPROVAL else "approved",
            )
            db.add(proposal)
            db.flush()
            payload = proposal.as_dict()

        AuditLog().record(
            EventType.APPROVAL_REQUIRED
            if decision.decision is Decision.REQUIRE_APPROVAL
            else EventType.POLICY_ALLOWED,
            actor=Actor.HUMAN,
            actor_label=raised_by,
            phase="manual",
            tool_name=action,
            decision=decision,
            arguments=arguments,
            result=payload,
            summary=f"{raised_by} raised {action} from the console",
        )
        return {
            **payload,
            "approval_required": decision.decision is Decision.REQUIRE_APPROVAL,
            "approver_roles": list(decision.approver_roles),
            "policy_reason": decision.reason,
        }

    async def execute(self, proposal_id: int, executed_by: str) -> dict[str, Any]:
        with session_scope() as db:
            proposal = db.get(Proposal, proposal_id)
            if proposal is None:
                raise WorkflowError(f"No proposal with id {proposal_id}.")
            if proposal.executed_at is not None:
                raise WorkflowError(f"Proposal {proposal_id} was already executed.")
            if proposal.state != "approved":
                raise WorkflowError(
                    f"Proposal {proposal_id} is {proposal.state!r} and has not been approved."
                )
            action, arguments = proposal.action, dict(proposal.arguments or {})

        result = await self._invoke(action, arguments, preapproved=True)
        failed = bool(result.get("refused")) or result.get("ok") is False

        with session_scope() as db:
            proposal = db.get(Proposal, proposal_id)
            proposal.executed_at = datetime.now(UTC).replace(microsecond=0)
            proposal.execution_result = result
            proposal.state = "failed" if failed else "executed"
            if failed:
                proposal.execution_error = str(result.get("error") or result.get("reason"))

        AuditLog().record(
            EventType.TOOL_CALLED if not failed else EventType.TOOL_FAILED,
            actor=Actor.HUMAN,
            actor_label=executed_by,
            phase="manual",
            tool_name=action,
            arguments=arguments,
            result=result,
            summary=f"{executed_by} executed {action} from the console",
            error=str(result.get("error")) if failed else None,
        )
        return {"proposal_id": proposal_id, "executed": not failed, "result": result}
