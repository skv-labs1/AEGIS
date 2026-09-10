"""Device state simulator.

Remediation in this demo is not a no-op that returns "success". Each action
mutates the synthetic device's stored telemetry through this module, so the
health read that follows a remediation reflects what the action actually did.
That is what makes the verification step in Aegis meaningful: the numbers move
because state changed, not because a script said they should.

Effects are deterministic and safe to repeat: clearing disk space a second time
reclaims nothing further, because reclaimed categories are marked consumed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..common import db as dbmod

# Categories a routine cleanup may reclaim without user consent.
SAFE_CATEGORIES = {
    "temp_files",
    "windows_update_cache",
    "browser_and_teams_cache",
    "recycle_bin",
    "windows_old",
    "orphaned_outlook_data_files",
}

# Below this disk utilisation, paging and antivirus indexing pressure eases and
# CPU/memory drift back toward the device's measured baseline.
PRESSURE_RELIEF_THRESHOLD = 85.0
RELIEF_FRACTION = 0.80


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_telemetry(conn: sqlite3.Connection, device_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM device_telemetry WHERE device_id = ?", (device_id,)
    ).fetchone()
    if row is None:
        return None
    telemetry = {
        "disk_used_pct": row["disk_used_pct"],
        "cpu_avg_pct": row["cpu_avg_pct"],
        "cpu_baseline_pct": row["cpu_baseline_pct"],
        "memory_used_pct": row["memory_used_pct"],
        "memory_baseline_pct": row["memory_baseline_pct"],
        "days_since_reboot": row["days_since_reboot"],
        "pending_reboot": bool(row["pending_reboot"]),
        "app_crashes_7d": json.loads(row["app_crashes_7d"]),
    }
    telemetry.update(json.loads(row["extra"]))
    return telemetry


def _write_telemetry(conn: sqlite3.Connection, device_id: str, tel: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE device_telemetry SET disk_used_pct=?, cpu_avg_pct=?, memory_used_pct=?,"
        " days_since_reboot=?, pending_reboot=?, app_crashes_7d=?, updated_at=?"
        " WHERE device_id=?",
        (
            round(float(tel["disk_used_pct"]), 1),
            round(float(tel["cpu_avg_pct"]), 1),
            round(float(tel["memory_used_pct"]), 1),
            int(tel["days_since_reboot"]),
            int(bool(tel["pending_reboot"])),
            dbmod.dumps(tel.get("app_crashes_7d") or {}),
            _now_iso(),
            device_id,
        ),
    )


def _log(conn: sqlite3.Connection, action: str, target: str, detail: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO action_log (occurred_at, system, action, target, detail)"
        " VALUES (?, 'endpoint', ?, ?, ?)",
        (_now_iso(), action, target, dbmod.dumps(detail)),
    )


def _relieve_pressure(tel: dict[str, Any], disk_before: float, disk_after: float) -> None:
    """Ease CPU and memory toward baseline when disk pressure is removed."""
    if disk_before < PRESSURE_RELIEF_THRESHOLD or disk_after >= PRESSURE_RELIEF_THRESHOLD:
        return
    for current_key, baseline_key in (
        ("cpu_avg_pct", "cpu_baseline_pct"),
        ("memory_used_pct", "memory_baseline_pct"),
    ):
        current = float(tel[current_key])
        baseline = float(tel[baseline_key])
        if current > baseline:
            tel[current_key] = current - (current - baseline) * RELIEF_FRACTION


def clear_disk_space(
    conn: sqlite3.Connection, device_id: str, categories: list[str] | None = None
) -> dict[str, Any]:
    """Reclaim disk space on a device. Returns a before/after report."""
    device = conn.execute("SELECT record FROM devices WHERE device_id = ?", (device_id,)).fetchone()
    if device is None:
        raise KeyError(f"Unknown device: {device_id}")
    total_gb = float(json.loads(device["record"])["total_disk_gb"])

    tel = read_telemetry(conn, device_id)
    if tel is None:
        raise KeyError(f"No telemetry for device: {device_id}")

    rows = conn.execute(
        "SELECT id, category, gb, detail FROM disk_reclaimable"
        " WHERE device_id = ? AND reclaimed = 0",
        (device_id,),
    ).fetchall()

    requested = set(categories) if categories else set(SAFE_CATEGORIES)
    unsafe = requested - SAFE_CATEGORIES
    if unsafe:
        raise ValueError(
            f"Categories not permitted for automated cleanup: {sorted(unsafe)}. "
            f"Permitted: {sorted(SAFE_CATEGORIES)}"
        )

    selected = [r for r in rows if r["category"] in requested]
    reclaimed_gb = sum(float(r["gb"]) for r in selected)

    disk_before = float(tel["disk_used_pct"])
    used_before_gb = disk_before / 100.0 * total_gb
    used_after_gb = max(0.0, used_before_gb - reclaimed_gb)
    disk_after = round(used_after_gb / total_gb * 100.0, 1)

    cpu_before, mem_before = float(tel["cpu_avg_pct"]), float(tel["memory_used_pct"])
    tel["disk_used_pct"] = disk_after
    _relieve_pressure(tel, disk_before, disk_after)
    _write_telemetry(conn, device_id, tel)

    for r in selected:
        conn.execute("UPDATE disk_reclaimable SET reclaimed = 1 WHERE id = ?", (r["id"],))

    report = {
        "device_id": device_id,
        "action": "clear_disk_space",
        "reclaimed_gb": round(reclaimed_gb, 1),
        "categories_cleared": [
            {"category": r["category"], "gb": r["gb"], "detail": r["detail"]} for r in selected
        ],
        "categories_requested_but_already_clear": sorted(
            requested - {r["category"] for r in selected}
        ),
        "disk_used_pct_before": round(disk_before, 1),
        "disk_used_pct_after": disk_after,
        "disk_free_gb_after": round(total_gb - used_after_gb, 1),
        "cpu_avg_pct_before": round(cpu_before, 1),
        "cpu_avg_pct_after": round(float(tel["cpu_avg_pct"]), 1),
        "memory_used_pct_before": round(mem_before, 1),
        "memory_used_pct_after": round(float(tel["memory_used_pct"]), 1),
        "side_effects": (
            "Disk utilisation fell below the paging-pressure threshold; CPU and memory "
            "eased toward their measured baselines."
            if disk_before >= PRESSURE_RELIEF_THRESHOLD > disk_after
            else "No secondary pressure relief observed."
        ),
        "completed_at": _now_iso(),
    }
    _log(conn, "clear_disk_space", device_id, report)
    return report


def restart_application(
    conn: sqlite3.Connection, device_id: str, process_name: str
) -> dict[str, Any]:
    """Restart an application, clearing its recorded crash count."""
    tel = read_telemetry(conn, device_id)
    if tel is None:
        raise KeyError(f"Unknown device: {device_id}")

    process = process_name.upper()
    if not process.endswith(".EXE"):
        process = f"{process}.EXE"

    crashes = dict(tel.get("app_crashes_7d") or {})
    cleared = crashes.pop(process, 0)
    tel["app_crashes_7d"] = crashes

    mem_before = float(tel["memory_used_pct"])
    baseline = float(tel["memory_baseline_pct"])
    if mem_before > baseline:
        tel["memory_used_pct"] = mem_before - min(4.0, (mem_before - baseline) * 0.35)
    _write_telemetry(conn, device_id, tel)

    report = {
        "device_id": device_id,
        "action": "restart_application",
        "process": process,
        "was_running": True,
        "crash_count_cleared": cleared,
        "remaining_crash_counts": crashes,
        "memory_used_pct_before": round(mem_before, 1),
        "memory_used_pct_after": round(float(tel["memory_used_pct"]), 1),
        "note": (
            f"{process} was restarted. Its 7-day crash counter reset from {cleared} to 0. "
            "Whether crashes recur depends on whether the underlying cause was addressed."
        ),
        "completed_at": _now_iso(),
    }
    _log(conn, "restart_application", device_id, report)
    return report
