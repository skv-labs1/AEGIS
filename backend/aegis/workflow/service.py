"""Investigation operations, with the workflow enforced at every step.

This is what makes "orchestration" more than a word in a prompt. Each operation
checks the investigation's recorded state before it does anything, so an agent
cannot propose before diagnosing, execute before approval, or resolve before
verification agrees with the measurements.

Nothing here trusts the agent's account of what has happened. The database is
the account.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ..db.models import (
    Actor,
    EventType,
    Investigation,
    Proposal,
    Verification,
)
from ..db.session import session_scope
from ..governance.approvals import ApprovalRequest, ApprovalService, ApprovalState
from ..governance.audit import AuditLog
from ..governance.policy import Decision, RiskClass, RiskPolicy
from .state import InvestigationState, TransitionError, assert_transition, next_states
from .verification import Verdict, VerificationConfig, agreement, compare

# A call into the upstream systems, already governed.
Invoker = Callable[..., Awaitable[dict[str, Any]]]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class WorkflowError(RuntimeError):
    """A workflow rule was broken. The message is written for the agent to act on."""


class InvestigationService:
    def __init__(
        self,
        *,
        invoke: Invoker,
        policy: RiskPolicy,
        approvals: ApprovalService,
        remediation_actions: Callable[[], dict[str, Any]],
    ) -> None:
        self._invoke = invoke
        self._policy = policy
        self._approvals = approvals
        self._remediation_actions = remediation_actions
        self._verification = VerificationConfig.from_dict(policy.verification)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _load(db, investigation_id: int) -> Investigation:
        record = db.get(Investigation, investigation_id)
        if record is None:
            raise WorkflowError(f"No investigation with id {investigation_id}.")
        return record

    @staticmethod
    def _state(record: Investigation) -> InvestigationState:
        return InvestigationState(record.state)

    def _audit(self, investigation_id: int | None, session_id: int | None) -> AuditLog:
        return AuditLog(session_id=session_id, investigation_id=investigation_id)

    def _move(self, record: Investigation, to: InvestigationState) -> None:
        try:
            assert_transition(self._state(record), to)
        except TransitionError as exc:
            raise WorkflowError(str(exc)) from exc
        record.state = to.value
        if to.is_terminal:
            record.closed_at = _now()

    # -- operations -----------------------------------------------------------

    async def start(self, incident_number: str, session_id: int | None = None) -> dict[str, Any]:
        """Open an investigation and pull the incident that triggered it."""
        incident = await self._invoke("itsm_get_incident", {"number": incident_number})
        if not incident.get("found"):
            raise WorkflowError(
                f"Incident {incident_number} was not found in the service management system."
            )
        detail = incident["incident"]

        with session_scope() as db:
            existing = (
                db.query(Investigation)
                .filter(
                    Investigation.incident_number == incident_number.upper(),
                    Investigation.state.notin_(
                        [s.value for s in InvestigationState if s.is_terminal]
                    ),
                )
                .first()
            )
            if existing is not None:
                raise WorkflowError(
                    f"Investigation {existing.id} is already open for {incident_number} "
                    f"in state {existing.state!r}. Continue that one rather than starting again."
                )

            record = Investigation(
                incident_number=incident_number.upper(),
                session_id=session_id,
                state=InvestigationState.OPENED.value,
                subject_user_id=(detail.get("caller") or {}).get("user_id"),
                subject_device_id=detail.get("device_id"),
            )
            db.add(record)
            db.flush()
            record.state = InvestigationState.INVESTIGATING.value
            investigation_id = record.id
            payload = record.as_dict()

        self._audit(investigation_id, session_id).record(
            EventType.EVIDENCE_CAPTURED,
            actor=Actor.GATEWAY,
            phase="investigate",
            summary=f"Investigation {investigation_id} opened for {incident_number}",
            result={"incident": incident_number.upper()},
        )
        return {
            **payload,
            "state": InvestigationState.INVESTIGATING.value,
            "incident": detail,
            "next_steps": (
                "Gather evidence with the read tools, then call record_diagnosis. "
                "A remediation cannot be proposed before a diagnosis exists."
            ),
        }

    async def record_diagnosis(
        self,
        investigation_id: int,
        root_cause: str,
        contributing_factors: list[str],
        evidence_cited: list[str],
        confidence: str,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        if not evidence_cited:
            raise WorkflowError(
                "A diagnosis must cite the evidence it rests on. List the tools or findings "
                "that support the stated root cause."
            )
        with session_scope() as db:
            record = self._load(db, investigation_id)
            self._move(record, InvestigationState.DIAGNOSED)
            record.root_cause = root_cause
            record.contributing_factors = contributing_factors
            record.evidence_cited = evidence_cited
            record.diagnosis_confidence = confidence
            record.diagnosed_at = _now()
            payload = record.as_dict()

        self._audit(investigation_id, session_id).record(
            EventType.EVIDENCE_CAPTURED,
            phase="diagnose",
            summary=f"Diagnosis recorded: {root_cause[:160]}",
            result={
                "root_cause": root_cause,
                "confidence": confidence,
                "contributing_factors": contributing_factors,
                "evidence_cited": evidence_cited,
            },
        )
        return {
            **payload,
            "next_steps": (
                "Propose a remediation with propose_remediation, or resolve the "
                "investigation if no action is warranted."
            ),
        }

    async def propose(
        self,
        investigation_id: int,
        action: str,
        arguments: dict[str, Any],
        rationale: str,
        expected_outcome: str,
        agent_risk_assessment: str | None = None,
        agent_risk_rationale: str | None = None,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        """Record a proposed remediation and put it in front of a human."""
        catalogue = self._remediation_actions()
        if action not in catalogue:
            raise WorkflowError(
                f"{action!r} is not an available remediation action. "
                f"Available: {', '.join(sorted(catalogue)) or 'none'}. "
                "Call list_remediation_actions to see each action's parameters."
            )

        decision = self._policy.evaluate(action, catalogue[action].get("annotations"))
        if decision.decision is Decision.DENY:
            raise WorkflowError(
                f"{action} is refused by policy and cannot be proposed. {decision.reason}"
            )

        with session_scope() as db:
            record = self._load(db, investigation_id)
            self._move(record, InvestigationState.PROPOSED)
            proposal = Proposal(
                investigation_id=investigation_id,
                action=action,
                arguments=arguments,
                rationale=rationale,
                expected_outcome=expected_outcome,
                policy_risk_class=decision.risk.value,
                agent_risk_assessment=agent_risk_assessment,
                agent_risk_rationale=agent_risk_rationale,
                state="pending",
            )
            db.add(proposal)
            db.flush()
            proposal_id = proposal.id

        audit = self._audit(investigation_id, session_id)

        # A low-risk write needs no human. Anything above that is gated.
        if decision.decision is Decision.ALLOW:
            with session_scope() as db:
                self._load(db, investigation_id).state = InvestigationState.APPROVED.value
                db.get(Proposal, proposal_id).state = "approved"
            return {
                "proposal_id": proposal_id,
                "action": action,
                "risk_class": decision.risk.value,
                "approval_required": False,
                "state": InvestigationState.APPROVED.value,
                "next_steps": "Call execute_remediation with this proposal_id.",
            }

        request = ApprovalRequest(
            tool=action,
            arguments=arguments,
            decision=decision,
            rationale=rationale,
            expected_effect=expected_outcome or decision.expected_effect,
            reference=proposal_id,
        )
        with session_scope() as db:
            self._move(self._load(db, investigation_id), InvestigationState.AWAITING_APPROVAL)
        audit.record(
            EventType.APPROVAL_REQUIRED,
            actor=Actor.GATEWAY,
            phase="approval",
            tool_name=action,
            decision=decision,
            arguments=arguments,
            result={**request.as_dict(), "proposal_id": proposal_id},
            summary=f"Proposal {proposal_id} ({action}) awaiting human approval",
        )

        outcome = await self._approvals.request(request)

        with session_scope() as db:
            record = self._load(db, investigation_id)
            proposal = db.get(Proposal, proposal_id)
            proposal.approved_by = outcome.approver
            proposal.approver_role = outcome.approver_role
            proposal.approval_note = outcome.note
            proposal.decided_at = outcome.decided_at or _now()
            if outcome.granted:
                proposal.state = "approved"
                self._move(record, InvestigationState.APPROVED)
            else:
                proposal.state = outcome.state.value
                self._move(record, InvestigationState.REJECTED)
            state = record.state

        audit.record(
            EventType.POLICY_ALLOWED if outcome.granted else EventType.POLICY_REFUSED,
            actor=Actor.HUMAN if outcome.approver else Actor.GATEWAY,
            actor_label=outcome.approver,
            phase="approval",
            tool_name=action,
            decision=decision,
            result={**outcome.as_dict(), "proposal_id": proposal_id},
            summary=(
                f"Proposal {proposal_id} approved by {outcome.approver}"
                if outcome.granted
                else f"Proposal {proposal_id} not approved ({outcome.state.value})"
            ),
        )

        if not outcome.granted:
            return {
                "proposal_id": proposal_id,
                "action": action,
                "approved": False,
                "approval_state": outcome.state.value,
                "decided_by": outcome.approver,
                "reason": outcome.note,
                "state": state,
                "next_steps": (
                    "The action did not run and must not be described as done. Propose an "
                    "alternative, or escalate the investigation with what you found."
                ),
            }

        return {
            "proposal_id": proposal_id,
            "action": action,
            "approved": True,
            "approved_by": outcome.approver,
            "approver_role": outcome.approver_role,
            "state": state,
            "next_steps": "Call execute_remediation with this proposal_id.",
        }

    async def execute(
        self, investigation_id: int, proposal_id: int, session_id: int | None = None
    ) -> dict[str, Any]:
        """Run an approved remediation, snapshotting state either side of it."""
        with session_scope() as db:
            record = self._load(db, investigation_id)
            proposal = db.get(Proposal, proposal_id)
            if proposal is None or proposal.investigation_id != investigation_id:
                raise WorkflowError(
                    f"Proposal {proposal_id} does not belong to investigation {investigation_id}."
                )
            if proposal.executed_at is not None:
                raise WorkflowError(
                    f"Proposal {proposal_id} was already executed at "
                    f"{proposal.executed_at.isoformat()}. Propose a new action rather than "
                    "repeating this one."
                )
            if proposal.state != "approved":
                raise WorkflowError(
                    f"Proposal {proposal_id} is in state {proposal.state!r} and has not been "
                    "approved. It cannot be executed."
                )
            state = self._state(record)
            if state is not InvestigationState.APPROVED:
                raise WorkflowError(
                    f"Investigation is in state {state.value!r}; a remediation can only run "
                    f"from 'approved'. Allowed next: {', '.join(next_states(state))}."
                )
            action, arguments = proposal.action, dict(proposal.arguments or {})
            subject = record.subject_device_id

        audit = self._audit(investigation_id, session_id)
        before = await self._snapshot(subject, arguments)

        result = await self._invoke(action, arguments, preapproved=True)
        failed = bool(result.get("refused")) or result.get("ok") is False or "error" in result

        after = await self._snapshot(subject, arguments)

        with session_scope() as db:
            record = self._load(db, investigation_id)
            proposal = db.get(Proposal, proposal_id)
            proposal.executed_at = _now()
            proposal.execution_result = result
            proposal.state_before = before
            proposal.state_after = after
            if failed:
                proposal.state = "failed"
                proposal.execution_error = str(result.get("error") or result.get("reason"))
                self._move(record, InvestigationState.ESCALATED)
            else:
                proposal.state = "executed"
                self._move(record, InvestigationState.EXECUTED)
            state = record.state

        audit.record(
            EventType.TOOL_CALLED if not failed else EventType.TOOL_FAILED,
            phase="remediate",
            tool_name=action,
            arguments=arguments,
            result=result,
            summary=f"Executed proposal {proposal_id} ({action})",
            error=str(result.get("error")) if failed else None,
        )

        if failed:
            return {
                "proposal_id": proposal_id,
                "executed": False,
                "error": result.get("error") or result.get("reason"),
                "state": state,
                "next_steps": (
                    "The remediation did not succeed. Report this honestly and escalate."
                ),
            }

        return {
            "proposal_id": proposal_id,
            "executed": True,
            "result": result,
            "state": state,
            "state_before": before,
            "state_after": after,
            "next_steps": (
                "Call verify_remediation with your verdict. Aegis compares it against the "
                "measured before and after state."
            ),
        }

    async def _snapshot(
        self, subject: str | None, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Read the subject's health, so verification has something to measure."""
        if self._verification is None:
            return None
        target = subject or arguments.get(self._verification.subject_argument)
        if not target:
            return None
        payload = await self._invoke(
            self._verification.health_tool, {self._verification.subject_argument: target}
        )
        if payload.get("found") is False or payload.get("refused"):
            return None
        return {k: v for k, v in payload.items() if k != "_aegis"}

    async def verify(
        self,
        investigation_id: int,
        proposal_id: int,
        agent_verdict: str,
        agent_rationale: str,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        """Compare the agent's verdict against what actually changed."""
        try:
            verdict = Verdict(agent_verdict)
        except ValueError as exc:
            raise WorkflowError(
                f"{agent_verdict!r} is not a verdict. Use one of: "
                f"{', '.join(v.value for v in Verdict)}."
            ) from exc

        with session_scope() as db:
            record = self._load(db, investigation_id)
            proposal = db.get(Proposal, proposal_id)
            if proposal is None or proposal.investigation_id != investigation_id:
                raise WorkflowError(f"Proposal {proposal_id} is not part of this investigation.")
            if proposal.executed_at is None:
                raise WorkflowError(
                    f"Proposal {proposal_id} has not been executed, so there is nothing to verify."
                )
            before, after = proposal.state_before, proposal.state_after

        if self._verification is None:
            measured, delta = (
                Verdict.UNMEASURABLE,
                {"reason": "No verification metrics configured."},
            )
        else:
            measured, delta = compare(self._verification, before, after)

        agreed, discrepancy = agreement(verdict, measured)

        with session_scope() as db:
            record = self._load(db, investigation_id)
            db.add(
                Verification(
                    proposal_id=proposal_id,
                    investigation_id=investigation_id,
                    agent_verdict=verdict.value,
                    agent_rationale=agent_rationale,
                    measured_verdict=measured.value,
                    measured_delta=delta,
                    agreed=agreed,
                    discrepancy=discrepancy,
                )
            )
            target = (
                InvestigationState.VERIFIED
                if agreed and measured in (Verdict.RESOLVED, Verdict.PARTIALLY_RESOLVED)
                else InvestigationState.VERIFICATION_FAILED
            )
            self._move(record, target)
            state = record.state

        self._audit(investigation_id, session_id).record(
            EventType.EVIDENCE_CAPTURED,
            phase="verify",
            tool_name=self._verification.health_tool if self._verification else None,
            result={
                "agent_verdict": verdict.value,
                "measured_verdict": measured.value,
                "agreed": agreed,
                "delta": delta,
            },
            summary=(
                f"Verification: agent said {verdict.value}, measurement says {measured.value}"
            ),
            error=discrepancy if not agreed else None,
        )

        return {
            "proposal_id": proposal_id,
            "agent_verdict": verdict.value,
            "measured_verdict": measured.value,
            "agreed": agreed,
            "discrepancy": discrepancy,
            "measurement": delta,
            "state": state,
            "next_steps": (
                "Call resolve_investigation to close the incident."
                if state == InvestigationState.VERIFIED.value
                else "This cannot be resolved as fixed. Propose another remediation or escalate."
            ),
        }

    async def resolve(
        self,
        investigation_id: int,
        resolution_code: str,
        summary: str,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        """Close the investigation and write the outcome back to the incident."""
        valid = {"Solved (Permanently)", "Solved (Workaround)", "Not Solved (Escalated)"}
        if resolution_code not in valid:
            raise WorkflowError(f"resolution_code must be one of {sorted(valid)}.")

        with session_scope() as db:
            record = self._load(db, investigation_id)
            state = self._state(record)
            solved = resolution_code.startswith("Solved")

            if solved and state not in (
                InvestigationState.VERIFIED,
                InvestigationState.DIAGNOSED,
            ):
                raise WorkflowError(
                    f"An investigation in state {state.value!r} cannot be closed as "
                    f"{resolution_code!r}. A remediation must be verified first, or the "
                    "incident escalated with 'Not Solved (Escalated)'."
                )
            # Closing straight from a diagnosis means no action was taken, so
            # claiming a permanent fix would be false.
            if state is InvestigationState.DIAGNOSED and resolution_code == "Solved (Permanently)":
                raise WorkflowError(
                    "No remediation was executed, so this cannot be closed as "
                    "'Solved (Permanently)'. Use 'Solved (Workaround)' if the issue "
                    "needs no action, or escalate."
                )
            incident_number = record.incident_number
            target = InvestigationState.RESOLVED if solved else InvestigationState.ESCALATED
            self._move(record, target)
            record.resolution_code = resolution_code
            record.resolution_summary = summary
            final_state = record.state

        note = await self._invoke(
            "itsm_add_work_note",
            {"number": incident_number, "text": summary, "author": "Aegis"},
            preapproved=True,
        )
        closed = await self._invoke(
            "itsm_resolve_incident",
            {
                "number": incident_number,
                "resolution_code": resolution_code,
                "resolution_notes": summary,
                "resolved_by": "Aegis",
            },
            preapproved=True,
        )

        self._audit(investigation_id, session_id).record(
            EventType.TOOL_CALLED,
            phase="resolve",
            tool_name="itsm_resolve_incident",
            result={"resolution_code": resolution_code, "incident": incident_number},
            summary=f"Investigation {investigation_id} closed as {resolution_code}",
        )

        return {
            "investigation_id": investigation_id,
            "incident_number": incident_number,
            "state": final_state,
            "resolution_code": resolution_code,
            "work_note_added": bool(note.get("added")),
            "incident_updated": bool(closed.get("resolved")),
        }

    def get(self, investigation_id: int) -> dict[str, Any]:
        with session_scope() as db:
            record = self._load(db, investigation_id)
            payload = record.as_dict()
            payload["proposals"] = [p.as_dict() for p in record.proposals]
            verifications = (
                db.query(Verification)
                .filter(Verification.investigation_id == investigation_id)
                .order_by(Verification.id)
                .all()
            )
            payload["verifications"] = [v.as_dict() for v in verifications]
            payload["allowed_next_states"] = next_states(self._state(record))
        return payload

    def active_for_session(self, session_id: int | None) -> int | None:
        if session_id is None:
            return None
        with session_scope() as db:
            record = (
                db.query(Investigation)
                .filter(
                    Investigation.session_id == session_id,
                    Investigation.state.notin_(
                        [s.value for s in InvestigationState if s.is_terminal]
                    ),
                )
                .order_by(Investigation.id.desc())
                .first()
            )
            return record.id if record else None


__all__ = [
    "ApprovalState",
    "InvestigationService",
    "RiskClass",
    "WorkflowError",
]
