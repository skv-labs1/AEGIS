"""A provider that replays a recorded run instead of calling a model.

It satisfies the same protocol as Gemini or Groq, so the engine cannot tell the
difference and nothing downstream changes. Placeholders in the trace are filled
from what the live systems return during playback, so a replay works against a
freshly seeded database where the ids are not the ones that were recorded.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..engine.providers.base import (
    Completion,
    Message,
    ProviderError,
    Role,
    ToolCall,
    ToolSpec,
    Usage,
)
from .trace import Trace

# A trace records "investigation_id": "$investigation_id". On playback that is
# replaced with the id the live run actually produced.
PLACEHOLDER_PREFIX = "$"


class ReplayProvider:
    """Plays a trace back turn by turn, at something like real pacing."""

    name = "replay"
    ready = True

    def __init__(self, trace: Trace, *, pacing: float = 1.0) -> None:
        self.trace = trace
        self.model = f"{trace.provider}/{trace.model}" if trace.is_recorded else "authored-trace"
        self._pacing = pacing
        self._turn = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        if self._turn >= len(self.trace.turns):
            raise ProviderError(
                "The trace ran out of turns. The live run diverged from the recording, "
                "usually because a tool returned something different.",
                provider=self.name,
            )
        turn = self.trace.turns[self._turn]
        self._turn += 1

        if self._pacing > 0:
            await asyncio.sleep(min(turn.latency_ms / 1000.0, 3.0) * self._pacing)

        resolved = _resolve(messages, turn.tool_calls)
        return Completion(
            text=turn.text,
            tool_calls=[
                ToolCall(
                    id=f"replay-{self._turn}-{index}",
                    name=call["name"],
                    arguments=call["arguments"],
                )
                for index, call in enumerate(resolved)
            ],
            provider=self.name,
            model=self.model,
            usage=Usage(0, 0),
            latency_ms=turn.latency_ms,
            finish_reason="replay",
        )


def _resolve(messages: list[Message], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill placeholders from what the live systems returned this run."""
    bindings = _bindings(messages)
    out: list[dict[str, Any]] = []
    for call in calls:
        arguments = {
            key: _substitute(value, bindings)
            for key, value in (call.get("arguments") or {}).items()
        }
        out.append({"name": call["name"], "arguments": arguments})
    return out


def _substitute(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX):
        key = value[len(PLACEHOLDER_PREFIX) :]
        if key in bindings:
            return bindings[key]
    if isinstance(value, dict):
        return {k: _substitute(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, bindings) for v in value]
    return value


def _bindings(messages: list[Message]) -> dict[str, Any]:
    """Ids the live run produced, read back out of the tool results so far."""
    found: dict[str, Any] = {}
    for message in messages:
        if message.role is not Role.TOOL or not message.content:
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("investigation_id", "proposal_id"):
            if payload.get(key) is not None:
                found[key] = payload[key]
        device = payload.get("primary_compute") or payload.get("device_id")
        if device:
            found["device_id"] = device
    return found
