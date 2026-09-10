"""Google Gemini adapter.

Uses the REST generateContent endpoint directly rather than a vendor SDK, so the
gateway's dependency surface stays small.

VERIFICATION STATUS: written against the documented request and response shapes
but not yet exercised against the live API, because this environment has no key
and the vendor docs are unreachable from it. Parsing is deliberately tolerant so
a minor shape difference surfaces as a clear error rather than a crash. Run
`python -m aegis.engine.check --provider gemini` once with a real key before
relying on it.
"""

from __future__ import annotations

import json
import os
import time
import uuid
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
from .schema import sanitise_for_gemini

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        api_key_env: str = "GEMINI_API_KEY",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 90.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env
        self._timeout = timeout

    @property
    def api_key(self) -> str:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise ProviderError(
                f"{self._api_key_env} is not set, so the Gemini provider cannot be used.",
                provider=self.name,
            )
        return key

    # -- translation ----------------------------------------------------------

    @staticmethod
    def _tool_declarations(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description[:1024],
                "parameters": sanitise_for_gemini(tool.input_schema),
            }
            for tool in tools
        ]

    @staticmethod
    def _contents(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            if message.role is Role.SYSTEM:
                if message.content:
                    system.append(message.content)
                continue
            if message.role is Role.TOOL:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": message.tool_name or "tool",
                                    "response": _as_object(message.content),
                                }
                            }
                        ],
                    }
                )
                continue
            if message.role is Role.ASSISTANT:
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue
            contents.append({"role": "user", "parts": [{"text": message.content or ""}]})
        return ("\n\n".join(system) if system else None), contents

    # -- call -----------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        system, contents = self._contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"function_declarations": self._tool_declarations(tools)}]

        url = f"{self.base_url}/models/{self.model}:generateContent"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url, json=body, headers={"x-goog-api-key": self.api_key}
                )
        except httpx.HTTPError as exc:
            raise RateLimited(f"Gemini request failed: {exc}", provider=self.name) from exc

        latency = (time.perf_counter() - started) * 1000
        if response.status_code == 429:
            raise RateLimited("Gemini rate limit reached.", provider=self.name)
        if response.status_code >= 500:
            raise RateLimited(f"Gemini returned {response.status_code}.", provider=self.name)
        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini rejected the request ({response.status_code}): {response.text[:400]}",
                provider=self.name,
            )
        return self._parse(response.json(), latency)

    def _parse(self, payload: dict[str, Any], latency_ms: float) -> Completion:
        candidates = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback") or {}
            raise ProviderError(
                f"Gemini returned no candidates. Feedback: {json.dumps(feedback)[:300]}",
                provider=self.name,
            )
        candidate = candidates[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []

        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in parts:
            if part.get("text"):
                texts.append(part["text"])
            call = part.get("functionCall")
            if call:
                calls.append(
                    ToolCall(
                        id=f"gemini-{uuid.uuid4().hex[:8]}",
                        name=call.get("name", ""),
                        arguments=call.get("args") or {},
                    )
                )

        usage_raw = payload.get("usageMetadata") or {}
        return Completion(
            text="\n".join(texts) if texts else None,
            tool_calls=calls,
            provider=self.name,
            model=self.model,
            usage=Usage(
                input_tokens=int(usage_raw.get("promptTokenCount", 0) or 0),
                output_tokens=int(usage_raw.get("candidatesTokenCount", 0) or 0),
            ),
            latency_ms=round(latency_ms, 1),
            finish_reason=candidate.get("finishReason"),
            raw=payload,
        )


def _as_object(content: str | None) -> dict[str, Any]:
    """Gemini expects a function response to be an object, not a bare string."""
    if not content:
        return {"result": None}
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return {"result": content}
    return parsed if isinstance(parsed, dict) else {"result": parsed}
