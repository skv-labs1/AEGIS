"""Aegis's own state.

This database holds what Aegis did and why. It never holds enterprise master
data: users, devices, assets and tickets stay in the systems of record and are
fetched on demand. What is kept here is the evidence an investigation actually
looked at, because six months later "what did the agent see when it decided?"
must be answerable and the live record will have moved on.

The audit table is append-only. That is enforced below with a mapper guard, not
left to convention.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Actor(str, enum.Enum):
    """Who caused an event. Every audit row attributes itself to one of these."""

    AGENT = "agent"  # the connected agent host
    GATEWAY = "gateway"  # Aegis itself, e.g. a policy refusal
    HUMAN = "human"  # a named person, e.g. an approver
    SYSTEM = "system"  # scheduled or automatic activity


class EventType(str, enum.Enum):
    SESSION_STARTED = "session.started"
    TOOL_CALLED = "tool.called"
    TOOL_FAILED = "tool.failed"
    POLICY_ALLOWED = "policy.allowed"
    POLICY_REFUSED = "policy.refused"
    APPROVAL_REQUIRED = "approval.required"
    EVIDENCE_CAPTURED = "evidence.captured"
    MODEL_CALLED = "model.called"
    MODEL_FAILED = "model.failed"
    RUN_COMPLETED = "run.completed"
    UPSTREAM_UNAVAILABLE = "upstream.unavailable"


class GatewaySession(Base):
    """One MCP connection from an agent host.

    Recording which host did the work matters: Claude Code, the built-in engine
    and a future enterprise agent platform all connect the same way, and the
    audit trail should say which one acted.
    """

    __tablename__ = "gateway_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_name: Mapped[str] = mapped_column(String(200), default="unknown")
    client_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[AuditEvent]] = relationship(back_populates="session")


class AuditEvent(Base):
    """An append-only record of something Aegis did or refused to do.

    Rows are never updated or deleted by application code; see the guard at the
    bottom of this module.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("gateway_sessions.id"), nullable=True)
    # Filled in once an investigation exists to attach events to.
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    actor: Mapped[str] = mapped_column(String(20), default=Actor.AGENT.value)
    actor_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50))
    phase: Mapped[str | None] = mapped_column(String(40), nullable=True)

    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    upstream: Mapped[str | None] = mapped_column(String(80), nullable=True)

    risk_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_by: Mapped[str | None] = mapped_column(String(30), nullable=True)

    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[GatewaySession | None] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_audit_session", "session_id", "id"),
        Index("ix_audit_investigation", "investigation_id", "id"),
        Index("ix_audit_tool", "tool_name"),
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "session_id": self.session_id,
            "investigation_id": self.investigation_id,
            "actor": self.actor,
            "actor_label": self.actor_label,
            "event_type": self.event_type,
            "phase": self.phase,
            "tool": self.tool_name,
            "upstream": self.upstream,
            "risk_class": self.risk_class,
            "policy_decision": self.policy_decision,
            "policy_reason": self.policy_reason,
            "classified_by": self.classified_by,
            "arguments": self.arguments,
            "summary": self.summary,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class Evidence(Base):
    """A snapshot of what a tool returned during an investigation.

    Stored because the live system will have changed by the time anyone reviews
    the decision. Redaction happens before this is written.
    """

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("gateway_sessions.id"), nullable=True)
    investigation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_event_id: Mapped[int | None] = mapped_column(ForeignKey("audit_events.id"), nullable=True)

    source_system: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (Index("ix_evidence_investigation", "investigation_id", "id"),)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "source_system": self.source_system,
            "tool": self.tool_name,
            "subject": self.subject,
            "summary": self.summary,
        }


class Investigation(Base):
    """One governed run against one incident.

    The state column is the workflow's memory. It is what stops a remediation
    being executed before it was approved, or an incident being resolved as
    fixed before verification agreed.
    """

    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_number: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("gateway_sessions.id"), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="opened", index=True)

    subject_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subject_device_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The diagnosis, recorded as structured data rather than prose so the
    # console and the eval harness can both read it.
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributing_factors: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    evidence_cited: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    diagnosis_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    diagnosed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resolution_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="investigation", order_by="Proposal.id"
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "investigation_id": self.id,
            "incident_number": self.incident_number,
            "state": self.state,
            "subject_user_id": self.subject_user_id,
            "subject_device_id": self.subject_device_id,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "diagnosis": {
                "root_cause": self.root_cause,
                "contributing_factors": self.contributing_factors,
                "evidence_cited": self.evidence_cited,
                "confidence": self.diagnosis_confidence,
                "recorded_at": self.diagnosed_at.isoformat() if self.diagnosed_at else None,
            }
            if self.root_cause
            else None,
            "resolution": {
                "code": self.resolution_code,
                "summary": self.resolution_summary,
            }
            if self.resolution_code
            else None,
        }


