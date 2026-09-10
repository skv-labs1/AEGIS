"""Measuring whether a remediation worked.

Aegis snapshots the subject's health before and after an action and compares
the two. The agent states its own verdict separately. When the two disagree in
the direction that flatters the agent, the workflow refuses to resolve the
incident as fixed and records the disagreement.

Which metrics count, and by how much, is configuration. A different endpoint
platform with a different health model needs new paths here, not new code.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class Verdict(str, enum.Enum):
    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    NOT_RESOLVED = "not_resolved"
    UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True)
class MetricSpec:
    path: str
    direction: str  # increase | decrease | informational
    min_change: float = 0.0


@dataclass(frozen=True)
class VerificationConfig:
    health_tool: str
    subject_argument: str
    primary: MetricSpec
    secondary: tuple[MetricSpec, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> VerificationConfig | None:
        if not raw:
            return None
        primary = raw.get("primary_metric") or {}
        return cls(
            health_tool=raw["health_tool"],
            subject_argument=raw.get("subject_argument", "device_id"),
            primary=MetricSpec(
                path=primary.get("path", "health_score"),
                direction=primary.get("direction", "increase"),
                min_change=float(primary.get("min_change", 0.0)),
            ),
            secondary=tuple(
                MetricSpec(
                    path=m["path"],
                    direction=m.get("direction", "informational"),
                    min_change=float(m.get("min_change", 0.0)),
                )
                for m in (raw.get("secondary_metrics") or [])
            ),
        )


def read_path(payload: dict[str, Any] | None, path: str) -> Any:
    """Read a dotted path out of a nested result."""
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _improvement(before: Any, after: Any, direction: str) -> float | None:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    if direction == "increase":
        return float(after) - float(before)
    if direction == "decrease":
        return float(before) - float(after)
    return None


def compare(
    config: VerificationConfig,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[Verdict, dict[str, Any]]:
    """Compare two health snapshots and return a verdict plus the deltas."""
    if before is None or after is None:
        return Verdict.UNMEASURABLE, {
            "reason": "A before or after snapshot is missing, so the outcome cannot be measured."
        }

    metrics: list[dict[str, Any]] = []
    for spec in (config.primary, *config.secondary):
        b, a = read_path(before, spec.path), read_path(after, spec.path)
        entry: dict[str, Any] = {
            "metric": spec.path,
            "before": b,
            "after": a,
            "direction": spec.direction,
        }
        change = _improvement(b, a, spec.direction)
        if change is not None:
            entry["improvement"] = round(change, 2)
            entry["improved"] = change > 0
        metrics.append(entry)

    primary_change = _improvement(
        read_path(before, config.primary.path),
        read_path(after, config.primary.path),
        config.primary.direction,
    )

    if primary_change is None:
        verdict = Verdict.UNMEASURABLE
    elif primary_change >= config.primary.min_change:
        verdict = Verdict.RESOLVED
    elif primary_change > 0:
        verdict = Verdict.PARTIALLY_RESOLVED
    else:
        verdict = Verdict.NOT_RESOLVED

    return verdict, {
        "primary_metric": config.primary.path,
        "primary_change": round(primary_change, 2) if primary_change is not None else None,
        "min_change_for_resolved": config.primary.min_change,
        "metrics": metrics,
    }


# How optimistic each verdict is. A verdict claiming more than the measurement
# supports is the case that must be blocked.
_OPTIMISM = {
    Verdict.NOT_RESOLVED: 0,
    Verdict.UNMEASURABLE: 0,
    Verdict.PARTIALLY_RESOLVED: 1,
    Verdict.RESOLVED: 2,
}


def agreement(agent: Verdict, measured: Verdict) -> tuple[bool, str | None]:
    """Do the agent's verdict and the measurement agree?

    Returns (agreed, discrepancy). An agent claiming a better outcome than the
    numbers support is the failure that matters; the reverse is recorded but
    not blocked, because caution is not a fault.
    """
    if agent is measured:
        return True, None
    if _OPTIMISM[agent] > _OPTIMISM[measured]:
        return False, (
            f"The agent reported {agent.value!r} but the measured change is "
            f"{measured.value!r}. The claim is not supported by the before and after "
            "state, so this incident cannot be resolved as fixed."
        )
    return True, (
        f"The agent reported {agent.value!r} while the measurement shows "
        f"{measured.value!r}. The agent was more cautious than the numbers require; "
        "recorded, and not treated as a failure."
    )
