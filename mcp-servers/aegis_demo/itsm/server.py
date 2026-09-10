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
