"""Run one investigation with the built-in engine.

    python -m aegis.engine.run INC-1042

Connects to the gateway over MCP exactly as any other agent host would, so the
engine gets no privileges Claude Code would not have.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from ..config import get_settings
from ..db.session import init_engine
from .loop import AgentEngine
from .providers.registry import load_chain


async def run(incident: str, gateway_url: str, provider: str | None, max_turns: int) -> int:
    settings = get_settings()
    init_engine(settings)
    chain = load_chain(Path(settings.policy_file).parent.parent / "llm_providers.yaml")
    if provider:
        chain = chain.select(provider)

    print(f"Investigating {incident} via {gateway_url}")
    print(f"Provider chain: {' -> '.join(chain.names)}\n")

    engine = AgentEngine(gateway_url, chain, max_turns=max_turns)
    result = await engine.run(incident)

    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.finished == "completed" else 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run an investigation with the built-in engine")
    parser.add_argument("incident", help="incident number, e.g. INC-1042")
    parser.add_argument("--gateway", default="http://127.0.0.1:8800/mcp")
    parser.add_argument("--provider", default=None, help="force a provider from the chain")
    parser.add_argument("--max-turns", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(run(args.incident, args.gateway, args.provider, args.max_turns))


if __name__ == "__main__":
    raise SystemExit(main())
