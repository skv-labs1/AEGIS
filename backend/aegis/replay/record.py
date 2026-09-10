"""Record a live investigation so it can be replayed without a model.

    python -m aegis.replay.record INC-1042

Runs the incident with whatever provider is configured, captures the model's
turns, and writes a trace next to the others. The trace holds only the agent's
decisions: playing it back still exercises the real gateway, policy, approval
gate and verification.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from ..config import get_settings
from ..db.session import init_engine
from ..engine.loop import AgentEngine
from ..engine.providers.registry import load_chain
from .trace import TRACE_DIR, TraceRecorder


async def record(incident: str, gateway_url: str, provider: str | None, out: Path | None) -> int:
    settings = get_settings()
    init_engine(settings)
    chain = load_chain(settings.policy_file.parent.parent / "llm_providers.yaml")
    if provider:
        chain = chain.select(provider)
    if not chain.usable:
        print("No provider has its API key set, so there is nothing to record.")
        return 1

    recorder = TraceRecorder(incident.upper())
    engine = AgentEngine(gateway_url, chain, recorder=recorder)
    result = await engine.run(incident.upper())

    if result.finished != "completed":
        print(f"Run finished as {result.finished}; not saving a trace of an incomplete run.")
        if result.error:
            print(f"  {result.error}")
        return 1

    trace = recorder.finish(
        notes=(
            f"Recorded from a live run on {recorder.provider}/{recorder.model}. "
            f"{result.turns} turns, {result.tool_calls} tool calls."
        )
    )
    path = out or TRACE_DIR / f"{incident.upper()}-recorded.json"
    trace.save(path)
    print(f"Saved {len(trace.turns)} turns to {path}")
    print("Replace the authored trace with this one to demo a real recorded run.")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Record an investigation for replay")
    parser.add_argument("incident")
    parser.add_argument("--gateway", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    return asyncio.run(record(args.incident, args.gateway, args.provider, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
