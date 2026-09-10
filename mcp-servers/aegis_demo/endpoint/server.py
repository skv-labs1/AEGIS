"""Demo endpoint management system exposed as an MCP server.

Stands in for Intune, Jamf, Ivanti Neurons, Workspace ONE or similar. Covers
device inventory, health telemetry, software inventory, patch status and two
remediation actions.

The remediation tools mutate stored device state through ``simulator``, so a
health read taken after an action reflects what the action actually changed.
All data is synthetic and no real endpoint is ever contacted.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..common import db as dbmod
from ..common import records
from . import simulator

SERVER_NAME = "aegis-demo-endpoint"
DEFAULT_PORT = 8803

server = MCPServer(
    name=SERVER_NAME,
    version="0.1.0",
    instructions=(
        "Synthetic endpoint management system for the Aegis demo. Provides device inventory, "
        "health telemetry with a transparent scoring breakdown, software inventory including "
        "known issues per version, patch status, and two remediation actions. Remediation "
        "changes device state, so health readings taken afterwards reflect the change. "
        "All devices are synthetic."
    ),
)


def _conn() -> sqlite3.Connection:
    return dbmod.connect()


@server.tool(
    name="get_device",
    title="Get device",
    description=(
        "Retrieve a device by device id, hostname or serial number, including hardware "
        "specification, operating system build, enrolment and compliance state."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_device(identifier: str) -> dict[str, Any]:
    """Get one device by device id, hostname or serial number."""
    conn = _conn()
    try:
        ident = identifier.upper()
        row = conn.execute(
            "SELECT record FROM devices WHERE upper(device_id) = ? OR upper(hostname) = ?"
            " OR upper(serial_number) = ?",
            (ident, ident, ident),
        ).fetchone()
        if row is None:
            return {"found": False, "identifier": identifier, "error": "Device not found"}
        device = json.loads(row["record"])
        user = (
            records.get_user(conn, device["primary_user_id"])
            if device.get("primary_user_id")
            else None
        )
        if user:
            device["primary_user"] = {
                "user_id": user["user_id"],
                "display_name": user["display_name"],
                "department": user.get("department"),
            }
        return {"found": True, "device": device}
    finally:
        conn.close()


@server.tool(
    name="get_device_health",
    title="Get device health",
    description=(
        "Current health telemetry for a device with a scored breakdown: disk, CPU, memory, "
        "application crashes, patch compliance, software currency, uptime and storage "
        "hardware. Every penalty names the measurement that caused it. Call this before and "
        "after a remediation to verify whether it worked."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_device_health(device_id: str) -> dict[str, Any]:
    """Current health score and telemetry for a device."""
    conn = _conn()
    try:
        device_row = conn.execute(
            "SELECT record FROM devices WHERE upper(device_id) = ?", (device_id.upper(),)
        ).fetchone()
        if device_row is None:
            return {"found": False, "device_id": device_id, "error": "Device not found"}
        device = json.loads(device_row["record"])
        telemetry = simulator.read_telemetry(conn, device["device_id"])
        if telemetry is None:
            return {"found": False, "device_id": device_id, "error": "No telemetry available"}

        total_gb = float(device["total_disk_gb"])
        used_gb = telemetry["disk_used_pct"] / 100.0 * total_gb
        reclaimable = conn.execute(
            "SELECT category, gb, detail FROM disk_reclaimable"
            " WHERE device_id = ? AND reclaimed = 0 ORDER BY gb DESC",
            (device["device_id"],),
        ).fetchall()

        health = records.compute_health(conn, device["device_id"], telemetry)
        return {
            "found": True,
            "device_id": device["device_id"],
            "hostname": device["hostname"],
            **health,
            "telemetry": {
                "disk_used_pct": round(telemetry["disk_used_pct"], 1),
                "disk_used_gb": round(used_gb, 1),
                "disk_free_gb": round(total_gb - used_gb, 1),
                "disk_total_gb": total_gb,
                "cpu_avg_pct": round(telemetry["cpu_avg_pct"], 1),
                "memory_used_pct": round(telemetry["memory_used_pct"], 1),
                "days_since_reboot": telemetry["days_since_reboot"],
                "pending_reboot": telemetry["pending_reboot"],
                "app_crashes_7d": telemetry["app_crashes_7d"],
                **{k: v for k, v in telemetry.items() if k.startswith("storage_")},
            },
            "reclaimable_disk": {
                "total_gb": round(sum(float(r["gb"]) for r in reclaimable), 1),
                "categories": [
                    {"category": r["category"], "gb": r["gb"], "detail": r["detail"]}
                    for r in reclaimable
                ],
            },
        }
    finally:
        conn.close()


@server.tool(
    name="get_health_history",
    title="Get device health history",
    description=(
        "Daily health scores and telemetry for a device over recent days. Use it to tell a "
        "sudden failure apart from a gradual degradation."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_health_history(device_id: str, days: int = 14) -> dict[str, Any]:
    """Health history for a device."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT captured_at, health_score, disk_used_pct, cpu_avg_pct, memory_used_pct,"
            " app_crashes_24h FROM health_history WHERE upper(device_id) = ?"
            " ORDER BY captured_at DESC LIMIT ?",
            (device_id.upper(), min(int(days) + 1, 60)),
        ).fetchall()
        if not rows:
            return {"found": False, "device_id": device_id, "error": "No history for device"}
        points = [dict(r) for r in reversed(rows)]
        first, last = points[0]["health_score"], points[-1]["health_score"]
        delta = round(last - first, 1)
        return {
            "found": True,
            "device_id": device_id.upper(),
            "points": points,
            "trend": {
                "health_score_change": delta,
                "direction": "declining" if delta < -5 else "improving" if delta > 5 else "stable",
                "disk_used_pct_change": round(
                    points[-1]["disk_used_pct"] - points[0]["disk_used_pct"], 1
                ),
            },
        }
    finally:
        conn.close()


