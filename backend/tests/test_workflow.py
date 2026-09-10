"""The workflow must be impossible to skip.

A prompt telling an agent to diagnose before acting is a suggestion. These
tests cover the thing that makes it true: every operation checks the recorded
state before it does anything, so the order holds no matter what the agent was
told or which host is connected.
"""

from __future__ import annotations

import asyncio

import pytest

from aegis.db.models import AuditEvent, Proposal, Verification
from aegis.db.session import session_scope
from aegis.governance.approvals import (
    ApprovalState,
    DatabaseApprovalService,
    decide,
)
from aegis.governance.policy import RiskPolicy
from aegis.workflow.state import (
    InvestigationState,
    TransitionError,
    assert_transition,
    can_transition,
)

DIAGNOSIS = {
    "root_cause": "Disk exhaustion caused by orphaned Outlook data files.",
    "contributing_factors": ["Outdated Office build", "Patches failing for lack of space"],
    "evidence_cited": ["endpoint_get_device_health", "endpoint_get_patch_status"],
    "confidence": "high",
}


async def diagnosed(gateway, incident="INC-1042") -> int:
    started = await gateway.workflow.start(incident)
    investigation_id = started["investigation_id"]
    await gateway.workflow.record_diagnosis(investigation_id, **DIAGNOSIS)
    return investigation_id


# -- the state machine itself -------------------------------------------------


def test_state_machine_forbids_skipping_ahead():
    assert not can_transition(InvestigationState.OPENED, InvestigationState.EXECUTED)
    assert not can_transition(InvestigationState.PROPOSED, InvestigationState.EXECUTED)
    assert not can_transition(InvestigationState.EXECUTED, InvestigationState.RESOLVED)


def test_a_failed_verification_can_never_become_resolved():
    """The one transition that would let a broken fix be reported as done."""
    assert not can_transition(InvestigationState.VERIFICATION_FAILED, InvestigationState.RESOLVED)
    assert can_transition(InvestigationState.VERIFICATION_FAILED, InvestigationState.ESCALATED)


def test_terminal_states_go_nowhere():
    for state in (InvestigationState.RESOLVED, InvestigationState.ESCALATED):
        assert state.is_terminal
        for target in InvestigationState:
            assert not can_transition(state, target)


def test_refusals_say_what_is_missing():
    """A refusal that does not explain itself just gets retried."""
    with pytest.raises(TransitionError, match="Record a diagnosis first"):
        assert_transition(InvestigationState.INVESTIGATING, InvestigationState.PROPOSED)
    with pytest.raises(TransitionError, match="has not been approved"):
        assert_transition(InvestigationState.PROPOSED, InvestigationState.EXECUTED)
    with pytest.raises(TransitionError, match="Verify the remediation"):
        assert_transition(InvestigationState.EXECUTED, InvestigationState.RESOLVED)


# -- enforcement through the service -----------------------------------------


async def test_cannot_propose_before_a_diagnosis_exists(gateway):
    started = await gateway.workflow.start("INC-1042")
    with pytest.raises(Exception, match="Record a diagnosis first"):
        await gateway.workflow.propose(
            started["investigation_id"],
            action="endpoint_clear_disk_space",
            arguments={"device_id": "DEV-4411"},
            rationale="Free up space",
            expected_outcome="More space",
        )


async def test_a_diagnosis_must_cite_evidence(gateway):
    started = await gateway.workflow.start("INC-1042")
    with pytest.raises(Exception, match="must cite the evidence"):
        await gateway.workflow.record_diagnosis(
            started["investigation_id"],
            root_cause="Something is wrong",
            contributing_factors=[],
            evidence_cited=[],
            confidence="high",
        )


async def test_cannot_execute_an_unapproved_proposal(gateway, monkeypatch):
    from aegis.governance import approvals as mod

    investigation_id = await diagnosed(gateway)

    async def never_decide(request):
        return mod.ApprovalOutcome(state=ApprovalState.EXPIRED, note="No decision.")

    monkeypatch.setattr(gateway.approvals, "request", never_decide)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    assert proposal["approved"] is False

    with pytest.raises(Exception, match="has not been approved"):
        await gateway.workflow.execute(investigation_id, proposal["proposal_id"])


async def test_a_timeout_is_not_approval(gateway, monkeypatch):
    """The gate must fail closed when nobody answers."""
    from aegis.governance import approvals as mod

    investigation_id = await diagnosed(gateway)
    monkeypatch.setattr(
        gateway.approvals,
        "request",
        lambda request: asyncio.sleep(
            0, result=mod.ApprovalOutcome(state=ApprovalState.EXPIRED, note="timed out")
        ),
    )
    before = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    after = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})

    assert proposal["approved"] is False
    assert proposal["approval_state"] == "expired"
    assert after["telemetry"]["disk_used_pct"] == before["telemetry"]["disk_used_pct"]


