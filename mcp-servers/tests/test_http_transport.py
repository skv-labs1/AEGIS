"""Prove the servers work over Streamable HTTP, the transport Aegis will use.

The in-process tests cover the protocol; this covers the deployment path. It is
skipped with --no-http for a fast unit-test loop.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"server did not open port {port} within {timeout}s")


@pytest.fixture()
def http_endpoint(demo_db):
    port = _free_port()
    env = dict(os.environ, AEGIS_DEMO_DB=str(demo_db))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "aegis_demo.endpoint.server",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def test_server_is_reachable_over_streamable_http(http_endpoint):
    async with Client(http_endpoint, raise_exceptions=True) as client:
        tools = await client.list_tools()
        assert {"get_device_health", "clear_disk_space"} <= {t.name for t in tools.tools}

        result = await client.call_tool("get_device_health", {"device_id": "DEV-4411"})
        assert not result.is_error
        assert result.structured_content["band"] == "critical"


async def test_remediation_over_http_persists_to_shared_state(http_endpoint):
    """Two separate MCP sessions must see the same device state."""
    async with Client(http_endpoint, raise_exceptions=True) as client:
        await client.call_tool("clear_disk_space", {"device_id": "DEV-4411"})

    async with Client(http_endpoint, raise_exceptions=True) as client:
        result = await client.call_tool("get_device_health", {"device_id": "DEV-4411"})
        assert result.structured_content["telemetry"]["disk_used_pct"] < 75
