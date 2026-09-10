"""The simulator is what makes verification meaningful, so its arithmetic must hold."""

from __future__ import annotations

import pytest

from aegis_demo.common import db as dbmod
from aegis_demo.endpoint import simulator


@pytest.fixture()
def conn(demo_db):
    connection = dbmod.connect(demo_db)
    yield connection
    connection.close()


def test_reclaimed_space_matches_the_disk_change(conn):
    """Reported GB reclaimed must equal the actual change in used space."""
    report = simulator.clear_disk_space(conn, "DEV-4411")
    total_gb = 512.0
    before_gb = report["disk_used_pct_before"] / 100 * total_gb
    after_gb = report["disk_used_pct_after"] / 100 * total_gb
    assert abs((before_gb - after_gb) - report["reclaimed_gb"]) < 0.6


def test_clearing_twice_reclaims_nothing_further(conn):
    first = simulator.clear_disk_space(conn, "DEV-4411")
    second = simulator.clear_disk_space(conn, "DEV-4411")
    assert first["reclaimed_gb"] > 0
    assert second["reclaimed_gb"] == 0
    assert second["disk_used_pct_after"] == first["disk_used_pct_after"]


def test_selective_cleanup_only_clears_what_was_asked(conn):
    report = simulator.clear_disk_space(conn, "DEV-4411", ["temp_files"])
    assert report["reclaimed_gb"] == 9.0
    assert [c["category"] for c in report["categories_cleared"]] == ["temp_files"]

    remaining = conn.execute(
        "SELECT COUNT(*) c FROM disk_reclaimable WHERE device_id='DEV-4411' AND reclaimed=0"
    ).fetchone()["c"]
    assert remaining == 5


def test_pressure_relief_only_applies_when_crossing_the_threshold(conn):
    """A device that was never under disk pressure gets no CPU windfall."""
    before = simulator.read_telemetry(conn, "DEV-4412")
    report = simulator.clear_disk_space(conn, "DEV-4412")
    assert report["cpu_avg_pct_after"] == pytest.approx(before["cpu_avg_pct"])
    assert "No secondary pressure relief" in report["side_effects"]


def test_restart_clears_only_the_named_process(conn):
    report = simulator.restart_application(conn, "DEV-4411", "OUTLOOK")
    assert report["crash_count_cleared"] == 4
    assert report["remaining_crash_counts"] == {"EXCEL.EXE": 1}


def test_restart_normalises_process_name(conn):
    for name in ("outlook", "OUTLOOK.EXE", "Outlook"):
        simulator.clear_disk_space(conn, "DEV-4412")  # no-op on crash counts
        report = simulator.restart_application(conn, "DEV-4411", name)
        assert report["process"] == "OUTLOOK.EXE"


def test_actions_are_written_to_the_action_log(conn):
    simulator.clear_disk_space(conn, "DEV-4411")
    simulator.restart_application(conn, "DEV-4411", "OUTLOOK")
    rows = conn.execute("SELECT action, target FROM action_log ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["clear_disk_space", "restart_application"]
    assert all(r["target"] == "DEV-4411" for r in rows)


def test_unknown_device_raises(conn):
    with pytest.raises(KeyError):
        simulator.clear_disk_space(conn, "DEV-0000")
    with pytest.raises(KeyError):
        simulator.restart_application(conn, "DEV-0000", "OUTLOOK")
