"""The console API.

These run against the real gateway and the real demo systems, so they cover the
path a browser actually takes.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from aegis.db.models import AuditEvent, Proposal
from aegis.db.session import session_scope


class _LifespanRunner:
    """Runs an app's lifespan inside one task.

    The MCP transport mounted in the app opens anyio cancel scopes, and those
    must be entered and exited in the same task. A pytest async-generator
    fixture does not guarantee that, so the lifespan gets its own task here.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._error: BaseException | None = None

    async def __aenter__(self):
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._error is not None:
            raise self._error
        return self.app

    async def _run(self) -> None:
        try:
            async with self.app.router.lifespan_context(self.app):
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            self._error = exc
        finally:
            self._ready.set()

    async def __aexit__(self, *exc_info: object) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task


@pytest.fixture()
async def client(settings, fresh_enterprise_state, monkeypatch):
    """The FastAPI app, wired to the throwaway database and demo systems."""
    monkeypatch.setenv("AEGIS_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("AEGIS_UPSTREAMS_FILE", str(settings.upstreams_file))
    monkeypatch.setenv("AEGIS_POLICY_FILE", str(settings.policy_file))

    from aegis.api import app as app_module

    # The dashboard cache is module state and would otherwise carry answers
    # from one test's database into the next one's.
    app_module._dashboard_cache["payload"] = None

    application = app_module.create_app()
    async with _LifespanRunner(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


ADMIN = {"X-Aegis-Persona": "Rebecca Lindqvist", "X-Aegis-Role": "it_admin"}
AUDITOR = {"X-Aegis-Persona": "An Auditor", "X-Aegis-Role": "auditor"}


async def test_status_reports_systems_and_engine_readiness(client):
    payload = (await client.get("/api/status")).json()
    assert {s["name"] for s in payload["systems"]} == {"itsm", "itam", "endpoint"}
    assert all(s["connected"] for s in payload["systems"])
    assert payload["remediation_actions"], "the console needs the proposable actions"
    # No API key is set in the test environment, so the engine must say so plainly
    # rather than offering a run that cannot happen.
    assert payload["engine"]["available"] is False
    assert "API key" in payload["engine"]["reason"]


async def test_incident_queue_and_detail(client):
    queue = (await client.get("/api/incidents")).json()
    assert any(i["number"] == "INC-1042" for i in queue["incidents"])

    detail = (await client.get("/api/incidents/INC-1042")).json()
    assert detail["incident"]["caller"]["display_name"] == "Priya Raghavan"
    assert detail["incident"]["description"]["content_type"] == "untrusted_user_supplied_text"


async def test_creating_an_incident_goes_through_the_gateway(client):
    response = await client.post(
        "/api/incidents",
        json={
            "short_description": "Screen flickering",
            "description": "The display flickers when docked.",
            "caller_id": "USR-1002",
            "device_id": "DEV-4412",
            "priority": 3,
            "investigate_automatically": False,
        },
    )
    assert response.status_code == 201
    number = response.json()["incident"]["number"]

    with session_scope() as db:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.tool_name == "itsm_create_incident")
            .one()
        )
        assert event.policy_decision == "allow"
        assert event.risk_class == "WRITE_LOW"

    assert (await client.get(f"/api/incidents/{number}")).json()["found"] is True


async def test_auto_mode_falls_back_to_replay_when_no_model_is_available(client):
    """This is what lets the demo run with no API key."""
    response = await client.post("/api/incidents/INC-1042/investigate?mode=auto")
    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    assert payload["mode"] == "replay"
    assert payload["trace_origin"] == "authored"


async def test_a_live_run_is_refused_rather_than_quietly_replayed(client):
    """Asking for live must not silently give you a recording instead."""
    response = await client.post("/api/incidents/INC-1042/investigate?mode=live")
    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


async def test_an_incident_with_no_trace_and_no_model_says_so(client):
    response = await client.post("/api/incidents/INC-1039/investigate?mode=auto")
    assert response.status_code == 503
    assert "no stored trace" in response.json()["detail"]


async def test_replay_availability_is_reported_honestly(client):
    payload = (await client.get("/api/status")).json()
    trace = payload["replay"]["traces"]["INC-1042"]
    assert trace["origin"] == "authored"
    assert "NOT RECORDED" in trace["notes"]


async def test_device_page_gathers_every_system(client):
    payload = (await client.get("/api/devices/DEV-4411")).json()
    assert payload["health"]["band"] == "critical"
    assert payload["patches"]["missing_critical_count"] == 2
    assert payload["asset"]["support_position"] == "out_of_warranty"
    assert payload["incidents"]["count"] >= 5
    assert payload["history"]["trend"]["direction"] == "declining"


async def test_a_manual_action_is_gated_exactly_like_the_agents(client):
    """A person clicking a button is not a reason to skip the controls."""
    before = (await client.get("/api/devices/DEV-4411")).json()

    raised = await client.post(
        "/api/devices/DEV-4411/actions",
        headers=ADMIN,
        json={
            "action": "endpoint_clear_disk_space",
            "arguments": {},
            "rationale": "Disk critically low.",
        },
    )
    assert raised.status_code == 201
    proposal = raised.json()
    assert proposal["approval_required"] is True
    assert proposal["state"] == "pending"

    during = (await client.get("/api/devices/DEV-4411")).json()
    assert during["health"]["telemetry"]["disk_used_pct"] == before["health"]["telemetry"]["disk_used_pct"]

    approved = await client.post(
        f"/api/approvals/{proposal['proposal_id']}",
        headers=ADMIN,
        json={"approve": True, "note": "Approved in the change window."},
    )
    assert approved.status_code == 200
    assert approved.json()["executed"] is True

    after = (await client.get("/api/devices/DEV-4411")).json()
    assert after["health"]["telemetry"]["disk_used_pct"] < 75


