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