@server.tool(
    name="get_installed_software",
    title="Get installed software",
    description=(
        "Software inventory for a device with installed and latest available versions, and "
        "known issues that affect the installed version. Use it to check whether a reported "
        "symptom matches a documented defect in the version actually installed."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_installed_software(
    device_id: str, name_contains: str | None = None, outdated_only: bool = False
) -> dict[str, Any]:
    """Software inventory for a device."""
    conn = _conn()
    try:
        clauses = ["upper(device_id) = ?"]
        params: list[Any] = [device_id.upper()]
        if name_contains:
            clauses.append("lower(name) LIKE ?")
            params.append(f"%{name_contains.lower()}%")
        if outdated_only:
            clauses.append("is_outdated = 1")
        rows = conn.execute(
            f"SELECT record FROM software WHERE {' AND '.join(clauses)} ORDER BY name", params
        ).fetchall()
        items = [json.loads(r["record"]) for r in rows]
        outdated_critical = [
            i["name"] for i in items if i.get("is_outdated") and i.get("is_business_critical")
        ]
        with_issues = [i["name"] for i in items if i.get("known_issues")]
        return {
            "device_id": device_id.upper(),
            "count": len(items),
            "software": items,
            "summary": {
                "outdated_business_critical": outdated_critical,
                "with_known_issues_affecting_installed_version": with_issues,
            },
        }
    finally:
        conn.close()


@server.tool(
    name="get_patch_status",
    title="Get patch status",
    description=(
        "Patch compliance for a device: missing updates with severity, install state and "
        "failure reason, plus recently installed updates. A failure reason often points at "
        "the underlying fault rather than the patch itself."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_patch_status(device_id: str) -> dict[str, Any]:
    """Patch compliance status for a device."""
    conn = _conn()
    try:
        scan = conn.execute(
            "SELECT last_scan_at FROM patch_scans WHERE upper(device_id) = ?", (device_id.upper(),)
        ).fetchone()
        rows = conn.execute(
            "SELECT status, record FROM patches WHERE upper(device_id) = ?", (device_id.upper(),)
        ).fetchall()
        if scan is None and not rows:
            return {"found": False, "device_id": device_id, "error": "No patch data for device"}
        missing = [json.loads(r["record"]) for r in rows if r["status"] == "missing"]
        installed = [json.loads(r["record"]) for r in rows if r["status"] == "installed"]
        failures = {m.get("failure_reason") for m in missing if m.get("failure_reason")}
        return {
            "found": True,
            "device_id": device_id.upper(),
            "last_scan_at": scan["last_scan_at"] if scan else None,
            "missing_count": len(missing),
            "missing_critical_count": sum(1 for m in missing if m.get("severity") == "critical"),
            "missing": missing,
            "recently_installed": installed,
            "common_failure_reasons": sorted(failures),
            "compliance": "compliant" if not missing else "non_compliant",
        }
    finally:
        conn.close()


@server.tool(
    name="clear_disk_space",
    title="Clear disk space",
    description=(
        "Reclaim disk space on a device by removing cached, temporary and orphaned files. "
        "Changes device state. Returns a before and after report including which categories "
        "were cleared and how much was recovered. Permitted categories: temp_files, "
        "windows_update_cache, browser_and_teams_cache, recycle_bin, windows_old, "
        "orphaned_outlook_data_files. Omit categories to clear all permitted categories."
    ),
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def clear_disk_space(device_id: str, categories: list[str] | None = None) -> dict[str, Any]:
    """Reclaim disk space on a device."""
    conn = _conn()
    try:
        return {
            "succeeded": True,
            "result": simulator.clear_disk_space(conn, device_id.upper(), categories),
        }
    except KeyError as exc:
        return {"succeeded": False, "error": str(exc)}
    except ValueError as exc:
        return {"succeeded": False, "error": str(exc)}
    finally:
        conn.close()


@server.tool(
    name="restart_application",
    title="Restart application",
    description=(
        "Restart an application on a device and reset its crash counter. Changes device "
        "state. Clears the symptom; it does not address an underlying cause, so verify "
        "afterwards rather than assuming the issue is resolved."
    ),
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def restart_application(device_id: str, process_name: str) -> dict[str, Any]:
    """Restart an application on a device."""
    conn = _conn()
    try:
        return {
            "succeeded": True,
            "result": simulator.restart_application(conn, device_id.upper(), process_name),
        }
    except KeyError as exc:
        return {"succeeded": False, "error": str(exc)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Run the {SERVER_NAME} MCP server")
    parser.add_argument(
        "--transport",
        default=os.environ.get("MCP_TRANSPORT", "streamable-http"),
        choices=["stdio", "streamable-http"],
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
