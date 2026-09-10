"""Writing the audit trail.

Every tool call, policy refusal and captured piece of evidence goes through
here. The table is append-only, so a mistake is corrected by recording another
event, never by editing one.

What a reviewer must be able to reconstruct months later: what the agent did,
which tool it used through which system, what the policy decided and why, what
it saw, how long it took, and what failed.
"""

from __future__ import annotations

import json
from typing import Any

from ..db.models import Actor, AuditEvent, EventType, Evidence, GatewaySession
from ..db.session import session_scope
from .policy import PolicyDecision

# Arguments and results are recorded, but a single oversized payload should not
# be able to bloat the trail. Evidence keeps the full record; the audit row
# keeps a bounded copy.
MAX_RECORDED_CHARS = 20_000


def _bounded(value: Any) -> Any:
    """Keep a payload within a sane size, saying so when it is trimmed."""
    if value is None:
        return None
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return {"unserialisable": repr(value)[:500]}
    if len(encoded) <= MAX_RECORDED_CHARS:
        return value
    return {
        "truncated": True,
        "original_size_chars": len(encoded),
        "note": "Full payload retained as evidence; this copy was trimmed for the audit row.",
        "preview": encoded[:2000],
    }


class AuditLog:
    """Append-only writer, scoped to one gateway session."""

    def __init__(self, session_id: int | None = None, investigation_id: int | None = None) -> None:
        self.session_id = session_id
        self.investigation_id = investigation_id

    # -- session lifecycle ----------------------------------------------------

    @staticmethod
    def open_session(
        client_name: str = "unknown",
        client_version: str | None = None,
        protocol_version: str | None = None,
    ) -> int:
        """Record a new agent host connection and return its id."""
        with session_scope() as db:
            record = GatewaySession(
                client_name=client_name,
                client_version=client_version,
                protocol_version=protocol_version,
            )
            db.add(record)
            db.flush()
            session_id = record.id
            db.add(
                AuditEvent(
                    session_id=session_id,
                    actor=Actor.GATEWAY.value,
                    event_type=EventType.SESSION_STARTED.value,
                    summary=f"Agent host connected: {client_name} {client_version or ''}".strip(),
                    result={"client_name": client_name, "client_version": client_version},
                )
            )
        return session_id

    # -- events ---------------------------------------------------------------

    def record(
        self,
        event_type: EventType,
        *,
        actor: Actor = Actor.AGENT,
        actor_label: str | None = None,
        tool_name: str | None = None,
        upstream: str | None = None,
        decision: PolicyDecision | None = None,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        summary: str | None = None,
        latency_ms: float | None = None,
        error: str | None = None,
        phase: str | None = None,
    ) -> int:
        with session_scope() as db:
            event = AuditEvent(
                session_id=self.session_id,
                investigation_id=self.investigation_id,
                actor=actor.value,
                actor_label=actor_label,
                event_type=event_type.value,
                phase=phase,
                tool_name=tool_name,
                upstream=upstream,
                risk_class=decision.risk.value if decision else None,
                policy_decision=decision.decision.value if decision else None,
                policy_reason=decision.reason if decision else None,
                classified_by=decision.source if decision else None,
                arguments=_bounded(arguments),
                result=_bounded(result),
                summary=summary,
                latency_ms=latency_ms,
                error=error,
            )
            db.add(event)
            db.flush()
            return event.id

    def capture_evidence(
        self,
        *,
        source_system: str,
        tool_name: str,
        payload: dict[str, Any] | None,
        subject: str | None = None,
        summary: str | None = None,
        audit_event_id: int | None = None,
    ) -> int:
        """Store what a tool returned, so the decision stays reviewable later."""
        with session_scope() as db:
            evidence = Evidence(
                session_id=self.session_id,
                investigation_id=self.investigation_id,
                audit_event_id=audit_event_id,
                source_system=source_system,
                tool_name=tool_name,
                subject=subject,
                summary=summary,
                payload=payload,
            )
            db.add(evidence)
            db.flush()
            return evidence.id

    # -- reading --------------------------------------------------------------

    @staticmethod
    def trail(session_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with session_scope() as db:
            query = db.query(AuditEvent).order_by(AuditEvent.id)
            if session_id is not None:
                query = query.filter(AuditEvent.session_id == session_id)
            return [e.as_dict() for e in query.limit(limit).all()]

    @staticmethod
    def evidence_for(session_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with session_scope() as db:
            rows = (
                db.query(Evidence)
                .filter(Evidence.session_id == session_id)
                .order_by(Evidence.id)
                .limit(limit)
                .all()
            )
            return [e.as_dict() for e in rows]
