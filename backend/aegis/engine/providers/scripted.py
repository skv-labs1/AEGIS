"""A deterministic provider for tests and replay.

The agent loop is the part of the engine that must be provably correct: that it
executes tools, feeds results back, stops when it should, and handles a provider
failure. Proving that against a live model would be slow, costly and flaky. This
provider replaces the model with a script, so the loop is tested exactly and the
providers are tested separately.

It is also what replay mode uses, so a recorded run can be played back with no
model call at all.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Completion, Message, ProviderError, ToolCall, ToolSpec, Usage

Step = Completion | Callable[[list[Message], list[ToolSpec]], Completion] | Exception


class ScriptedProvider:
    """Returns pre-arranged completions, one per call."""

    name = "scripted"

    ready = True

    def __init__(self, steps: list[Step], *, model: str = "scripted-1") -> None:
        self.model = model
        self._steps = list(steps)
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []

    @staticmethod
    def say(text: str) -> Completion:
        return Completion(
            text=text,
            tool_calls=[],
            provider="scripted",
            model="scripted-1",
            usage=Usage(10, 10),
        )

    @staticmethod
    def call(name: str, arguments: dict[str, Any] | None = None, call_id: str = "") -> Completion:
        return Completion(
            text=None,
            tool_calls=[
                ToolCall(id=call_id or f"scripted-{name}", name=name, arguments=arguments or {})
            ],
            provider="scripted",
            model="scripted-1",
            usage=Usage(10, 10),
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        self.calls.append((list(messages), list(tools)))
        if not self._steps:
            raise ProviderError(
                "The script ran out of steps. The loop asked for more turns than the "
                "test provided, which usually means it is not stopping when it should.",
                provider=self.name,
            )
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages, tools)
        return step


class AlwaysFailingProvider:
    """Fails every call. Used to prove the fallback chain actually falls back."""

    ready = True

    def __init__(self, name: str = "broken", *, error: Exception | None = None) -> None:
        self.name = name
        self.model = "none"
        self._error = error or ProviderError("Provider unavailable.", retryable=True, provider=name)
        self.attempts = 0

    async def complete(self, messages, tools, **kwargs):
        self.attempts += 1
        raise self._error
