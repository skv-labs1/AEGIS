"""The database is rebuilt from seed files, so seeding must be complete and repeatable."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from aegis_demo.common import db as dbmod


def _iso_to_dt(value: str) -> datetime:
    # Python 3.11+ parses the trailing Z directly.
    return datetime.fromisoformat(value)


def test_all_tables_populated(demo_db):
    conn = dbmod.connect(demo_db)
    try:
        for table, minimum in [
            ("users", 8),
            ("devices", 8),
            ("assets", 10),
            ("software", 20),
            ("patches", 10),
            ("incidents", 10),
            ("health_history", 100),
        ]:
            count = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            assert count >= minimum, f"{table} has {count} rows, expected at least {minimum}"
    finally:
        conn.close()


def test_relative_dates_resolve_to_now(demo_db):
    """Seed offsets become absolute timestamps, so the demo never looks stale."""
    conn = dbmod.connect(demo_db)
    try:
        row = conn.execute("SELECT record FROM incidents WHERE number='INC-1042'").fetchone()
        opened = _iso_to_dt(json.loads(row["record"])["opened_at"])
        age_hours = (datetime.now(UTC) - opened).total_seconds() / 3600
        assert 2.5 <= age_hours <= 3.5, f"INC-1042 should be ~3h old, got {age_hours:.1f}h"
    finally:
        conn.close()


def test_reseeding_is_idempotent(demo_db):
    """Reseeding replaces state rather than duplicating it, so Reset Demo is safe."""
    from aegis_demo.common import seed

    conn = dbmod.connect(demo_db)
    try:
        before = conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
        seed.build(conn)
        after = conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
        assert before == after
    finally:
        conn.close()


def test_history_is_deterministic(demo_db, tmp_path):
    """Same seed files must produce the same history, so evals are reproducible."""
    from aegis_demo.common import seed

    other = tmp_path / "second.db"
    conn2 = dbmod.connect(other)
    try:
        seed.build(conn2)
        rows2 = conn2.execute(
            "SELECT health_score FROM health_history WHERE device_id='DEV-4411'"
            " ORDER BY captured_at"
        ).fetchall()
    finally:
        conn2.close()

    conn1 = dbmod.connect(demo_db)
    try:
        rows1 = conn1.execute(
            "SELECT health_score FROM health_history WHERE device_id='DEV-4411'"
            " ORDER BY captured_at"
        ).fetchall()
    finally:
        conn1.close()

    assert [r["health_score"] for r in rows1] == [r["health_score"] for r in rows2]
