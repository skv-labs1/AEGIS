"""Demo IT Asset Management system exposed as an MCP server.

Stands in for the asset/CMDB capability of ServiceNow, Ivanti, Flexera or
similar. All data is synthetic.
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

SERVER_NAME = "aegis-demo-itam"
DEFAULT_PORT = 8802

server = MCPServer(
    name=SERVER_NAME,
    version="0.1.0",
    instructions=(
        "Synthetic IT Asset Management system for the Aegis demo. Provides asset records, "
        "ownership, warranty status, lifecycle position and spare stock. Use it to decide "
        "whether a fault should be repaired, replaced under warranty, or refreshed. "
        "All records are synthetic."
    ),
)


def _conn() -> sqlite3.Connection:
    return dbmod.connect()


def _asset(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["record"])


def _decorate(conn: sqlite3.Connection, asset: dict[str, Any]) -> dict[str, Any]:
    out = dict(asset)
    for key in ("purchase_days_ago", "warranty_expires_in_days", "refresh_due_in_days"):
        out.pop(key, None)
    if asset.get("assigned_user_id"):
        user = records.get_user(conn, asset["assigned_user_id"])
        if user:
            out["assigned_user"] = {
                "user_id": user["user_id"],
                "display_name": user["display_name"],
                "department": user.get("department"),
            }
    out["support_position"] = "in_warranty" if asset.get("warranty_active") else "out_of_warranty"
    if asset.get("refresh_overdue"):
        out["refresh_position"] = "overdue_for_refresh"
    else:
        out["refresh_position"] = "within_refresh_cycle"
    return out


@server.tool(
    name="get_asset",
    title="Get asset",
    description="Retrieve an asset by asset tag, device id or serial number.",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_asset(identifier: str) -> dict[str, Any]:
    """Get one asset by asset tag, device id or serial number."""
    conn = _conn()
    try:
        ident = identifier.upper()
        row = conn.execute(
            "SELECT record FROM assets WHERE asset_tag = ? OR device_id = ?", (ident, ident)
        ).fetchone()
        if row is None:
            rows = conn.execute("SELECT record FROM assets").fetchall()
            match = [
                a
                for a in (_asset(r) for r in rows)
                if (a.get("serial_number") or "").upper() == ident
            ]
            if not match:
                return {"found": False, "identifier": identifier, "error": "Asset not found"}
            return {"found": True, "asset": _decorate(conn, match[0])}
        return {"found": True, "asset": _decorate(conn, _asset(row))}
    finally:
        conn.close()


@server.tool(
    name="get_assets_for_user",
    title="Get assets for user",
    description=(
        "List every asset assigned to a user, including peripherals and mobile devices. "
        "Use this to identify which device a reported problem relates to."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_assets_for_user(user_id: str) -> dict[str, Any]:
    """List all assets assigned to a user."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT record FROM assets WHERE assigned_user_id = ? ORDER BY category, asset_tag",
            (user_id.upper(),),
        ).fetchall()
        assets = [_decorate(conn, _asset(r)) for r in rows]
        return {
            "user_id": user_id.upper(),
            "count": len(assets),
            "assets": assets,
            "primary_compute": next(
                (
                    a["device_id"]
                    for a in assets
                    if a.get("subcategory") == "Laptop" and a.get("device_id")
                ),
                None,
            ),
        }
    finally:
        conn.close()


@server.tool(
    name="get_warranty",
    title="Get warranty and lifecycle position",
    description=(
        "Warranty and refresh position for an asset. Use before recommending a hardware "
        "repair or replacement, since an in-warranty fault should go to the vendor."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_warranty(identifier: str) -> dict[str, Any]:
    """Warranty, provider and refresh position for an asset."""
    result = get_asset(identifier)
    if not result.get("found"):
        return result
    a = result["asset"]
    return {
        "found": True,
        "asset_tag": a["asset_tag"],
        "device_id": a.get("device_id"),
        "model": a.get("model"),
        "warranty_active": a.get("warranty_active"),
        "warranty_expires_at": a.get("warranty_expires_at"),
        "warranty_provider": a.get("warranty_provider"),
        "support_position": a.get("support_position"),
        "refresh_due_at": a.get("refresh_due_at"),
        "refresh_position": a.get("refresh_position"),
        "purchased_at": a.get("purchased_at"),
        "purchase_cost": a.get("purchase_cost"),
        "currency": a.get("currency"),
        "notes": a.get("notes"),
        "guidance": (
            "In warranty: hardware faults should be raised with the warranty provider rather "
            "than repaired locally."
            if a.get("warranty_active")
            else "Out of warranty: hardware faults require a replacement or refresh decision."
        ),
    }


@server.tool(
    name="get_spare_inventory",
    title="Get spare stock",
    description=(
        "List unassigned assets available in stock. Use when a replacement device may be needed."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_spare_inventory(subcategory: str | None = None) -> dict[str, Any]:
    """List assets in stock and unassigned."""
    conn = _conn()
    try:
        if subcategory:
            rows = conn.execute(
                "SELECT record FROM assets WHERE lifecycle_state = 'in_stock' AND subcategory = ?",
                (subcategory,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT record FROM assets WHERE lifecycle_state = 'in_stock'"
            ).fetchall()
        spares = [_decorate(conn, _asset(r)) for r in rows]
        return {"count": len(spares), "spares": spares}
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