async def test_a_remediation_cannot_be_executed_twice(gateway):
    investigation_id = await diagnosed(gateway)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])
    with pytest.raises(Exception, match="already executed"):
        await gateway.workflow.execute(investigation_id, proposal["proposal_id"])


async def test_cannot_resolve_before_verification(gateway):
    investigation_id = await diagnosed(gateway)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])
    with pytest.raises(Exception, match="must be verified first"):
        await gateway.workflow.resolve(investigation_id, "Solved (Permanently)", "All good.")


async def test_an_unknown_action_cannot_be_proposed(gateway):
    investigation_id = await diagnosed(gateway)
    with pytest.raises(Exception, match="not an available remediation action"):
        await gateway.workflow.propose(
            investigation_id,
            action="endpoint_format_hard_drive",
            arguments={"device_id": "DEV-4411"},
            rationale="Fresh start",
            expected_outcome="Clean device",
        )


async def test_a_denied_action_cannot_be_proposed(gateway):
    """Policy denial holds at the proposal stage, not just at execution."""
    investigation_id = await diagnosed(gateway)
    with pytest.raises(Exception, match="not an available remediation action"):
        await gateway.workflow.propose(
            investigation_id,
            action="endpoint_reimage_device",
            arguments={"device_id": "DEV-4411"},
            rationale="Nothing else worked",
            expected_outcome="Clean device",
        )


async def test_two_investigations_cannot_run_on_one_incident(gateway):
    await gateway.workflow.start("INC-1042")
    with pytest.raises(Exception, match="already open"):
        await gateway.workflow.start("INC-1042")


# -- verification -------------------------------------------------------------


async def test_verification_measures_rather_than_trusts(gateway):
    investigation_id = await diagnosed(gateway)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])
    result = await gateway.workflow.verify(
        investigation_id, proposal["proposal_id"], "resolved", "Disk freed and health recovered."
    )

    assert result["measured_verdict"] == "resolved"
    assert result["agreed"] is True
    metrics = {m["metric"]: m for m in result["measurement"]["metrics"]}
    assert metrics["health_score"]["before"] < metrics["health_score"]["after"]
    assert metrics["telemetry.disk_used_pct"]["improved"] is True


async def test_an_overstated_verdict_is_caught_and_blocks_resolution(gateway):
    """The agent claims success the numbers do not support."""
    investigation_id = await diagnosed(gateway)
    # Clear only the smallest category, so almost nothing improves.
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411", "categories": ["browser_and_teams_cache"]},
        rationale="Clear caches",
        expected_outcome="Some space freed",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])

    result = await gateway.workflow.verify(
        investigation_id, proposal["proposal_id"], "resolved", "All fixed."
    )
    assert result["agreed"] is False
    assert result["measured_verdict"] != "resolved"
    assert "not supported by the before and after state" in result["discrepancy"]
    assert result["state"] == InvestigationState.VERIFICATION_FAILED.value

    with pytest.raises(Exception, match="cannot be closed as"):
        await gateway.workflow.resolve(investigation_id, "Solved (Permanently)", "Done.")

    with session_scope() as db:
        record = db.query(Verification).one()
        assert record.agreed is False
        assert record.agent_verdict == "resolved"


async def test_being_more_cautious_than_the_measurement_is_not_a_failure(gateway):
    investigation_id = await diagnosed(gateway)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])
    result = await gateway.workflow.verify(
        investigation_id,
        proposal["proposal_id"],
        "partially_resolved",
        "Disk is better but Office is still outdated.",
    )
    assert result["agreed"] is True
    assert result["measured_verdict"] == "resolved"
    assert "more cautious" in result["discrepancy"]
    assert result["state"] == InvestigationState.VERIFIED.value


async def test_a_failed_remediation_cannot_be_reported_as_fixed(gateway):
    investigation_id = await diagnosed(gateway)
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411", "categories": ["browser_and_teams_cache"]},
        rationale="Clear caches",
        expected_outcome="Some space freed",
    )
    await gateway.workflow.execute(investigation_id, proposal["proposal_id"])
    await gateway.workflow.verify(
        investigation_id, proposal["proposal_id"], "not_resolved", "Barely moved."
    )
    result = await gateway.workflow.resolve(
        investigation_id, "Not Solved (Escalated)", "Cleanup insufficient; needs an Office update."
    )
    assert result["state"] == InvestigationState.ESCALATED.value
    assert result["incident_updated"] is True


