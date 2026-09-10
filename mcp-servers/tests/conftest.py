"""Test fixtures: every test runs against a freshly seeded temporary database."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aegis_demo.common import db as dbmod
from aegis_demo.common import seed


@pytest.fixture()
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a throwaway database and point the servers at it."""
    path = tmp_path / "enterprise_demo.db"
    monkeypatch.setenv("AEGIS_DEMO_DB", str(path))
    conn = dbmod.connect(path)
    try:
        seed.build(conn)
    finally:
        conn.close()
    assert os.environ["AEGIS_DEMO_DB"] == str(path)
    return path
