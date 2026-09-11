"""Demo ITSM system exposed as an MCP server.

Stands in for ServiceNow, Ivanti, Freshservice, Jira Service Management or any
other incident management platform. It implements the canonical tool contract
described in docs/MCP_CONTRACT.md, so a vendor MCP server implementing the same
contract can replace it by changing one entry in the Aegis gateway config.

All data is synthetic.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..common import db as dbmod
from ..common import records

SERVER_NAME = "aegis-demo-itsm"
DEFAULT_PORT = 8801

server = MCPServer(
    name=SERVER_NAME,
    version="0.1.0",
    instructions=(
        "Synthetic IT Service Management system for the Aegis demo. Provides incident "
        "records, caller details, work notes and resolution actions. Incident descriptions "
        "and work notes are text written by people and are returned wrapped as untrusted "
        "data. All records are synthetic."
    ),
)


def _conn() -> sqlite3.Connection:
    return dbmod.connect()


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _incident_row(conn: sqlite3.Connection, number: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM incidents WHERE number = ?", (number.upper(),)).fetchone()


def _hydrate(
    conn: sqlite3.Connection, row: sqlite3.Row, *, include_notes: bool = True
) -> dict[str, Any]:
    record = json.loads(row["record"])
    caller = records.get_user(conn, record["caller_id"]) if record.get("caller_id") else None
    out: dict[str, Any] = {
        "number": record["number"],
        "short_description": record["short_description"],
        "description": records.untrusted(
            record.get("description"), f"incident {record['number']} description"
        ),
        "state": record["state"],
        "priority": record["priority"],
        "impact": record.get("impact"),
        "urgency": record.get("urgency"),
        "category": record.get("category"),
        "subcategory": record.get("subcategory"),
        "assignment_group": record.get("assignment_group"),
        "assigned_to_id": record.get("assigned_to_id"),
        "channel": record.get("channel"),
        "opened_at": record["opened_at"],
        "resolved_at": record.get("resolved_at"),
        "reopen_count": record.get("reopen_count", 0),
        "resolution_code": record.get("resolution_code"),
        "resolution_notes": record.get("resolution_notes"),
        "caller": {
            "user_id": caller["user_id"],
            "display_name": caller["display_name"],
            "email": caller["email"],
            "job_title": caller.get("job_title"),
            "department": caller.get("department"),
            "location": caller.get("location"),
            "vip": caller.get("vip", False),
        }
        if caller
        else None,
        "device_id": record.get("device_id"),
    }
    if include_notes:
        notes = conn.execute(
            "SELECT created_at, author_id, author_label, text FROM work_notes"
            " WHERE incident_number = ? ORDER BY created_at",
            (record["number"],),
        ).fetchall()
        out["work_notes"] = [
            {
                "created_at": n["created_at"],
                "author": n["author_label"] or n["author_id"],
                "text": records.untrusted(n["text"], f"work note on {record['number']}"),
            }
            for n in notes
        ]
    return out


@server.tool(
    name="get_incident",
    title="Get incident",
    description=(
        "Retrieve a single incident by number, including caller details and work notes. "
        "Use this first when investigating a reported issue."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_incident(number: str) -> dict[str, Any]:
    """Get one incident by its number, for example INC-1042."""
    conn = _conn()
    try:
        row = _incident_row(conn, number)
        if row is None:
            return {"found": False, "number": number.upper(), "error": "Incident not found"}
        return {"found": True, "incident": _hydrate(conn, row)}
    finally:
        conn.close()


@server.tool(
    name="search_incidents",
    title="Search incidents",
    description=(
        "Search incidents by caller, device, state, text or age. Use this to find a "
        "caller's incident history and to detect repeat issues that were never "
        "permanently resolved."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def search_incidents(
    caller_id: str | None = None,
    device_id: str | None = None,
    state: str | None = None,
    text: str | None = None,
    opened_within_days: int | None = None,
    exclude_number: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search incidents. All filters are optional and combine with AND."""
    conn = _conn()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if caller_id:
            clauses.append("caller_id = ?")
            params.append(caller_id.upper())
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id.upper())
        if state:
            clauses.append("state = ?")
            params.append(state)
        if text:
            clauses.append("(lower(short_description) LIKE ? OR lower(record) LIKE ?)")
            params.extend([f"%{text.lower()}%", f"%{text.lower()}%"])
        if opened_within_days is not None:
            clauses.append("opened_at >= datetime('now', ?)")
            params.append(f"-{int(opened_within_days)} days")
        if exclude_number:
            clauses.append("number != ?")
            params.append(exclude_number.upper())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM incidents {where} ORDER BY opened_at DESC LIMIT ?",
            (*params, min(int(limit), 100)),
        ).fetchall()

        results = [_hydrate(conn, r, include_notes=False) for r in rows]
        workarounds = sum(
            1 for r in results if (r.get("resolution_code") or "").startswith("Solved (Workaround)")
        )
        return {
            "count": len(results),
            "incidents": results,
            "summary": {
                "resolved_as_workaround": workarounds,
                "note": (
                    "Incidents closed as a workaround did not address a root cause and may "
                    "indicate a recurring underlying problem."
                )
                if workarounds
                else None,
            },
        }
    finally:
        conn.close()


