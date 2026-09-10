"""The built-in agent loop.

Connects to the Aegis gateway as an MCP client, exactly as Claude Code does, and
drives whichever model the provider chain resolves to. Because it is just
another host, the governance in the gateway applies to it unchanged: it cannot
call a remediation directly, cannot skip the workflow, and its every tool call
is audited.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import Client

from ..db.models import Actor, EventType
from ..governance.audit import AuditLog
from .prompts import opening_task, system_prompt
from .providers.base import Completion, Message, ProviderError, Role, ToolSpec
from .providers.registry import ProviderChain

logger = logging.getLogger(__name__)

# Results are fed back to the model as JSON. A single enormous payload would
# crowd out the rest of the investigation, so it is trimmed with a note saying so.
MAX_RESULT_CHARS = 12_000


@dataclass
class RunResult:
    incident_number: str
    investigation_id: int | None
    turns: int
    tool_calls: int
    finished: str  # completed | max_turns | provider_failure | error
    final_message: str | None
    providers_used: list[str] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_number": self.incident_number,
            "investigation_id": self.investigation_id,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "finished": self.finished,
            "final_message": self.final_message,
            "providers_used": self.providers_used,
            "models_used": self.models_used,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "duration_ms": round(self.duration_ms, 1),
            "replayed": self.replayed,
            "error": self.error,
        }


def _trim(payload: Any) -> str:
    text = json.dumps(payload, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return json.dumps(
        {
            "truncated": True,
            "original_size_chars": len(text),
            "note": "Result trimmed. Narrow the query if you need the rest.",
            "preview": text[:MAX_RESULT_CHARS],
        }
    )


class AgentEngine:
    """Runs one investigation to completion, or until it must stop."""

    def __init__(
        self,
        tool_source: Any,
        chain: ProviderChain,
        *,
        max_turns: int = 30,
        max_tokens: int = 4096,
        recorder: Any = None,
    ) -> None:
        self._tool_source = tool_source
        self._chain = chain
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        # Set to capture the model's turns so the run can be replayed later.
        self._recorder = recorder

    async def run(self, incident_number: str) -> RunResult:
        started = time.perf_counter()
        result = RunResult(
            incident_number=incident_number,
            investigation_id=None,
            turns=0,
            tool_calls=0,
            finished="error",
            final_message=None,
        )
        audit = AuditLog()

        result.replayed = any(p.name == "replay" for p in self._chain.providers)

        async with Client(self._tool_source, raise_exceptions=True) as client:
            listed = await client.list_tools()
            tools = [
                ToolSpec(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.input_schema or {"type": "object", "properties": {}},
                )
                for t in listed.tools
            ]
            messages = [
                Message(role=Role.SYSTEM, content=system_prompt(client.instructions)),
                Message(role=Role.USER, content=opening_task(incident_number)),
            ]

            for turn in range(1, self._max_turns + 1):
                result.turns = turn
                try:
                    completion = await self._chain.complete(
                        messages, tools, max_tokens=self._max_tokens
                    )
                except ProviderError as exc:
                    result.finished = "provider_failure"
                    result.error = str(exc)
                    audit.record(
                        EventType.MODEL_FAILED,
                        actor=Actor.SYSTEM,
                        summary="Every configured model provider failed",
                        error=str(exc),
                    )
                    break

                if self._recorder is not None:
                    self._recorder.observe(completion)
                self._record_completion(audit, result, completion)
                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=completion.text,
                        tool_calls=completion.tool_calls,
                    )
                )

                if not completion.wants_tools:
                    result.final_message = completion.text
                    result.finished = "completed"
                    break

                for call in completion.tool_calls:
                    payload = await self._execute(client, call.name, call.arguments)
                    result.tool_calls += 1
                    if call.name == "start_investigation" and isinstance(payload, dict):
                        found = payload.get("investigation_id")
                        if found:
                            result.investigation_id = int(found)
                            audit.investigation_id = result.investigation_id
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=_trim(payload),
                            tool_call_id=call.id,
                            tool_name=call.name,
                        )
                    )
            else:
                result.finished = "max_turns"
                result.error = f"The investigation did not conclude within {self._max_turns} turns."

        result.duration_ms = (time.perf_counter() - started) * 1000
        audit.record(
            EventType.RUN_COMPLETED,
            actor=Actor.SYSTEM,
            summary=(
                f"Engine run for {incident_number} finished as {result.finished} "
                f"after {result.turns} turns and {result.tool_calls} tool calls"
            ),
            result=result.as_dict(),
            latency_ms=result.duration_ms,
            error=result.error,
        )
        return result

    @staticmethod
    def _record_completion(audit: AuditLog, result: RunResult, completion: Completion) -> None:
        if completion.provider not in result.providers_used:
            result.providers_used.append(completion.provider)
        if completion.model not in result.models_used:
            result.models_used.append(completion.model)
        result.input_tokens += completion.usage.input_tokens
        result.output_tokens += completion.usage.output_tokens
        audit.record(
            EventType.MODEL_CALLED,
            actor=Actor.SYSTEM,
            summary=(
                f"{completion.provider}/{completion.model} returned "
                + (
                    f"{len(completion.tool_calls)} tool call(s)"
                    if completion.tool_calls
                    else "a final message"
                )
            ),
            result={
                "provider": completion.provider,
                "model": completion.model,
                "input_tokens": completion.usage.input_tokens,
                "output_tokens": completion.usage.output_tokens,
                "finish_reason": completion.finish_reason,
                "tools_requested": [c.name for c in completion.tool_calls],
            },
            latency_ms=completion.latency_ms,
        )

    @staticmethod
    async def _execute(client: Client, name: str, arguments: dict[str, Any]) -> Any:
        """Call a gateway tool. A failure is data the model must see, not a crash."""
        try:
            outcome = await client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model instead
            logger.info("Tool %s raised: %s", name, exc)
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "guidance": "That call did not work. Check the tool name and arguments.",
            }
        if outcome.structured_content is not None and not outcome.is_error:
            return outcome.structured_content

        texts = [c.text for c in (outcome.content or []) if getattr(c, "type", None) == "text"]
        detail = "\n".join(texts) if texts else None
        if not outcome.is_error:
            return {"ok": True, "text": detail} if detail else {"ok": True}

        # An error must arrive as data the model can act on. A weaker model given
        # only "Unknown tool: x" tends to retry the same call.
        return {
            "ok": False,
            "error": detail or "The tool call failed without a message.",
            "guidance": (
                "That call did not work. Check the tool name against the tools you were "
                "given and check the argument names, then either correct it or continue "
                "without it. Do not repeat the same call unchanged."
            ),
        }
