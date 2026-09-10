"""Recording and replaying an investigation.

Replay here means replaying *the model's decisions*, not the system's behaviour.
The recorded trace holds only what the agent chose to do, turn by turn. On
playback the engine, gateway, policy, approval gate, remediation and
verification all run for real against live state. So a replayed demo still
blocks for a human approval, still changes the simulated device, and still
verifies by measurement.

That distinction is what makes replay honest: nothing is faked except the model
call, which is the only part that costs money and can rate-limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRACE_DIR = Path(__file__).resolve().parents[2] / "traces"
SCHEMA_VERSION = 1


@dataclass
class TraceTurn:
    """One model turn: either tool calls, or a closing message."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    text: str | None = None
    latency_ms: float = 900.0

    def as_dict(self) -> dict[str, Any]:
        return {"tool_calls": self.tool_calls, "text": self.text, "latency_ms": self.latency_ms}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TraceTurn:
        return cls(
            tool_calls=raw.get("tool_calls") or [],
            text=raw.get("text"),
            latency_ms=float(raw.get("latency_ms", 900.0)),
        )


@dataclass
class Trace:
    """A recorded or authored run, replayable against live systems."""

    incident_number: str
    turns: list[TraceTurn]
    origin: str = "recorded"  # recorded | authored
    provider: str = "unknown"
    model: str = "unknown"
    recorded_at: str = ""
    notes: str = ""

    @property
    def is_recorded(self) -> bool:
        return self.origin == "recorded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "incident_number": self.incident_number,
            "origin": self.origin,
            "provider": self.provider,
            "model": self.model,
            "recorded_at": self.recorded_at,
            "notes": self.notes,
            "turns": [t.as_dict() for t in self.turns],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Trace:
        version = int(raw.get("schema_version", 1))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Trace uses schema version {version}; this build understands "
                f"{SCHEMA_VERSION}. Re-record it."
            )
        return cls(
            incident_number=raw["incident_number"],
            turns=[TraceTurn.from_dict(t) for t in raw.get("turns", [])],
            origin=raw.get("origin", "recorded"),
            provider=raw.get("provider", "unknown"),
            model=raw.get("model", "unknown"),
            recorded_at=raw.get("recorded_at", ""),
            notes=raw.get("notes", ""),
        )

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> Trace:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


class TraceRecorder:
    """Collects the model's turns during a live run."""

    def __init__(self, incident_number: str) -> None:
        self.incident_number = incident_number
        self.turns: list[TraceTurn] = []
        self.provider = "unknown"
        self.model = "unknown"

    def observe(self, completion: Any) -> None:
        self.provider = completion.provider
        self.model = completion.model
        self.turns.append(
            TraceTurn(
                tool_calls=[
                    {"name": call.name, "arguments": call.arguments}
                    for call in completion.tool_calls
                ],
                text=completion.text,
                latency_ms=completion.latency_ms or 900.0,
            )
        )

    def finish(self, notes: str = "") -> Trace:
        return Trace(
            incident_number=self.incident_number,
            turns=self.turns,
            origin="recorded",
            provider=self.provider,
            model=self.model,
            recorded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            notes=notes,
        )


def available_traces(directory: Path | None = None) -> dict[str, Trace]:
    """Every trace on disk, keyed by incident number."""
    target = directory or TRACE_DIR
    if not target.is_dir():
        return {}
    traces: dict[str, Trace] = {}
    for path in sorted(target.glob("*.json")):
        try:
            trace = Trace.load(path)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        traces[trace.incident_number.upper()] = trace
    return traces
