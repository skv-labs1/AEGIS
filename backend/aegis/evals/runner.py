"""Running the eval scenarios.

Each scenario reseeds the synthetic enterprise, runs an investigation, and
scores it from the audit trail. Results are stored so the metrics page can
compare providers over time.

Two honesty rules are built in:

- The provider that produced a run is recorded with the result, because a score
  from a replayed trace says nothing about a model and must never be presented
  as if it did.
- Scoring reads the record, not the agent's summary of itself.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db.models import AuditEvent, EvalResult, EvalRun, Investigation, Proposal, Verification
from ..db.session import session_scope
from ..engine.loop import AgentEngine
from ..engine.providers.registry import ProviderChain
from .scoring import Score, score_investigation

logger = logging.getLogger(__name__)

SCENARIO_FILE = Path(__file__).resolve().parents[2] / "scenarios" / "scenarios.json"


@dataclass
class Scenario:
    id: str
    incident_number: str
    title: str
    expectations: dict[str, Any]
    why_it_matters: str = ""
    variant: str | None = None
    seed_incident: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scenario:
        return cls(
            id=raw["id"],
            incident_number=raw["incident_number"],
            title=raw["title"],
            expectations=raw.get("expectations") or {},
            why_it_matters=raw.get("why_it_matters", ""),
            variant=raw.get("variant"),
            seed_incident=raw.get("seed_incident"),
        )


def load_scenarios(path: Path | None = None) -> list[Scenario]:
    with open(path or SCENARIO_FILE, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [Scenario.from_dict(s) for s in raw.get("scenarios", [])]


@dataclass
class ScenarioOutcome:
    scenario: Scenario
    score: Score
    provider: str
    model: str
    turns: int
    tool_calls: int
    duration_ms: float
    finished: str
    error: str | None = None
    investigation_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario.id,
            "title": self.scenario.title,
            "provider": self.provider,
            "model": self.model,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "duration_ms": round(self.duration_ms, 1),
            "finished": self.finished,
            "error": self.error,
            "investigation_id": self.investigation_id,
            **self.score.as_dict(),
        }


@dataclass
class EvalReport:
    outcomes: list[ScenarioOutcome] = field(default_factory=list)
    provider: str = "unknown"
    model: str = "unknown"
    replayed: bool = False

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.score.passed)

    @property
    def mean_score(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(o.score.fraction for o in self.outcomes) / len(self.outcomes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "replayed": self.replayed,
            "scenarios": len(self.outcomes),
            "passed": self.passed,
            "mean_score": round(self.mean_score, 3),
            "results": [o.as_dict() for o in self.outcomes],
            "caveat": (
                "These results come from replayed traces, so they measure the harness and "
                "the governance, not a model. Run with a provider key for model results."
                if self.replayed
                else None
            ),
        }


def read_record(incident_number: str) -> tuple[dict | None, list, list, list]:
    """Everything a scorer needs, read from Aegis's own record."""
    with session_scope() as db:
        investigation = (
            db.query(Investigation)
            .filter(Investigation.incident_number == incident_number.upper())
            .order_by(Investigation.id.desc())
            .first()
        )
        if investigation is None:
            return None, [], [], []
        events = [
            e.as_dict()
            for e in db.query(AuditEvent)
            .filter(AuditEvent.investigation_id == investigation.id)
            .order_by(AuditEvent.id)
            .all()
        ]
        proposals = [
            p.as_dict()
            for p in db.query(Proposal)
            .filter(Proposal.investigation_id == investigation.id)
            .all()
        ]
        verifications = [
            v.as_dict()
            for v in db.query(Verification)
            .filter(Verification.investigation_id == investigation.id)
            .all()
        ]
        return investigation.as_dict(), events, proposals, verifications


async def run_scenario(
    scenario: Scenario,
    tool_source: Any,
    chain: ProviderChain,
    *,
    reseed: Any = None,
    seed_incident: Any = None,
) -> ScenarioOutcome:
    """Run one scenario against a freshly seeded enterprise."""
    if reseed is not None:
        reseed()
    if scenario.seed_incident and seed_incident is not None:
        await seed_incident(scenario)

    engine = AgentEngine(tool_source, chain)
    result = await engine.run(scenario.incident_number)
    investigation, events, proposals, verifications = read_record(scenario.incident_number)
    score = score_investigation(scenario.expectations, investigation, events, proposals, verifications)

    return ScenarioOutcome(
        scenario=scenario,
        score=score,
        provider=result.providers_used[0] if result.providers_used else "unknown",
        model=result.models_used[0] if result.models_used else "unknown",
        turns=result.turns,
        tool_calls=result.tool_calls,
        duration_ms=result.duration_ms,
        finished=result.finished,
        error=result.error,
        investigation_id=result.investigation_id,
    )


def store(report: EvalReport, label: str = "") -> int:
    """Persist a report so the metrics page can show history."""
    with session_scope() as db:
        run = EvalRun(
            label=label or report.provider,
            provider=report.provider,
            model=report.model,
            replayed=report.replayed,
            scenarios=len(report.outcomes),
            passed=report.passed,
            mean_score=report.mean_score,
        )
        db.add(run)
        db.flush()
        for outcome in report.outcomes:
            db.add(
                EvalResult(
                    run_id=run.id,
                    scenario_id=outcome.scenario.id,
                    title=outcome.scenario.title,
                    passed=outcome.score.passed,
                    score=outcome.score.fraction,
                    turns=outcome.turns,
                    tool_calls=outcome.tool_calls,
                    duration_ms=outcome.duration_ms,
                    detail=outcome.as_dict(),
                )
            )
        return run.id
