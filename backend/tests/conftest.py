"""Fixtures for gateway tests.

The gateway is tested against the real demo enterprise systems, running as
separate processes over HTTP, because that is how it will run. Aegis's own
database is a throwaway per test, and the enterprise database is reseeded
before each test so remediation in one test cannot leak into another.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aegis.config import Settings
from aegis.db import session as db_session
from aegis.gateway.server import AegisGateway
from aegis.governance.approvals import AutoApproveForTesting

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "mcp-servers"
BACKEND_ROOT = Path(__file__).resolve().parents[1]

SERVERS = [
    ("itsm", "aegis_demo.itsm.server"),
    ("itam", "aegis_demo.itam.server"),
    ("endpoint", "aegis_demo.endpoint.server"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"demo server exited early (code {proc.returncode})")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"demo server never opened port {port}")


@pytest.fixture(scope="session")
def enterprise_db(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("enterprise") / "enterprise_demo.db"


@pytest.fixture(scope="session")
def demo_systems(enterprise_db: Path):
    """Run the three demo enterprise MCP servers for the whole test session."""
    from aegis_demo.common import db as demo_db
    from aegis_demo.common import seed

    conn = demo_db.connect(enterprise_db)
    try:
        seed.build(conn)
    finally:
        conn.close()

    env = dict(os.environ, AEGIS_DEMO_DB=str(enterprise_db))
    running: list[tuple[str, int, subprocess.Popen]] = []
    try:
        for name, module in SERVERS:
            port = _free_port()
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    module,
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=str(DEMO_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait_for_port(port, proc)
            running.append((name, port, proc))
        yield {name: f"http://127.0.0.1:{port}/mcp" for name, port, _ in running}
    finally:
        for _, _, proc in running:
            proc.terminate()
        for _, _, proc in running:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture()
def fresh_enterprise_state(enterprise_db: Path, demo_systems):
    """Reset the synthetic enterprise before each test."""
    from aegis_demo.common import db as demo_db
    from aegis_demo.common import seed

    conn = demo_db.connect(enterprise_db)
    try:
        seed.build(conn)
    finally:
        conn.close()
    return enterprise_db


@pytest.fixture()
def upstreams_file(tmp_path: Path, demo_systems) -> Path:
    path = tmp_path / "mcp_upstreams.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "upstreams": [
                    {
                        "name": name,
                        "prefix": name,
                        "url": url,
                        "enabled": True,
                        "capability": name.upper(),
                    }
                    for name, url in demo_systems.items()
                ],
            }
        )
    )
    return path


@pytest.fixture()
def settings(tmp_path: Path, upstreams_file: Path) -> Settings:
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'aegis.db'}",
        policy_file=BACKEND_ROOT / "policy" / "risk_policy.yaml",
        upstreams_file=upstreams_file,
    )
    db_session.reset_for_tests(cfg)
    return cfg


@pytest.fixture()
def approver() -> AutoApproveForTesting:
    return AutoApproveForTesting()


@pytest.fixture()
async def gateway(settings: Settings, fresh_enterprise_state, approver):
    """A gateway with an approver attached, connected to the demo systems."""
    gw = AegisGateway(settings, approvals=approver)
    await gw.start()
    try:
        yield gw
    finally:
        await gw.stop()


@pytest.fixture()
async def ungoverned_gateway(settings: Settings, fresh_enterprise_state):
    """A gateway with no approval channel connected. Writes must refuse."""
    gw = AegisGateway(settings)
    await gw.start()
    try:
        yield gw
    finally:
        await gw.stop()
