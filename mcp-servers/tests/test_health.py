"""The health score must be explainable: every deduction names its cause."""

from __future__ import annotations

from aegis_demo.common.health import band_for, score_device

HEALTHY = {
    "disk_used_pct": 50.0,
    "cpu_avg_pct": 30.0,
    "memory_used_pct": 55.0,
    "days_since_reboot": 4,
    "pending_reboot": False,
    "app_crashes_7d": {},
}


def test_healthy_device_scores_full_marks():
    result = score_device(HEALTHY)
    assert result.score == 100.0
    assert result.band == "healthy"
    assert result.as_dict()["penalties"] == []


def test_every_penalty_names_its_measurement():
    telemetry = dict(HEALTHY, disk_used_pct=97.0, app_crashes_7d={"OUTLOOK.EXE": 4})
    payload = score_device(telemetry, missing_critical_patches=2).as_dict()
    assert payload["penalties"], "a degraded device must report penalties"
    for penalty in payload["penalties"]:
        assert penalty["measurement"], "each penalty must state the measurement behind it"
        assert penalty["penalty"] > 0


def test_disk_penalty_cap_is_reachable():
    """A cap the metric can never reach is dead configuration, not a safety limit."""
    result = score_device(dict(HEALTHY, disk_used_pct=100.0))
    disk = next(c for c in result.components if c.name == "disk_utilisation")
    assert disk.penalty == 25.0, "a full disk must reach the documented disk cap"


def test_penalties_are_capped():
    """A single catastrophic metric must not drive the score to zero on its own."""
    result = score_device(dict(HEALTHY, app_crashes_7d={"A.EXE": 500}))
    crashes = next(c for c in result.components if c.name == "application_stability")
    assert crashes.penalty == 12.0


def test_score_never_negative():
    awful = {
        "disk_used_pct": 100.0,
        "cpu_avg_pct": 100.0,
        "memory_used_pct": 100.0,
        "days_since_reboot": 400,
        "pending_reboot": True,
        "app_crashes_7d": {"A.EXE": 50},
        "storage_predicted_failure": True,
        "storage_reallocated_sectors": 9000,
    }
    result = score_device(awful, missing_critical_patches=20, outdated_critical_software=20)
    assert result.score == 0.0


def test_bands():
    assert band_for(92) == "healthy"
    assert band_for(75) == "fair"
    assert band_for(55) == "degraded"
    assert band_for(20) == "critical"
