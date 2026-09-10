"""The built-in engine.

The loop is the part that must be provably correct: that it executes tools,
feeds results back, stops when it should, survives a provider failure, and is
governed exactly like any other host. Proving that against a live model would be
slow, costly and flaky, so the model is scripted and the providers are tested
separately.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aegis.db.models import AuditEvent, Investigation
from aegis.db.session import session_scope
from aegis.engine.loop import AgentEngine
from aegis.engine.prompts import system_prompt
from aegis.engine.providers.base import Message, ProviderError, Role
from aegis.engine.providers.registry import ProviderChain
from aegis.engine.providers.scripted import AlwaysFailingProvider, ScriptedProvider


def last_tool_payload(messages: list[Message], tool_name: str) -> dict[str, Any]:
    """Read back what a named tool returned, so a script can chain ids."""
    for message in reversed(messages):
        if message.role is Role.TOOL and message.tool_name == tool_name:
            return json.loads(message.content or "{}")
    return {}


def chain_of(*providers) -> ProviderChain:
    return ProviderChain(providers=list(providers))


# -- the loop -----------------------------------------------------------------


async def test_engine_runs_the_whole_governed_workflow(gateway):
    """A scripted model, driving the real gateway, end to end."""

    def diagnose(messages, tools):
        started = last_tool_payload(messages, "start_investigation")
        return ScriptedProvider.call(
            "record_diagnosis",
            {
                "investigation_id": started["investigation_id"],
                "root_cause": "Disk exhaustion from orphaned Outlook data files.",
                "contributing_factors": ["Outdated Office build"],
                "evidence_cited": ["endpoint_get_device_health"],
                "confidence": "high",
            },
        )

    def propose(messages, tools):
        started = last_tool_payload(messages, "start_investigation")
        return ScriptedProvider.call(
            "propose_remediation",
            {
                "investigation_id": started["investigation_id"],
                "action": "endpoint_clear_disk_space",
                "arguments": {"device_id": "DEV-4411"},
                "rationale": "Reclaim 128 GB of reclaimable space.",
                "expected_outcome": "Disk below 75 percent.",
            },
        )

    def execute(messages, tools):
        started = last_tool_payload(messages, "start_investigation")
        proposal = last_tool_payload(messages, "propose_remediation")
        return ScriptedProvider.call(
            "execute_remediation",
            {
                "investigation_id": started["investigation_id"],
                "proposal_id": proposal["proposal_id"],
            },
        )

    def verify(messages, tools):
        started = last_tool_payload(messages, "start_investigation")
        proposal = last_tool_payload(messages, "propose_remediation")
        return ScriptedProvider.call(
            "verify_remediation",
            {
                "investigation_id": started["investigation_id"],
                "proposal_id": proposal["proposal_id"],
                "verdict": "resolved",
                "rationale": "Disk freed and health recovered.",
            },
        )

    def resolve(messages, tools):
        started = last_tool_payload(messages, "start_investigation")
        return ScriptedProvider.call(
            "resolve_investigation",
            {
                "investigation_id": started["investigation_id"],
                "resolution_code": "Solved (Workaround)",
                "summary": "Reclaimed disk space. Office update still required.",
            },
        )

    provider = ScriptedProvider(
        [
            ScriptedProvider.call("start_investigation", {"incident_number": "INC-1042"}),
            ScriptedProvider.call("endpoint_get_device_health", {"device_id": "DEV-4411"}),
            diagnose,
            propose,
            execute,
            verify,
            resolve,
            ScriptedProvider.say("Resolved INC-1042. Root cause was disk exhaustion."),
        ]
    )

    engine = AgentEngine(gateway.server, chain_of(provider))
    result = await engine.run("INC-1042")

    assert result.finished == "completed"
    assert result.investigation_id is not None
    assert result.tool_calls == 7
    assert result.providers_used == ["scripted"]

    with session_scope() as db:
        investigation = db.get(Investigation, result.investigation_id)
        assert investigation.state == "resolved"
        assert investigation.root_cause.startswith("Disk exhaustion")


async def test_the_engine_is_governed_like_any_other_host(gateway):
    """It cannot call a remediation directly, because it is just another client."""
    provider = ScriptedProvider(
        [
            ScriptedProvider.call("endpoint_clear_disk_space", {"device_id": "DEV-4411"}),
            ScriptedProvider.say("That was refused."),
        ]
    )
    before = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})
    result = await AgentEngine(gateway.server, chain_of(provider)).run("INC-1042")
    after = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})

    assert result.finished == "completed"
    assert after["telemetry"]["disk_used_pct"] == before["telemetry"]["disk_used_pct"]


async def test_a_tool_failure_is_shown_to_the_model_not_raised(gateway):
    captured: list[list[Message]] = []

    def observe(messages, tools):
        captured.append(list(messages))
        return ScriptedProvider.say("Understood, that tool does not exist.")

    provider = ScriptedProvider([ScriptedProvider.call("no_such_tool", {"x": 1}), observe])
    result = await AgentEngine(gateway.server, chain_of(provider)).run("INC-1042")

    assert result.finished == "completed"
    tool_message = [m for m in captured[0] if m.role is Role.TOOL][-1]
    payload = json.loads(tool_message.content)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["guidance"], "the model needs to be told what to do about it"


async def test_the_loop_stops_at_the_turn_limit(gateway):
    """A model that never concludes must not run forever."""
    provider = ScriptedProvider([ScriptedProvider.call("aegis_status", {}) for _ in range(10)])
    engine = AgentEngine(gateway.server, chain_of(provider), max_turns=3)
    result = await engine.run("INC-1042")

    assert result.finished == "max_turns"
    assert result.turns == 3
    assert "did not conclude" in result.error


async def test_the_engine_reads_its_rules_from_the_gateway(gateway):
    """Operating rules come from the governance layer, not a copy in the engine."""
    captured: list[str] = []

    def observe(messages, tools):
        captured.append(messages[0].content or "")
        return ScriptedProvider.say("done")

    await AgentEngine(gateway.server, chain_of(ScriptedProvider([observe]))).run("INC-1042")
    prompt = captured[0]
    assert "Operating rules, published by Aegis" in prompt
    assert "record_diagnosis" in prompt, "the gateway's workflow must reach the model"


async def test_model_calls_are_audited_with_provider_and_model(gateway):
    provider = ScriptedProvider([ScriptedProvider.say("Nothing to do.")])
    await AgentEngine(gateway.server, chain_of(provider)).run("INC-1042")

    with session_scope() as db:
        events = db.query(AuditEvent).filter(AuditEvent.event_type == "model.called").all()
        assert events, "model usage must be auditable"
        assert events[0].result["provider"] == "scripted"
        assert events[0].result["model"] == "scripted-1"

        completed = db.query(AuditEvent).filter(AuditEvent.event_type == "run.completed").one()
        assert completed.result["finished"] == "completed"


def test_system_prompt_falls_back_when_the_gateway_says_nothing():
    assert "IT operations analyst" in system_prompt(None)
    assert "Operating rules" not in system_prompt(None)


# -- the provider chain -------------------------------------------------------


async def test_the_chain_falls_back_to_the_next_provider():
    broken = AlwaysFailingProvider("gemini")
    working = ScriptedProvider([ScriptedProvider.say("hello")])
    chain = chain_of(broken, working)

    completion = await chain.complete([], [])
    assert completion.provider == "scripted"
    assert broken.attempts == 1
    assert [a.provider for a in chain.attempts] == ["gemini", "scripted"]
    assert chain.attempts[0].ok is False


async def test_the_chain_reports_when_every_provider_fails():
    chain = chain_of(AlwaysFailingProvider("gemini"), AlwaysFailingProvider("groq"))
    with pytest.raises(ProviderError, match="Every configured provider failed"):
        await chain.complete([], [])
    assert [a.ok for a in chain.attempts] == [False, False]


async def test_a_run_can_pick_a_provider_without_reordering_the_others():
    a = ScriptedProvider([ScriptedProvider.say("a")])
    a.name = "gemini"
    b = ScriptedProvider([ScriptedProvider.say("b")])
    b.name = "groq"
    chain = chain_of(a, b)

    assert chain.names == ["gemini", "groq"]
    assert chain.select("groq").names == ["groq", "gemini"]


def test_selecting_an_unconfigured_provider_is_an_error():
    chain = chain_of(ScriptedProvider([]))
    with pytest.raises(ProviderError, match="No provider named"):
        chain.select("nonexistent")


def test_the_shipped_provider_config_loads():
    from pathlib import Path

    from aegis.engine.providers.registry import load_chain

    chain = load_chain(Path(__file__).resolve().parents[1] / "llm_providers.yaml")
    assert chain.names == ["gemini", "groq"], "disabled providers must stay out of the chain"
    assert chain.on_exhausted == "replay"


# -- schema translation -------------------------------------------------------


def test_gemini_schema_drops_keys_gemini_rejects():
    from aegis.engine.providers.schema import sanitise_for_gemini

    cleaned = sanitise_for_gemini(
        {
            "type": "object",
            "additionalProperties": False,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"device_id": {"type": "string", "default": "x"}},
            "required": ["device_id"],
        }
    )
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned
    assert "default" not in cleaned["properties"]["device_id"]
    assert cleaned["required"] == ["device_id"]


def test_gemini_schema_flattens_nullable_unions():
    """Optionality as anyOf[T, null] becomes a plain T that is simply not required."""
    from aegis.engine.providers.schema import sanitise_for_gemini

    cleaned = sanitise_for_gemini(
        {
            "type": "object",
            "properties": {
                "categories": {
                    "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]
                }
            },
        }
    )
    categories = cleaned["properties"]["categories"]
    assert categories["type"] == "array"
    assert categories["items"] == {"type": "string"}


def test_real_gateway_schemas_survive_translation(gateway):
    """The tools Aegis actually publishes must be expressible to both providers."""
    from aegis.engine.providers.schema import sanitise_for_gemini, sanitise_for_openai

    for tool in gateway.host.tools.values():
        gemini = sanitise_for_gemini(tool.input_schema)
        assert gemini["type"] == "object"
        assert "additionalProperties" not in json.dumps(gemini)
        assert sanitise_for_openai(tool.input_schema)["type"] == "object"


def test_openai_adapter_translates_a_tool_exchange():
    from aegis.engine.providers.base import ToolCall
    from aegis.engine.providers.openai_compatible import OpenAICompatibleProvider

    wire = OpenAICompatibleProvider._messages(
        [
            Message(role=Role.SYSTEM, content="rules"),
            Message(role=Role.USER, content="investigate"),
            Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="get_incident", arguments={"number": "INC-1"})],
            ),
            Message(
                role=Role.TOOL,
                content='{"found": true}',
                tool_call_id="c1",
                tool_name="get_incident",
            ),
        ]
    )
    assert wire[2]["tool_calls"][0]["function"]["name"] == "get_incident"
    assert json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]) == {"number": "INC-1"}
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"found": true}'}


def test_gemini_adapter_translates_a_tool_exchange():
    from aegis.engine.providers.base import ToolCall
    from aegis.engine.providers.gemini import GeminiProvider

    system, contents = GeminiProvider._contents(
        [
            Message(role=Role.SYSTEM, content="rules"),
            Message(role=Role.USER, content="investigate"),
            Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="get_incident", arguments={"number": "INC-1"})],
            ),
            Message(
                role=Role.TOOL,
                content='{"found": true}',
                tool_call_id="c1",
                tool_name="get_incident",
            ),
        ]
    )
    assert system == "rules"
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "get_incident"
    # Gemini requires an object, so a bare string result must be wrapped.
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"found": True}


def test_gemini_wraps_non_object_tool_results():
    from aegis.engine.providers.gemini import _as_object

    assert _as_object("plain text") == {"result": "plain text"}
    assert _as_object('{"a": 1}') == {"a": 1}
    assert _as_object("[1, 2]") == {"result": [1, 2]}
    assert _as_object(None) == {"result": None}
