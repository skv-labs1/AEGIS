"""Build ``enterprise_demo.db`` from the JSON seed files.

Seed files are the source of truth and live in version control. The database is
rebuilt from them on demand, which is what makes every demo run and every eval
scenario start from identical state.

Dates in the seed files are stored as *relative offsets* (``opened_days_ago``,
``warranty_expires_in_days``) and resolved against the current time here, so the
demo data never looks stale no matter when it is run.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import db as dbmod
from .health import score_device

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
HISTORY_DAYS = 14


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load(name: str) -> dict[str, Any]:
    with open(SEED_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _posture_counts(
    software: dict[str, list[dict[str, Any]]], patches: dict[str, dict[str, Any]], device_id: str
) -> tuple[int, int]:
    """(missing critical patches, outdated business-critical apps) for a device."""
    missing = sum(
        1 for p in patches.get(device_id, {}).get("missing", []) if p.get("severity") == "critical"
    )
    outdated = sum(
        1
        for s in software.get(device_id, [])
        if s.get("is_outdated") and s.get("is_business_critical")
    )
    return missing, outdated


def _generate_history(
    device: dict[str, Any], missing_patches: int, outdated_software: int, now: datetime
) -> list[tuple[Any, ...]]:
    """Deterministic 14-day health history trending to the device's current state.

    Seeded by device id, so the same seed files always produce the same history.
    """
    tel = device["telemetry"]
    rng = random.Random(f"aegis::{device['device_id']}")
    rows: list[tuple[Any, ...]] = []

    disk_now = float(tel["disk_used_pct"])
    cpu_now = float(tel["cpu_avg_pct"])
    cpu_base = float(tel["cpu_baseline_pct"])
    mem_now = float(tel["memory_used_pct"])
    mem_base = float(tel["memory_baseline_pct"])
    crashes_now = sum(int(v) for v in (tel.get("app_crashes_7d") or {}).values())

    # Where the device was 14 days ago: disk grows, cpu/memory sit near baseline.
    disk_then = max(20.0, disk_now - min(24.0, disk_now * 0.22))

    for offset in range(HISTORY_DAYS, -1, -1):
        progress = (HISTORY_DAYS - offset) / HISTORY_DAYS
        captured = now - timedelta(days=offset)
        disk = disk_then + (disk_now - disk_then) * progress + rng.uniform(-0.4, 0.4)
        # Contention appears late, as disk pressure builds.
        ramp = max(0.0, (progress - 0.45) / 0.55)
        cpu = cpu_base + (cpu_now - cpu_base) * ramp + rng.uniform(-1.5, 1.5)
        mem = mem_base + (mem_now - mem_base) * ramp + rng.uniform(-1.0, 1.0)
        # Crashes only in the trailing 7 days, roughly matching the 7-day total.
        if offset < 7 and crashes_now:
            crashes_24h = 1 if rng.random() < (crashes_now / 7.0) else 0
        else:
            crashes_24h = 0

        point = {
            "disk_used_pct": disk,
            "cpu_avg_pct": cpu,
            "memory_used_pct": mem,
            "days_since_reboot": max(0, int(tel["days_since_reboot"]) - offset),
            "pending_reboot": bool(tel.get("pending_reboot")) and offset < 10,
            "app_crashes_7d": {"total": crashes_24h * 7} if crashes_24h else {},
            "storage_predicted_failure": tel.get("storage_predicted_failure"),
            "storage_reallocated_sectors": tel.get("storage_reallocated_sectors"),
        }
        result = score_device(
            point,
            missing_critical_patches=missing_patches if offset < 12 else 0,
            outdated_critical_software=outdated_software,
        )
        rows.append(
            (
                device["device_id"],
                iso(captured),
                round(result.score, 1),
                round(disk, 1),
                round(cpu, 1),
                round(mem, 1),
                crashes_24h,
            )
        )
    return rows


def build(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, int]:
    """Drop and rebuild every table from the seed files. Returns row counts."""
    now = now or _now()
    dbmod.init_schema(conn)

    for table in (
        "users",
        "devices",
        "device_telemetry",
        "disk_reclaimable",
        "health_history",
        "software",
        "patches",
        "patch_scans",
        "assets",
        "incidents",
        "work_notes",
        "action_log",
        "meta",
    ):
        conn.execute(f"DELETE FROM {table}")

    users = _load("users")["users"]
    devices = _load("devices")["devices"]
    assets = _load("assets")["assets"]
    software_raw = _load("software")["software"]
    patches_raw = _load("patches")["patches"]
    incidents = _load("incidents")["incidents"]

    software_by_device = {s["device_id"]: s["installed"] for s in software_raw}
    patches_by_device = {p["device_id"]: p for p in patches_raw}

    for u in users:
        conn.execute(
            "INSERT INTO users (user_id, display_name, email, department, job_title, vip, record)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                u["user_id"],
                u["display_name"],
                u["email"],
                u.get("department"),
                u.get("job_title"),
                int(bool(u.get("vip"))),
                dbmod.dumps(u),
            ),
        )

    for d in devices:
        tel = d["telemetry"]
        record = {k: v for k, v in d.items() if k not in ("telemetry", "disk_reclaimable")}
        record["last_seen_at"] = iso(now - timedelta(hours=d.get("last_seen_hours_ago", 0)))
        conn.execute(
            "INSERT INTO devices (device_id, hostname, serial_number, primary_user_id,"
            " form_factor, compliance_state, record) VALUES (?,?,?,?,?,?,?)",
            (
                d["device_id"],
                d["hostname"],
                d.get("serial_number"),
                d.get("primary_user_id"),
                d.get("form_factor"),
                d.get("compliance_state"),
                dbmod.dumps(record),
            ),
        )
        extra = {k: v for k, v in tel.items() if k.startswith("storage_")}
        conn.execute(
            "INSERT INTO device_telemetry (device_id, disk_used_pct, cpu_avg_pct,"
            " cpu_baseline_pct, memory_used_pct, memory_baseline_pct, days_since_reboot,"
            " pending_reboot, app_crashes_7d, extra, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                d["device_id"],
                tel["disk_used_pct"],
                tel["cpu_avg_pct"],
                tel["cpu_baseline_pct"],
                tel["memory_used_pct"],
                tel["memory_baseline_pct"],
                tel["days_since_reboot"],
                int(bool(tel.get("pending_reboot"))),
                dbmod.dumps(tel.get("app_crashes_7d") or {}),
                dbmod.dumps(extra),
                iso(now),
            ),
        )
        for item in d.get("disk_reclaimable", []):
            conn.execute(
                "INSERT INTO disk_reclaimable (device_id, category, gb, detail, reclaimed)"
                " VALUES (?,?,?,?,0)",
                (d["device_id"], item["category"], item["gb"], item.get("detail")),
            )

        missing, outdated = _posture_counts(software_by_device, patches_by_device, d["device_id"])
        conn.executemany(
            "INSERT INTO health_history (device_id, captured_at, health_score, disk_used_pct,"
            " cpu_avg_pct, memory_used_pct, app_crashes_24h) VALUES (?,?,?,?,?,?,?)",
            _generate_history(d, missing, outdated, now),
        )

    for device_id, installed in software_by_device.items():
        for s in installed:
            conn.execute(
                "INSERT INTO software (device_id, name, installed_version,"
                " latest_available_version, is_outdated, is_business_critical, record)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    device_id,
                    s["name"],
                    s["installed_version"],
                    s.get("latest_available_version"),
                    int(bool(s.get("is_outdated"))),
                    int(bool(s.get("is_business_critical"))),
                    dbmod.dumps(s),
                ),
            )

    for device_id, entry in patches_by_device.items():
        conn.execute(
            "INSERT INTO patch_scans (device_id, last_scan_at) VALUES (?,?)",
            (device_id, iso(now - timedelta(hours=entry.get("last_scan_hours_ago", 0)))),
        )
        for p in entry.get("missing", []):
            record = dict(p)
            record["released_at"] = iso(now - timedelta(days=p.get("released_days_ago", 0)))
            conn.execute(
                "INSERT INTO patches (device_id, patch_id, status, severity, record)"
                " VALUES (?,?,'missing',?,?)",
                (device_id, p["patch_id"], p.get("severity"), dbmod.dumps(record)),
            )
        for p in entry.get("installed_recent", []):
            record = dict(p)
            record["installed_at"] = iso(now - timedelta(days=p.get("installed_days_ago", 0)))
            conn.execute(
                "INSERT INTO patches (device_id, patch_id, status, severity, record)"
                " VALUES (?,?,'installed',?,?)",
                (device_id, p["patch_id"], p.get("severity"), dbmod.dumps(record)),
            )

    for a in assets:
        record = dict(a)
        record["purchased_at"] = iso(now - timedelta(days=a.get("purchase_days_ago", 0)))
        record["warranty_expires_at"] = iso(
            now + timedelta(days=a.get("warranty_expires_in_days", 0))
        )
        record["warranty_active"] = a.get("warranty_expires_in_days", 0) > 0
        record["refresh_due_at"] = iso(now + timedelta(days=a.get("refresh_due_in_days", 0)))
        record["refresh_overdue"] = a.get("refresh_due_in_days", 0) < 0
        conn.execute(
            "INSERT INTO assets (asset_tag, device_id, assigned_user_id, category, subcategory,"
            " lifecycle_state, record) VALUES (?,?,?,?,?,?,?)",
            (
                a["asset_tag"],
                a.get("device_id"),
                a.get("assigned_user_id"),
                a.get("category"),
                a.get("subcategory"),
                a.get("lifecycle_state"),
                dbmod.dumps(record),
            ),
        )

    for i in incidents:
        opened = now - timedelta(
            days=i.get("opened_days_ago", 0), hours=i.get("opened_hours_ago", 0)
        )
        resolved = (
            now - timedelta(days=i["resolved_days_ago"])
            if i.get("resolved_days_ago") is not None
            else None
        )
        record = {k: v for k, v in i.items() if k != "work_notes"}
        record["opened_at"] = iso(opened)
        record["resolved_at"] = iso(resolved) if resolved else None
        conn.execute(
            "INSERT INTO incidents (number, short_description, caller_id, device_id, state,"
            " priority, category, opened_at, resolved_at, record) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                i["number"],
                i["short_description"],
                i.get("caller_id"),
                i.get("device_id"),
                i["state"],
                i["priority"],
                i.get("category"),
                iso(opened),
                iso(resolved) if resolved else None,
                dbmod.dumps(record),
            ),
        )
        for note in i.get("work_notes", []):
            conn.execute(
                "INSERT INTO work_notes (incident_number, created_at, author_id, author_label, text)"
                " VALUES (?,?,?,?,?)",
                (
                    i["number"],
                    iso(now - timedelta(days=note.get("days_ago", 0))),
                    note.get("author_id"),
                    note.get("author_label"),
                    note["text"],
                ),
            )

    conn.execute("INSERT INTO meta (key, value) VALUES ('seeded_at', ?)", (iso(now),))
    conn.execute("INSERT INTO meta (key, value) VALUES ('seed_version', ?)", ("0.1.0",))

    counts = {}
    for table in (
        "users",
        "devices",
        "assets",
        "software",
        "patches",
        "incidents",
        "work_notes",
        "health_history",
        "disk_reclaimable",
    ):
        counts[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return counts


def reseed(path: Path | None = None) -> dict[str, int]:
    conn = dbmod.connect(path)
    try:
        return build(conn)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the synthetic enterprise database")
    parser.add_argument("--db", type=Path, default=None, help="database path")
    args = parser.parse_args()
    target = args.db or dbmod.db_path()
    counts = reseed(target)
    print(f"Seeded {target}")
    for table, n in counts.items():
        print(f"  {table:<18} {n}")


if __name__ == "__main__":
    main()