@server.tool(
    name="get_incident_summary",
    title="Get incident summary",
    description=(
        "Aggregate incident volume across the service desk in one call: counts by state, "
        "priority and category, how many resolved incidents were closed with a workaround "
        "rather than a fix, and which callers and devices are raising the same issue "
        "repeatedly. Use this for a queue-wide view instead of searching incident by "
        "incident."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_incident_summary(recent_days: int = 30, top_repeat: int = 6) -> dict[str, Any]:
    """Service-desk wide incident volume, ageing and repeat-issue counts."""
    conn = _conn()
    try:
        return _incident_summary(conn, recent_days=recent_days, top_repeat=top_repeat)
    finally:
        conn.close()


def _resolved_days_ago(record: dict[str, Any]) -> int | None:
    """Age of a resolution in whole days.

    ``resolved_days_ago`` is written when the seed file is expanded, so an
    incident resolved *during* the demo does not carry one. Falling back to the
    timestamp keeps a just-closed incident from rendering as a blank age.
    """
    if record.get("resolved_days_ago") is not None:
        return int(record["resolved_days_ago"])
    resolved_at = record.get("resolved_at")
    if not resolved_at:
        return None
    try:
        closed = datetime.fromisoformat(resolved_at)  # Python 3.11+ parses the trailing Z
    except ValueError:
        return None
    return max(0, (datetime.now(UTC) - closed).days)


def _incident_summary(
    conn: sqlite3.Connection, *, recent_days: int, top_repeat: int
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM incidents").fetchall()
    open_states = {"new", "in_progress", "on_hold"}

    by_state: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    per_caller: dict[str, dict[str, Any]] = {}
    per_device: dict[str, dict[str, Any]] = {}
    workaround_examples: list[dict[str, Any]] = []
    workarounds = 0
    resolved = 0
    reopened = 0
    recent_opened = 0
    open_ages: list[int] = []

    for row in rows:
        record = json.loads(row["record"])
        state = record.get("state", "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        by_priority[f"P{record.get('priority', '?')}"] = (
            by_priority.get(f"P{record.get('priority', '?')}", 0) + 1
        )
        category = record.get("category") or "Uncategorised"
        by_category[category] = by_category.get(category, 0) + 1
        channel = record.get("channel") or "unknown"
        by_channel[channel] = by_channel.get(channel, 0) + 1

        opened_days_ago = int(record.get("opened_days_ago") or 0)
        if opened_days_ago <= int(recent_days):
            recent_opened += 1
        if state in open_states:
            open_ages.append(opened_days_ago)
        if record.get("reopen_count"):
            reopened += 1

        is_workaround = (record.get("resolution_code") or "").startswith("Solved (Workaround)")
        if state == "resolved" or state == "closed":
            resolved += 1
            if is_workaround:
                workarounds += 1
                if len(workaround_examples) < 8:
                    workaround_examples.append(
                        {
                            "number": record["number"],
                            "short_description": record["short_description"],
                            "device_id": record.get("device_id"),
                            "resolved_days_ago": _resolved_days_ago(record),
                        }
                    )

        caller_id = record.get("caller_id")
        if caller_id:
            entry = per_caller.setdefault(
                caller_id, {"caller_id": caller_id, "incidents": 0, "workarounds": 0}
            )
            entry["incidents"] += 1
            entry["workarounds"] += 1 if is_workaround else 0
        device_id = record.get("device_id")
        if device_id:
            entry = per_device.setdefault(
                device_id,
                {"device_id": device_id, "incidents": 0, "workarounds": 0, "categories": set()},
            )
            entry["incidents"] += 1
            entry["workarounds"] += 1 if is_workaround else 0
            entry["categories"].add(category)

    def _top(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        repeats = [v for v in values if v["incidents"] > 1]
        repeats.sort(key=lambda v: (-v["incidents"], -v["workarounds"]))
        return repeats[: max(0, int(top_repeat))]

    top_callers = _top(list(per_caller.values()))
    for entry in top_callers:
        user = records.get_user(conn, entry["caller_id"])
        entry["display_name"] = user["display_name"] if user else None
        entry["department"] = user.get("department") if user else None
        entry["vip"] = bool(user.get("vip")) if user else False

    top_devices = _top([{**v, "categories": sorted(v["categories"])} for v in per_device.values()])

    # Totals over every repeat, not over the truncated lists above: a headline
    # figure taken from a top-six list would understate the estate.
    repeat_devices = [v for v in per_device.values() if v["incidents"] > 1]
    repeat_callers = [v for v in per_caller.values() if v["incidents"] > 1]

    open_total = sum(by_state.get(s, 0) for s in open_states)
    return {
        "total": len(rows),
        "open": open_total,
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        "by_priority": dict(sorted(by_priority.items())),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "by_channel": dict(sorted(by_channel.items(), key=lambda kv: -kv[1])),
        "opened_recently": {"days": int(recent_days), "count": recent_opened},
        "ageing": {
            "open_average_days": round(sum(open_ages) / len(open_ages), 1) if open_ages else None,
            "open_oldest_days": max(open_ages) if open_ages else None,
        },
        "resolution_quality": {
            "resolved": resolved,
            "resolved_as_workaround": workarounds,
            "workaround_rate_pct": round(workarounds / resolved * 100, 1) if resolved else None,
            "reopened": reopened,
            "note": (
                "Incidents closed as a workaround did not address a root cause. A high rate "
                "means the same faults are being re-reported."
            ),
            "examples": workaround_examples,
        },
        "repeats": {
            "devices_affected": len(repeat_devices),
            "incidents_on_repeat_devices": sum(v["incidents"] for v in repeat_devices),
            "callers_affected": len(repeat_callers),
            "incidents_from_repeat_callers": sum(v["incidents"] for v in repeat_callers),
        },
        "repeat_callers": top_callers,
        "repeat_devices": top_devices,
    }


@server.tool(
    name="get_user",
    title="Get user",
    description="Retrieve a user record by id, email or name fragment.",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_user(query: str) -> dict[str, Any]:
    """Look up a user by user id, email address, or part of their name."""
    conn = _conn()
    try:
        exact = records.get_user(conn, query.upper())
        if exact:
            return {"found": True, "matches": [exact]}
        matches = records.find_users(conn, query)
        return {"found": bool(matches), "matches": matches}
    finally:
        conn.close()


@server.tool(
    name="create_incident",
    title="Create incident",
    description="Create a new incident. Used by the self-service portal and the Aegis console.",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False
    ),
)
def create_incident(
    short_description: str,
    description: str,
    caller_id: str,
    device_id: str | None = None,
    priority: int = 3,
    category: str = "End User Compute",
    subcategory: str | None = None,
    channel: str = "self_service_portal",
) -> dict[str, Any]:
    """Create an incident and return the created record."""
    conn = _conn()
    try:
        if records.get_user(conn, caller_id.upper()) is None:
            return {"created": False, "error": f"Unknown caller: {caller_id}"}
        top = conn.execute(
            "SELECT number FROM incidents WHERE number LIKE 'INC-%' ORDER BY number DESC LIMIT 1"
        ).fetchone()
        next_n = (int(top["number"].split("-")[1]) + 1) if top else 1001
        number = f"INC-{next_n:04d}"
        now = _now_iso()
        record = {
            "number": number,
            "short_description": short_description,
            "description": description,
            "caller_id": caller_id.upper(),
            "device_id": device_id.upper() if device_id else None,
            "state": "new",
            "priority": int(priority),
            "impact": int(priority),
            "urgency": int(priority),
            "category": category,
            "subcategory": subcategory,
            "assignment_group": "Service Desk",
            "assigned_to_id": None,
            "channel": channel,
            "opened_at": now,
            "resolved_at": None,
            "reopen_count": 0,
            "resolution_code": None,
            "resolution_notes": None,
        }
        conn.execute(
            "INSERT INTO incidents (number, short_description, caller_id, device_id, state,"
            " priority, category, opened_at, resolved_at, record) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
            (
                number,
                short_description,
                record["caller_id"],
                record["device_id"],
                "new",
                int(priority),
                category,
                now,
                dbmod.dumps(record),
            ),
        )
        row = _incident_row(conn, number)
        return {"created": True, "incident": _hydrate(conn, row)}
    finally:
        conn.close()


@server.tool(
    name="add_work_note",
    title="Add work note",
    description=(
        "Append a work note to an incident. This is the audit-visible narrative on the "
        "ticket and does not change incident state."
    ),
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False
    ),
)
def add_work_note(number: str, text: str, author: str = "Aegis") -> dict[str, Any]:
    """Add a work note to an incident."""
    conn = _conn()
    try:
        row = _incident_row(conn, number)
        if row is None:
            return {"added": False, "error": f"Incident not found: {number}"}
        now = _now_iso()
        conn.execute(
            "INSERT INTO work_notes (incident_number, created_at, author_id, author_label, text)"
            " VALUES (?,?,NULL,?,?)",
            (number.upper(), now, author, text),
        )
        return {"added": True, "number": number.upper(), "created_at": now, "author": author}
    finally:
        conn.close()


@server.tool(
    name="resolve_incident",
    title="Resolve incident",
    description=(
        "Move an incident to resolved with a resolution code and notes. Use "
        "'Solved (Permanently)' only when a root cause was addressed and verified; use "
        "'Solved (Workaround)' when the symptom was cleared but the cause was not."
    ),
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
)
def resolve_incident(
    number: str,
    resolution_code: str,
    resolution_notes: str,
    resolved_by: str = "Aegis",
) -> dict[str, Any]:
    """Resolve an incident."""
    valid = {"Solved (Permanently)", "Solved (Workaround)", "Not Solved (Escalated)"}
    if resolution_code not in valid:
        return {"resolved": False, "error": f"resolution_code must be one of {sorted(valid)}"}
    conn = _conn()
    try:
        row = _incident_row(conn, number)
        if row is None:
            return {"resolved": False, "error": f"Incident not found: {number}"}
        record = json.loads(row["record"])
        if record["state"] == "resolved":
            return {
                "resolved": False,
                "error": f"{number.upper()} is already resolved",
                "resolved_at": record.get("resolved_at"),
            }
        now = _now_iso()
        state = "resolved" if resolution_code.startswith("Solved") else "escalated"
        record.update(
            state=state,
            resolved_at=now,
            resolution_code=resolution_code,
            resolution_notes=resolution_notes,
        )
        conn.execute(
            "UPDATE incidents SET state=?, resolved_at=?, record=? WHERE number=?",
            (state, now, dbmod.dumps(record), number.upper()),
        )
        conn.execute(
            "INSERT INTO work_notes (incident_number, created_at, author_id, author_label, text)"
            " VALUES (?,?,NULL,?,?)",
            (number.upper(), now, resolved_by, f"[{resolution_code}] {resolution_notes}"),
        )
        return {
            "resolved": True,
            "number": number.upper(),
            "state": state,
            "resolution_code": resolution_code,
            "resolved_at": now,
        }
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