class Proposal(Base):
    """A remediation the agent wants to perform, and what happened to it."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Null for an action a person raised directly from the console. Manual
    # actions go through the same policy, approval and audit path as the agent's,
    # which is the point: the gateway governs people and agents alike.
    investigation_id: Mapped[int | None] = mapped_column(
        ForeignKey("investigations.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raised_by: Mapped[str] = mapped_column(String(120), default="agent")

    action: Mapped[str] = mapped_column(String(120))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text)
    expected_outcome: Mapped[str] = mapped_column(Text)

    # The policy's classification and the agent's own opinion are recorded
    # separately. The policy decides; the opinion is evidence about the agent.
    policy_risk_class: Mapped[str] = mapped_column(String(20))
    agent_risk_assessment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_risk_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)

    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Snapshots taken by Aegis either side of the action, so verification is a
    # measurement rather than a claim.
    state_before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    state_after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    investigation: Mapped[Investigation | None] = relationship(back_populates="proposals")
    verification: Mapped[Verification | None] = relationship(
        back_populates="proposal", uselist=False
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.id,
            "investigation_id": self.investigation_id,
            "raised_by": self.raised_by,
            "action": self.action,
            "arguments": self.arguments,
            "rationale": self.rationale,
            "expected_outcome": self.expected_outcome,
            "policy_risk_class": self.policy_risk_class,
            "agent_risk_assessment": self.agent_risk_assessment,
            "state": self.state,
            "approved_by": self.approved_by,
            "approver_role": self.approver_role,
            "approval_note": self.approval_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


class Verification(Base):
    """Did the remediation actually work?

    Holds both the agent's verdict and Aegis's own comparison of the before and
    after snapshots. When they disagree, the disagreement is the record, and the
    workflow refuses to resolve the incident as fixed.
    """

    __tablename__ = "verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), index=True)
    investigation_id: Mapped[int] = mapped_column(Integer, index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent_verdict: Mapped[str] = mapped_column(String(30))
    agent_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    measured_verdict: Mapped[str] = mapped_column(String(30))
    measured_delta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    agreed: Mapped[bool] = mapped_column(default=True)
    discrepancy: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposal: Mapped[Proposal] = relationship(back_populates="verification")

    def as_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.id,
            "proposal_id": self.proposal_id,
            "agent_verdict": self.agent_verdict,
            "agent_rationale": self.agent_rationale,
            "measured_verdict": self.measured_verdict,
            "measured_delta": self.measured_delta,
            "agreed": self.agreed,
            "discrepancy": self.discrepancy,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


class EvalRun(Base):
    """One pass of the eval scenarios.

    The provider is stored with the run because a score from a replayed trace
    measures the harness and the governance, not a model, and presenting one as
    the other would be exactly the kind of unsupported claim this project is
    about catching.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    label: Mapped[str] = mapped_column(String(120), default="")
    provider: Mapped[str] = mapped_column(String(60), default="unknown")
    model: Mapped[str] = mapped_column(String(120), default="unknown")
    replayed: Mapped[bool] = mapped_column(default=False)
    scenarios: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    mean_score: Mapped[float] = mapped_column(Float, default=0.0)

    results: Mapped[list[EvalResult]] = relationship(back_populates="run")

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "replayed": self.replayed,
            "scenarios": self.scenarios,
            "passed": self.passed,
            "mean_score": round(self.mean_score, 3),
        }


class EvalResult(Base):
    """How one scenario went in one run."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    scenario_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300), default="")
    passed: Mapped[bool] = mapped_column(default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[EvalRun] = relationship(back_populates="results")

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "passed": self.passed,
            "score": round(self.score, 3),
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "duration_ms": round(self.duration_ms, 1),
            "failed_checks": (self.detail or {}).get("failed_checks", []),
            "checks": (self.detail or {}).get("checks", []),
        }


class AuditIntegrityError(RuntimeError):
    """Raised when something tries to rewrite history."""


@event.listens_for(AuditEvent, "before_update", propagate=True)
def _block_audit_update(mapper, connection, target):
    raise AuditIntegrityError(
        "Audit events are append-only. Record a correcting event instead of "
        "modifying an existing one."
    )


@event.listens_for(AuditEvent, "before_delete", propagate=True)
def _block_audit_delete(mapper, connection, target):
    raise AuditIntegrityError("Audit events are append-only and cannot be deleted.")
