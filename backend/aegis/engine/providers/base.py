"""The provider abstraction.

The engine never imports a vendor SDK. It talks to this protocol, so switching
from Gemini to Groq to a paid Anthropic key is a change in configuration rather
than a change in code. Two rules keep providers genuinely interchangeable:

1. Tool calling is the only structured channel the loop depends on. Native
   structured-output modes differ too much between vendors, and every provider
   worth using supports tools.
2. Prompt text is shared. A provider's quirks belong in its adapter, never in
   the prompt, or the prompt slowly becomes vendor-specific.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol


class Role(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A tool the model wants to run."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """One turn, in a shape every adapter can translate."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set when role is TOOL
    tool_name: str | None = None  # set when role is TOOL


@dataclass
class ToolSpec:
    """A tool as discovered from the gateway."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Completion:
    """What a provider returned."""

    text: str | None
    tool_calls: list[ToolCall]
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    finish_reason: str | None = None
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ProviderError(RuntimeError):
    """A provider failed in a way the chain may be able to route around."""

    def __init__(self, message: str, *, retryable: bool = False, provider: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.provider = provider


class RateLimited(ProviderError):
    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(message, retryable=True, provider=provider)


class LLMProvider(Protocol):
    """Everything the engine needs from a model."""

    name: str
    model: str
    ready: bool

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion: ...