async def test_an_auditor_cannot_approve_through_the_console(client):
    raised = await client.post(
        "/api/devices/DEV-4411/actions",
        headers=AUDITOR,
        json={"action": "endpoint_restart_application", "arguments": {"process_name": "OUTLOOK"},
              "rationale": "Crashing repeatedly."},
    )
    proposal_id = raised.json()["proposal_id"]

    refused = await client.post(
        f"/api/approvals/{proposal_id}", headers=AUDITOR, json={"approve": True}
    )
    assert refused.status_code == 403
    assert "may not approve" in refused.json()["detail"]

    with session_scope() as db:
        assert db.get(Proposal, proposal_id).state == "pending"


async def test_a_denied_action_cannot_be_raised_from_the_console(client):
    response = await client.post(
        "/api/devices/DEV-4411/actions",
        headers=ADMIN,
        json={"action": "endpoint_reimage_device", "arguments": {}, "rationale": "Start fresh."},
    )
    assert response.status_code == 400
    assert "not an available action" in response.json()["detail"]


async def test_policy_view_shows_how_each_tool_is_reachable(client):
    payload = (await client.get("/api/policy")).json()
    by_tool = {rule["tool"]: rule for rule in payload["rules"]}

    assert by_tool["endpoint_get_device_health"]["published"] is True
    assert by_tool["endpoint_clear_disk_space"]["proposal_only"] is True
    assert by_tool["endpoint_clear_disk_space"]["published"] is False
    assert by_tool["itsm_resolve_incident"]["workflow_managed"] is True
    assert by_tool["endpoint_reimage_device"]["decision"] == "deny"
    assert payload["roles"]["auditor"]["may_approve"] == []


async def test_audit_endpoint_returns_the_trail(client):
    await client.get("/api/incidents/INC-1042")
    payload = (await client.get("/api/audit")).json()
    assert payload["count"] > 0
    assert any(e["tool"] == "itsm_get_incident" for e in payload["events"])


async def test_reset_rebuilds_the_demo(client):
    await client.post(
        "/api/devices/DEV-4411/actions",
        headers=ADMIN,
        json={"action": "endpoint_clear_disk_space", "arguments": {}, "rationale": "cleanup"},
    )
    result = await client.post("/api/demo/reset")
    assert result.status_code == 200

    with session_scope() as db:
        assert db.query(Proposal).count() == 0
        assert db.query(AuditEvent).count() == 0
    device = (await client.get("/api/devices/DEV-4411")).json()
    assert device["health"]["telemetry"]["disk_used_pct"] == 97.0


async def test_dashboard_reports_the_estate_and_aegis_own_governance(client):
    payload = (await client.get("/api/dashboard")).json()

    fleet = payload["fleet"]
    assert fleet["device_count"] > 1, "the dashboard needs a fleet, not one device"
    assert sum(fleet["health"]["bands"].values()) == fleet["scored_device_count"]
    assert fleet["patching"]["devices_missing_critical"] >= 1
    # The story device is the worst in the estate, which is what makes it the
    # one an operator would click through to.
    assert fleet["devices_needing_attention"][0]["device_id"] == "DEV-4411"

    incidents = payload["incidents"]
    assert incidents["total"] >= incidents["open"] > 0
    assert incidents["resolution_quality"]["resolved_as_workaround"] > 0
    assert any(c["caller_id"] == "USR-1001" for c in incidents["repeat_callers"])

    assert payload["assets"]["total"] > 1
    assert payload["sources"] == {"fleet": "endpoint", "incidents": "itsm", "assets": "itam"}

    governance = payload["governance"]
    assert governance["investigations"]["funnel"][0]["state"] == "investigating"
    assert governance["approvals"]["pending"] == 0
    assert governance["policy"]["governed_tool_calls"] >= 3


async def test_dashboard_reads_are_audited_but_are_not_evidence(client):
    """A screen refresh is a governed call; it is not something an agent saw."""
    from aegis.db.models import Evidence

    await client.get("/api/dashboard?refresh=true")

    with session_scope() as db:
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.tool_name == "endpoint_get_fleet_summary")
            .all()
        )
        assert events and all(e.policy_decision == "allow" for e in events)
        assert all(e.risk_class == "READ" for e in events)
        assert (
            db.query(Evidence)
            .filter(Evidence.tool_name == "endpoint_get_fleet_summary")
            .count()
            == 0
        )


async def test_dashboard_is_cached_between_refreshes(client):
    first = (await client.get("/api/dashboard?refresh=true")).json()
    second = (await client.get("/api/dashboard")).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["generated_at"] == first["generated_at"]


async def test_dashboard_counts_an_approval_that_has_already_executed(client):
    """A proposal moves to "executed" once it runs, and it was still approved.

    Counting only proposals sitting in the "approved" state reported zero
    approvals for a demo that had just completed successfully, which is the
    opposite of what the screen is there to show.
    """
    raised = await client.post(
        "/api/devices/DEV-4411/actions",
        headers=ADMIN,
        json={"action": "endpoint_clear_disk_space", "arguments": {}, "rationale": "Disk low."},
    )
    proposal_id = raised.json()["proposal_id"]

    pending_view = (await client.get("/api/dashboard?refresh=true")).json()["governance"]
    assert pending_view["approvals"] == {"pending": 1, "approved": 0, "rejected": 0}

    await client.post(
        f"/api/approvals/{proposal_id}", headers=ADMIN, json={"approve": True, "note": "go"}
    )

    with session_scope() as db:
        assert db.get(Proposal, proposal_id).state == "executed"

    after = (await client.get("/api/dashboard?refresh=true")).json()["governance"]
    assert after["approvals"] == {"pending": 0, "approved": 1, "rejected": 0}
