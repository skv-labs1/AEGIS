"""Run all three demo enterprise MCP servers together.

Each server is a separate process, the same as it would be in production where
they are separate vendor systems. Aegis reaches them over Streamable HTTP.

    python -m aegis_demo.run_all

Ctrl-C stops all three.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .common import db as dbmod
from .common import seed

SERVERS = [
    ("itsm", "aegis_demo.itsm.server", 8801),
    ("itam", "aegis_demo.itam.server", 8802),
    ("endpoint", "aegis_demo.endpoint.server", 8803),
]


def ensure_seeded(force: bool = False) -> None:
    path = dbmod.db_path()
    if force or not path.exists():
        counts = seed.reseed(path)
        total = sum(counts.values())
        print(f"Seeded {path} ({total} rows)")
    else:
        print(f"Using existing database {path} (--reseed to rebuild)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the demo enterprise MCP servers")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--reseed", action="store_true", help="rebuild the database first")
    parser.add_argument(
        "--only", nargs="*", choices=[s[0] for s in SERVERS], help="run only the named servers"
    )
    args = parser.parse_args()

    ensure_seeded(force=args.reseed)

    selected = [s for s in SERVERS if not args.only or s[0] in args.only]
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []
    env = dict(os.environ)
    env.setdefault("AEGIS_DEMO_DB", str(dbmod.db_path()))

    try:
        for name, module, port in selected:
            proc = subprocess.Popen(
                [sys.executable, "-m", module, "--host", args.host, "--port", str(port)],
                env=env,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            procs.append((name, proc))
            print(f"  {name:<9} http://{args.host}:{port}/mcp   (pid {proc.pid})")

        print("\nAll demo systems running. Ctrl-C to stop.")
        while True:
            for name, proc in procs:
                if proc.poll() is not None:
                    print(f"\n{name} exited with code {proc.returncode}", file=sys.stderr)
                    return proc.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        for _, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
