"""SQLite access for the synthetic enterprise systems.

The database is a *cache* built from the JSON seed files, never a source of
truth. Delete it and it is rebuilt identically. Only the demo MCP servers open
it; the Aegis gateway never does.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "enterprise_demo.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    department   TEXT,
    job_title    TEXT,
    vip          INTEGER NOT NULL DEFAULT 0,
    record       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    hostname        TEXT NOT NULL,
    serial_number   TEXT,
    primary_user_id TEXT,
    form_factor     TEXT,
    compliance_state TEXT,
    record          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_telemetry (
    device_id            TEXT PRIMARY KEY,
    disk_used_pct        REAL NOT NULL,
    cpu_avg_pct          REAL NOT NULL,
    cpu_baseline_pct     REAL NOT NULL,
    memory_used_pct      REAL NOT NULL,
    memory_baseline_pct  REAL NOT NULL,
    days_since_reboot    INTEGER NOT NULL,
    pending_reboot       INTEGER NOT NULL DEFAULT 0,
    app_crashes_7d       TEXT NOT NULL DEFAULT '{}',
    extra                TEXT NOT NULL DEFAULT '{}',
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disk_reclaimable (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    category  TEXT NOT NULL,
    gb        REAL NOT NULL,
    detail    TEXT,
    reclaimed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_reclaimable_device ON disk_reclaimable(device_id);

CREATE TABLE IF NOT EXISTS health_history (
    device_id       TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    health_score    REAL NOT NULL,
    disk_used_pct   REAL NOT NULL,
    cpu_avg_pct     REAL NOT NULL,
    memory_used_pct REAL NOT NULL,
    app_crashes_24h INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, captured_at)
);

CREATE TABLE IF NOT EXISTS software (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id              TEXT NOT NULL,
    name                   TEXT NOT NULL,
    installed_version      TEXT NOT NULL,
    latest_available_version TEXT,
    is_outdated            INTEGER NOT NULL DEFAULT 0,
    is_business_critical   INTEGER NOT NULL DEFAULT 0,
    record                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_software_device ON software(device_id);

CREATE TABLE IF NOT EXISTS patches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    patch_id    TEXT NOT NULL,
    status      TEXT NOT NULL,          -- 'missing' | 'installed'
    severity    TEXT,
    record      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_patches_device ON patches(device_id);

CREATE TABLE IF NOT EXISTS patch_scans (
    device_id     TEXT PRIMARY KEY,
    last_scan_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    asset_tag        TEXT PRIMARY KEY,
    device_id        TEXT,
    assigned_user_id TEXT,
    category         TEXT,
    subcategory      TEXT,
    lifecycle_state  TEXT,
    record           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assets_user ON assets(assigned_user_id);
CREATE INDEX IF NOT EXISTS ix_assets_device ON assets(device_id);

CREATE TABLE IF NOT EXISTS incidents (
    number     TEXT PRIMARY KEY,
    short_description TEXT NOT NULL,
    caller_id  TEXT,
    device_id  TEXT,
    state      TEXT NOT NULL,
    priority   INTEGER NOT NULL,
    category   TEXT,
    opened_at  TEXT NOT NULL,
    resolved_at TEXT,
    record     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_incidents_caller ON incidents(caller_id);
CREATE INDEX IF NOT EXISTS ix_incidents_device ON incidents(device_id);
CREATE INDEX IF NOT EXISTS ix_incidents_state  ON incidents(state);

CREATE TABLE IF NOT EXISTS work_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_number TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    author_id       TEXT,
    author_label    TEXT,
    text            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_worknotes_incident ON work_notes(incident_number);

CREATE TABLE IF NOT EXISTS action_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    system      TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db_path() -> Path:
    """Database location. ``AEGIS_DEMO_DB`` overrides the default."""
    return Path(os.environ.get("AEGIS_DEMO_DB", DEFAULT_DB_PATH))


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def loads(row: sqlite3.Row, column: str = "record") -> dict[str, Any]:
    return json.loads(row[column])


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