async def test_closing_without_any_remediation_cannot_claim_a_permanent_fix(gateway):
    investigation_id = await diagnosed(gateway, "INC-1040")
    with pytest.raises(Exception, match="No remediation was executed"):
        await gateway.workflow.resolve(investigation_id, "Solved (Permanently)", "Fine now.")
    result = await gateway.workflow.resolve(
        investigation_id, "Solved (Workaround)", "No action needed; advised the user."
    )
    assert result["state"] == InvestigationState.RESOLVED.value


# -- separation of duties -----------------------------------------------------


async def test_an_auditor_cannot_approve(gateway, settings):
    investigation_id = await diagnosed(gateway)
    with session_scope() as db:
        db.add(
            Proposal(
                investigation_id=investigation_id,
                action="endpoint_clear_disk_space",
                arguments={"device_id": "DEV-4411"},
                rationale="x",
                expected_outcome="y",
                policy_risk_class="WRITE_MEDIUM",
                state="pending",
            )
        )
        db.flush()

    policy = RiskPolicy.load(settings.policy_file)
    with session_scope() as db:
        proposal_id = db.query(Proposal).order_by(Proposal.id.desc()).first().id

    with pytest.raises(PermissionError, match="may not approve"):
        decide(proposal_id, approve=True, approver="An Auditor", role="auditor", policy=policy)


async def test_a_decision_cannot_be_made_twice(gateway, settings):
    investigation_id = await diagnosed(gateway)
    with session_scope() as db:
        db.add(
            Proposal(
                investigation_id=investigation_id,
                action="endpoint_clear_disk_space",
                arguments={"device_id": "DEV-4411"},
                rationale="x",
                expected_outcome="y",
                policy_risk_class="WRITE_MEDIUM",
                state="pending",
            )
        )
        db.flush()
        proposal_id = db.query(Proposal).order_by(Proposal.id.desc()).first().id

    policy = RiskPolicy.load(settings.policy_file)
    decide(proposal_id, approve=True, approver="Marcus Hall", role="it_admin", policy=policy)
    with pytest.raises(ValueError, match="cannot be decided again"):
        decide(proposal_id, approve=False, approver="Marcus Hall", role="it_admin", policy=policy)


async def test_a_real_human_decision_releases_the_waiting_agent(settings, gateway):
    """The agent's call genuinely blocks until a person decides."""
    investigation_id = await diagnosed(gateway)
    gateway.workflow._approvals = DatabaseApprovalService(timeout_seconds=10.0, poll_seconds=0.05)
    policy = RiskPolicy.load(settings.policy_file)

    async def approve_shortly() -> None:
        for _ in range(100):
            await asyncio.sleep(0.05)
            with session_scope() as db:
                pending = db.query(Proposal).filter(Proposal.state == "pending").first()
                proposal_id = pending.id if pending else None
            if proposal_id:
                decide(
                    proposal_id,
                    approve=True,
                    approver="Rebecca Lindqvist",
                    role="it_admin",
                    note="Approved during the change window.",
                    policy=policy,
                )
                return

    approver_task = asyncio.create_task(approve_shortly())
    proposal = await gateway.workflow.propose(
        investigation_id,
        action="endpoint_clear_disk_space",
        arguments={"device_id": "DEV-4411"},
        rationale="Reclaim space",
        expected_outcome="Disk below 75 percent",
    )
    await approver_task

    assert proposal["approved"] is True
    assert proposal["approved_by"] == "Rebecca Lindqvist"

    with session_scope() as db:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.actor == "human")
            .order_by(AuditEvent.id.desc())
            .first()
        )
        assert event.actor_label == "Rebecca Lindqvist"


async def test_a_verification_gated_action_cannot_be_proposed_as_a_remediation(gateway):
    """Resolving an incident is tied to verification, so it must not be proposable.

    If it could be proposed, an agent could close a ticket as fixed through the
    remediation path and never verify anything.
    """
    assert "itsm_resolve_incident" not in gateway.published_tools
    assert "itsm_resolve_incident" not in gateway._remediation_catalogue
    assert "itsm_resolve_incident" in gateway._workflow_managed

    investigation_id = await diagnosed(gateway)
    with pytest.raises(Exception, match="not an available remediation action"):
        await gateway.workflow.propose(
            investigation_id,
            action="itsm_resolve_incident",
            arguments={
                "number": "INC-1042",
                "resolution_code": "Solved (Permanently)",
                "resolution_notes": "Closing without verifying.",
            },
            rationale="Close the ticket",
            expected_outcome="Ticket closed",
        )
