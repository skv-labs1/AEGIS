"""Verify a provider against its live API.

The Gemini and Groq adapters are written from documented request and response
shapes but cannot be exercised from an environment with no key. This command is
the one run that confirms an adapter works end to end: it sends a tiny request
with one tool and reports exactly what came back, so a shape mismatch is
obvious rather than mysterious.

    python -m aegis.engine.check --provider gemini
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from ..config import get_settings
from .providers.base import Message, ProviderError, Role, ToolSpec
from .providers.registry import load_chain

PROBE_TOOL = ToolSpec(
    name="get_device_health",
    description="Return the health of a device. Use it when asked about a device.",
    input_schema={
        "type": "object",
        "properties": {
            "device_id": {"type": "string", "description": "Device identifier"},
            "categories": {
                "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]
            },
        },
        "required": ["device_id"],
        "additionalProperties": False,
    },
)


async def check(provider_name: str | None) -> int:
    settings = get_settings()
    config = Path(settings.policy_file).parent.parent / "llm_providers.yaml"
    chain = load_chain(config)
    providers = (
        [p for p in chain.providers if p.name == provider_name]
        if provider_name
        else chain.providers
    )
    if not providers:
        print(f"No provider named {provider_name!r} is configured.", file=sys.stderr)
        return 1

    failures = 0
    for provider in providers:
        print(f"\n=== {provider.name} ({provider.model}) ===")
        try:
            completion = await provider.complete(
                [
                    Message(role=Role.SYSTEM, content="You are a terse IT operations assistant."),
                    Message(role=Role.USER, content="What is the health of device DEV-4411?"),
                ],
                [PROBE_TOOL],
                max_tokens=512,
            )
        except ProviderError as exc:
            print(f"  FAILED: {exc}")
            failures += 1
            continue
        except Exception as exc:  # noqa: BLE001 - this command exists to surface anything
            print(f"  FAILED with an unexpected error: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        print(f"  reachable        yes ({completion.latency_ms:.0f} ms)")
        print(
            f"  tokens           in={completion.usage.input_tokens} "
            f"out={completion.usage.output_tokens}"
        )
        print(f"  finish reason    {completion.finish_reason}")
        if completion.tool_calls:
            call = completion.tool_calls[0]
            print(f"  tool calling     yes -> {call.name}({call.arguments})")
            if call.name != PROBE_TOOL.name:
                print("  WARNING: the model called a tool that was not offered.")
        else:
            print("  tool calling     NO - the model replied with text instead:")
            print(f"                   {(completion.text or '')[:200]}")
            print(
                "  This provider or model may not support tool calling well enough "
                "to drive an investigation."
            )
            failures += 1

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check an LLM provider against its live API")
    parser.add_argument("--provider", default=None, help="gemini, groq, ... (default: all)")
    args = parser.parse_args()

    for variable in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        state = "set" if os.environ.get(variable) else "not set"
        print(f"{variable}: {state}")

    return asyncio.run(check(args.provider))


if __name__ == "__main__":
    raise SystemExit(main())
