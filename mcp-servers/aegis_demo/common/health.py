"""Deterministic device health scoring.

The score exists so remediation can be *verified* numerically rather than
assumed. It is intentionally a transparent weighted-penalty model, not a
black box: every point deducted is attributable to a named component with the
measurement that caused it, which is what makes the agent's before/after
comparison checkable by a human.

Score is 100 minus the sum of component penalties, floored at 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (threshold, points per unit over threshold, maximum penalty)
DISK_RULE = (75.0, 1.00, 25.0)  # 1 point per % over 75; reaches the 25-point cap at 100%
CPU_RULE = (60.0, 0.35, 15.0)
MEMORY_RULE = (75.0, 0.30, 10.0)
UPTIME_RULE = (30.0, 0.10, 5.0)

CRASH_POINTS_EACH = 2.0
CRASH_MAX = 12.0
MISSING_CRITICAL_PATCH_POINTS_EACH = 2.0
MISSING_PATCH_MAX = 10.0
OUTDATED_CRITICAL_SOFTWARE_POINTS_EACH = 6.0
OUTDATED_SOFTWARE_MAX = 12.0
PENDING_REBOOT_POINTS = 4.0
FAILING_STORAGE_POINTS = 30.0


def _threshold_penalty(value: float, rule: tuple[float, float, float]) -> float:
    threshold, per_unit, cap = rule
    if value <= threshold:
        return 0.0
    return min((value - threshold) * per_unit, cap)


@dataclass
class HealthComponent:
    name: str
    measurement: str
    penalty: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.name,
            "measurement": self.measurement,
            "penalty": round(self.penalty, 1),
        }


@dataclass
class HealthResult:
    score: float
    band: str
    components: list[HealthComponent] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        contributing = [c.as_dict() for c in self.components if c.penalty > 0]
        contributing.sort(key=lambda c: c["penalty"], reverse=True)
        return {
            "health_score": round(self.score, 1),
            "band": self.band,
            "scoring_model": "100 minus weighted penalties; see aegis_demo/common/health.py",
            "penalties": contributing,
            "total_penalty": round(sum(c.penalty for c in self.components), 1),
        }


def band_for(score: float) -> str:
    if score >= 85:
        return "healthy"
    if score >= 70:
        return "fair"
    if score >= 50:
        return "degraded"
    return "critical"


def score_device(
    telemetry: dict[str, Any],
    *,
    missing_critical_patches: int = 0,
    outdated_critical_software: int = 0,
) -> HealthResult:
    """Compute a health score from current telemetry and posture counts."""
    components: list[HealthComponent] = []

    disk = float(telemetry.get("disk_used_pct", 0.0))
    components.append(
        HealthComponent(
            "disk_utilisation", f"{disk:.1f}% used", _threshold_penalty(disk, DISK_RULE)
        )
    )

    cpu = float(telemetry.get("cpu_avg_pct", 0.0))
    components.append(
        HealthComponent("cpu_utilisation", f"{cpu:.1f}% average", _threshold_penalty(cpu, CPU_RULE))
    )

    memory = float(telemetry.get("memory_used_pct", 0.0))
    components.append(
        HealthComponent(
            "memory_pressure", f"{memory:.1f}% used", _threshold_penalty(memory, MEMORY_RULE)
        )
    )

    crashes = telemetry.get("app_crashes_7d") or {}
    crash_total = sum(int(v) for v in crashes.values())
    if crash_total:
        detail = ", ".join(f"{k} x{v}" for k, v in sorted(crashes.items()))
    else:
        detail = "no application crashes in 7 days"
    components.append(
        HealthComponent(
            "application_stability", detail, min(crash_total * CRASH_POINTS_EACH, CRASH_MAX)
        )
    )

    components.append(
        HealthComponent(
            "patch_compliance",
            f"{missing_critical_patches} critical patch(es) missing",
            min(missing_critical_patches * MISSING_CRITICAL_PATCH_POINTS_EACH, MISSING_PATCH_MAX),
        )
    )

    components.append(
        HealthComponent(
            "software_currency",
            f"{outdated_critical_software} business-critical application(s) outdated",
            min(
                outdated_critical_software * OUTDATED_CRITICAL_SOFTWARE_POINTS_EACH,
                OUTDATED_SOFTWARE_MAX,
            ),
        )
    )

    uptime = float(telemetry.get("days_since_reboot", 0))
    components.append(
        HealthComponent(
            "uptime", f"{uptime:.0f} days since reboot", _threshold_penalty(uptime, UPTIME_RULE)
        )
    )

    pending = bool(telemetry.get("pending_reboot"))
    components.append(
        HealthComponent(
            "pending_reboot",
            "reboot pending" if pending else "no reboot pending",
            PENDING_REBOOT_POINTS if pending else 0.0,
        )
    )

    if telemetry.get("storage_predicted_failure"):
        sectors = telemetry.get("storage_reallocated_sectors", "unknown")
        components.append(
            HealthComponent(
                "storage_hardware",
                f"SMART predicts failure; {sectors} reallocated sectors",
                FAILING_STORAGE_POINTS,
            )
        )

    total = sum(c.penalty for c in components)
    score = max(0.0, 100.0 - total)
    return HealthResult(score=score, band=band_for(score), components=components)
