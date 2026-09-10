"""Replay mode.

Replay plays back the model's decisions only. Everything else runs for real, so
a replayed demo still passes through policy, still blocks for an approval, still
changes device state and still verifies by measurement. These tests hold it to
that, because a replay that faked the outcome would make the demo a lie.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.db.models import AuditEvent, Investigation, Proposal, Verification
from aegis.db.session import session_scope
from aegis.engine.loop import AgentEngine
from aegis.engine.providers.base import ProviderError, Role
from aegis.engine.providers.registry import ProviderChain
from aegis.engine.providers.scripted import ScriptedProvider
from aegis.replay.provider import ReplayProvider
from aegis.replay.trace import Trace, TraceRecorder, TraceTurn, available_traces

TRACES = Path(__file__).resolve().parents[1] / "traces"


def headline() -> Trace:
    return Trace.load(TRACES / "INC-1042-headline.json")


# -- the shipped trace --------------------------------------------------------

def test_the_shipped_trace_loads_and_is_labelled_honestly():
    trace = headline()
    assert trace.incident_number == "INC-1042"
    assert trace.origin == "authored", "an authored trace must not claim to be a recording"
    assert trace.is_recorded is False
    assert "AUTHORED, NOT RECORDED" in trace.notes


def test_traces_are_discovered_from_disk():
    assert "INC-1042" in available_traces(TRACES)


def test_a_trace_from_a_future_schema_is_refused():
    with pytest.raises(ValueError, match="schema version"):
        Trace.from_dict({"schema_version": 99, "incident_number": "INC-1", "turns": []})


# -- playback -----------------------------------------------------------------

async def test_a_replayed_run_still_passes_through_the_whole_workflow(gateway):
    engine = AgentEngine(gateway.server, ProviderChain(providers=[ReplayProvider(headline(), pacing=0)]))
    result = await engine.run("INC-1042")

    assert result.finished == "completed"
    assert result.replayed is True
    assert result.investigation_id is not None

    with session_scope() as db:
        investigation = db.get(Investigation, result.investigation_id)
        assert investigation.state == "resolved"
        assert investigation.resolution_code == "Solved (Workaround)"
        assert "orphaned OST" in investigation.root_cause

        proposal = db.query(Proposal).one()
        assert proposal.state == "executed"
        assert proposal.approved_by, "a replayed run must still be approved by a person"

        verification = db.query(Verification).one()
        assert verification.measured_verdict in ("resolved", "partially_resolved")
        assert verification.agreed is True


async def test_a_replayed_remediation_really_changes_the_device(gateway):
    before = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})
    engine = AgentEngine(gateway.server, ProviderChain(providers=[ReplayProvider(headline(), pacing=0)]))
    await engine.run("INC-1042")
    after = await gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})

    assert before["telemetry"]["disk_used_pct"] == 97.0
    assert after["telemetry"]["disk_used_pct"] == 72.0
    assert after["health_score"] > before["health_score"] + 30


async def test_replay_is_blocked_by_the_approval_gate_like_anything_else(ungoverned_gateway):
    """With no approval channel attached, a replayed remediation must not run."""
    engine = AgentEngine(
        ungoverned_gateway.server, ProviderChain(providers=[ReplayProvider(headline(), pacing=0)])
    )
    await engine.run("INC-1042")
    after = await ungoverned_gateway.invoke("endpoint_get_device_health", {"device_id": "DEV-4411"})
    assert after["telemetry"]["disk_used_pct"] == 97.0, "an unapproved replay must change nothing"


async def test_replay_is_audited_as_a_replay(gateway):
    engine = AgentEngine(gateway.server, ProviderChain(providers=[ReplayProvider(headline(), pacing=0)]))
    await engine.run("INC-1042")
    with session_scope() as db:
        events = db.query(AuditEvent).filter(AuditEvent.event_type == "model.called").all()
        assert events, "replayed turns must still be recorded"
        assert all(e.result["provider"] == "replay" for e in events)


# -- placeholder resolution ---------------------------------------------------

async def test_placeholders_bind_to_ids_from_the_live_run(gateway):
    """Ids differ every run, so a trace must not hard-code the ones it recorded."""
    raw = headline().as_dict()
    assert any(
        "$investigation_id" in str(turn["tool_calls"]) for turn in raw["turns"]
    ), "the trace should use placeholders rather than fixed ids"

    engine = AgentEngine(gateway.server, ProviderChain(providers=[ReplayProvider(headline(), pacing=0)]))
    result = await engine.run("INC-1042")

    # The placeholders resolved to the ids this run actually produced, not the
    # ones that were recorded, which is what makes a trace reusable.
    with session_scope() as db:
        proposal = db.query(Proposal).one()
        assert proposal.investigation_id == result.investigation_id
        assert proposal.arguments["device_id"] == "DEV-4411"
        assert proposal.state == "executed"


def test_a_placeholder_with_no_binding_is_left_alone():
    from aegis.replay.provider import _substitute

    assert _substitute("$missing", {}) == "$missing"
    assert _substitute("$investigation_id", {"investigation_id": 7}) == 7
    assert _substitute({"a": "$x"}, {"x": 1}) == {"a": 1}
    assert _substitute(["$x", "plain"], {"x": 2}) == [2, "plain"]


async def test_running_past_the_end_of_a_trace_is_a_clear_error():
    provider = ReplayProvider(Trace(incident_number="INC-1", turns=[TraceTurn(text="done")]), pacing=0)
    await provider.complete([], [])
    with pytest.raises(ProviderError, match="ran out of turns"):
        await provider.complete([], [])


# -- recording ----------------------------------------------------------------

async def test_a_live_run_can_be_recorded_and_replayed(gateway):
    """Record a run, then replay it, and get the same outcome."""
    def diagnose(messages, tools):
        for message in reversed(messages):
            if message.role is Role.TOOL and message.tool_name == "start_investigation":
                import json

                return ScriptedProvider.call(
                    "record_diagnosis",
                    {
                        "investigation_id": json.loads(message.content)["investigation_id"],
                        "root_cause": "Disk exhaustion.",
                        "contributing_factors": [],
                        "evidence_cited": ["endpoint_get_device_health"],
                        "confidence": "high",
                    },
                )
        raise AssertionError("no investigation to diagnose")

    provider = ScriptedProvider(
        [
            ScriptedProvider.call("start_investigation", {"incident_number": "INC-1042"}),
            diagnose,
            ScriptedProvider.say("Diagnosed; no action needed yet."),
        ]
    )
    recorder = TraceRecorder("INC-1042")
    engine = AgentEngine(gateway.server, ProviderChain(providers=[provider]), recorder=recorder)
    await engine.run("INC-1042")

    trace = recorder.finish(notes="from a test")
    assert trace.origin == "recorded"
    assert len(trace.turns) == 3
    assert trace.turns[0].tool_calls[0]["name"] == "start_investigation"
    assert trace.turns[-1].text.startswith("Diagnosed")


def test_a_recorded_trace_round_trips_through_disk(tmp_path):
    recorder = TraceRecorder("INC-9999")
    recorder.turns = [TraceTurn(tool_calls=[{"name": "x", "arguments": {"a": 1}}], text=None)]
    saved = recorder.finish(notes="round trip").save(tmp_path / "t.json")
    reloaded = Trace.load(saved)
    assert reloaded.incident_number == "INC-9999"
    assert reloaded.turns[0].tool_calls[0]["arguments"] == {"a": 1}
    assert reloaded.origin == "recorded"
