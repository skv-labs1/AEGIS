"""Adapter for any OpenAI-compatible chat completions endpoint.

One adapter covers Groq, OpenAI, OpenRouter, Together and a local Ollama,
because they all speak the same wire format. Which one is in use is decided by
``base_url`` and ``model`` in configuration.

VERIFICATION STATUS: written against the documented OpenAI chat-completions
shape, which Groq and Ollama both implement, but not yet exercised against a
live endpoint from this environment (no key, and the vendor docs are
unreachable here). Parsing is tolerant of the known variations. Run
`python -m aegis.engine.check --provider groq` once with a real key.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .base import (
    Completion,
    Message,
    ProviderError,
    RateLimited,
    Role,
    ToolCall,
    ToolSpec,
    Usage,
)
from .schema import sanitise_for_openai


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str = "openai_compatible",
        model: str,
        base_url: str,
        api_key_env: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env
        self._timeout = timeout

    @property
    def ready(self) -> bool:
        """Usable right now. A local endpoint needs no key; a hosted one does."""
        return not self._api_key_env or bool(os.environ.get(self._api_key_env))

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key_env:
            key = os.environ.get(self._api_key_env)
            if not key:
                raise ProviderError(
                    f"{self._api_key_env} is not set, so the {self.name} provider cannot be used.",
                    provider=self.name,
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    # -- translation ----------------------------------------------------------

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            if message.role is Role.TOOL:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "",
                        "content": message.content or "",
                    }
                )
                continue
            if message.role is Role.ASSISTANT and message.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
                continue
            out.append({"role": message.role.value, "content": message.content or ""})
        return out

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description[:1024],
                    "parameters": sanitise_for_openai(tool.input_schema),
                },
            }
            for tool in tools
        ]

    # -- call -----------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = self._tools(tools)
            body["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise RateLimited(f"{self.name} request failed: {exc}", provider=self.name) from exc

        latency = (time.perf_counter() - started) * 1000
        if response.status_code == 429:
            raise RateLimited(f"{self.name} rate limit reached.", provider=self.name)
        if response.status_code >= 500:
            raise RateLimited(f"{self.name} returned {response.status_code}.", provider=self.name)
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.name} rejected the request ({response.status_code}): {response.text[:400]}",
                provider=self.name,
            )
        return self._parse(response.json(), latency)

    def _parse(self, payload: dict[str, Any], latency_ms: float) -> Completion:
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.name} returned no choices.", provider=self.name)
        message = choices[0].get("message") or {}

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    raise ProviderError(
                        f"{self.name} returned tool arguments that are not valid JSON: "
                        f"{arguments[:200]}",
                        provider=self.name,
                    ) from None
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"{self.name}-{len(calls)}",
                    name=function.get("name", ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        usage_raw = payload.get("usage") or {}
        return Completion(
            text=message.get("content") or None,
            tool_calls=calls,
            provider=self.name,
            model=payload.get("model", self.model),
            usage=Usage(
                input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            ),
            latency_ms=round(latency_ms, 1),
            finish_reason=choices[0].get("finish_reason"),
            raw=payload,
        )
