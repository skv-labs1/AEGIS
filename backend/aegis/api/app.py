"""The console API.

One FastAPI process serves three things: the MCP gateway at /mcp, this JSON API
at /api, and the built console at /. Keeping them together makes local
development and a single-container deployment simple; nothing here depends on
that arrangement, and the gateway can be split out unchanged.

The live view is a tail of the audit table rather than a separate event bus. The
console therefore watches exactly what an auditor would later read, so the two
cannot disagree.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db.models import AuditEvent, Evidence, Investigation, Proposal
from ..db.session import init_engine, session_scope
from ..engine.loop import AgentEngine
from ..engine.providers.registry import ProviderChain, load_chain
from ..gateway.server import AegisGateway
from ..governance.approvals import DatabaseApprovalService, decide
from ..replay.provider import ReplayProvider
from ..replay.trace import Trace, available_traces
from ..workflow.manual import ManualActionService
from ..workflow.service import WorkflowError

logger = logging.getLogger(__name__)

# backend/aegis/api/app.py -> repo root is three levels up.
SPA_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class Console:
    """Everything the API needs, assembled once at startup."""

    gateway: AegisGateway
    manual: ManualActionService
    chain: ProviderChain | None
    runs: dict[str, asyncio.Task[Any]]
    last_runs: dict[str, dict[str, Any]]
    traces: dict[str, Trace]

    def __init__(self) -> None:
        self.runs = {}
        self.last_runs = {}
        self.traces = {}
        self.chain = None


console = Console()


# -- request models -----------------------------------------------------------


class NewIncident(BaseModel):
    short_description: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=4000)
    caller_id: str
    device_id: str | None = None
    priority: int = Field(default=3, ge=1, le=4)
    investigate_automatically: bool = True


class Decision(BaseModel):
    approve: bool
    note: str | None = None


class ManualAction(BaseModel):
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=3, max_length=1000)


# -- persona ------------------------------------------------------------------


class Persona(BaseModel):
    name: str
    role: str


def current_persona(
    x_aegis_persona: str | None = Header(default=None),
    x_aegis_role: str | None = Header(default=None),
) -> Persona:
    """Who is acting in the console.

    This is a demo identity, not authentication: the console sends whoever the
    user picked from the persona menu. It exists to demonstrate separation of
    duties, and the role is still checked against the policy before anything is
    approved. Real single sign-on is a documented extension, not built.
    """
    return Persona(name=x_aegis_persona or "Console User", role=x_aegis_role or "service_desk")


# -- lifespan -----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    gateway = console.gateway
    await gateway.start()
    console.manual = ManualActionService(
        invoke=gateway.invoke,
        policy=gateway.policy,
        remediation_actions=lambda: gateway._remediation_catalogue,
    )
    settings = get_settings()
    try:
        console.chain = load_chain(settings.policy_file.parent.parent / "llm_providers.yaml")
    except Exception as exc:  # noqa: BLE001 - the console works without an engine
        logger.warning("No usable LLM provider chain: %s", exc)
        console.chain = None
    console.traces = available_traces()
    if console.traces:
        logger.info("Replay traces available for %s", ", ".join(sorted(console.traces)))

    # The MCP transport owns a task group that must be entered here. Mounting the
    # sub-app is not enough on its own: without this, every request to /mcp fails
    # with "Task group is not initialized".
    async with gateway.server.session_manager.run():
        try:
            yield
        finally:
            await gateway.stop()


def create_app() -> FastAPI:
    settings: Settings = get_settings()
    init_engine(settings)
    console.gateway = AegisGateway(
        settings,
        approvals=DatabaseApprovalService(timeout_seconds=settings.approval_timeout_seconds),
    )

    app = FastAPI(
        title="Aegis",
        version="0.5.0",
        description="Governed agentic IT operations",
        lifespan=lifespan,
    )
    # Mounted before startup so its transport's task group is started by this
    # app's lifespan, and served at the same origin as the console so one URL
    # covers both. The mount answers on /mcp/; the redirect below means a client
    # configured with /mcp works too, which is otherwise a silent 405.
    app.mount("/mcp", console.gateway.server.streamable_http_app(streamable_http_path="/"))

    @app.api_route(
        "/mcp",
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )
    async def mcp_without_trailing_slash(request: Request) -> RedirectResponse:
        return RedirectResponse("/mcp/", status_code=307)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register(app)
    if SPA_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=SPA_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            return FileResponse(SPA_DIR / "index.html")

    return app


# -- helpers ------------------------------------------------------------------


async def _tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await console.gateway.invoke(name, arguments)
    if result.get("refused"):
        raise HTTPException(status_code=403, detail=result.get("reason", "Refused by policy."))
    return result


def _register(app: FastAPI) -> None:
    # -- meta -----------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        gateway = console.gateway
        by_risk: dict[str, list[str]] = {}
        for name in gateway.published_tools:
            tool = gateway.host.get(name)
            decision = gateway.policy.evaluate(name, tool.annotations if tool else None)
            by_risk.setdefault(decision.risk.value, []).append(name)
        return {
            "systems": [s.as_dict() for s in gateway.host.status],
            "published_tools": len(gateway.published_tools),
            "tools_by_risk_class": {k: sorted(v) for k, v in sorted(by_risk.items())},
            "remediation_actions": [
                {k: v for k, v in entry.items() if k != "annotations"}
                for entry in gateway._remediation_catalogue.values()
            ],
            "workflow_managed": gateway._workflow_managed,
            "policy_version": gateway.policy.version,
            "engine": {
                "available": bool(console.chain and console.chain.usable),
                "providers": console.chain.names if console.chain else [],
                "ready_providers": console.chain.ready_names if console.chain else [],
                "reason": (
                    None
                    if console.chain and console.chain.usable
                    else "No model provider has its API key set. Set GEMINI_API_KEY or "
                    "GROQ_API_KEY, drive the gateway from an MCP host such as Claude Code, "
                    "or use replay."
                ),
            },
            "last_runs": dict(console.last_runs),
            "replay": {
                "available_for": sorted(console.traces),
                "traces": {
                    number: {
                        "origin": trace.origin,
                        "provider": trace.provider,
                        "model": trace.model,
                        "turns": len(trace.turns),
                        "recorded_at": trace.recorded_at,
                        "notes": trace.notes,
                    }
                    for number, trace in console.traces.items()
                },
            },
        }

    @app.get("/api/policy")
    async def policy() -> dict[str, Any]:
        gateway = console.gateway
        rules = []
        for tool in sorted(set(gateway.policy.classified_tools())):
            decision = gateway.policy.evaluate(tool)
            rules.append(
                {
                    **decision.as_dict(),
                    "published": tool in gateway.published_tools,
                    "proposal_only": tool in gateway._remediation_catalogue,
                    "workflow_managed": tool in gateway._workflow_managed,
                }
            )
        return {
            "version": gateway.policy.version,
            "roles": {
                name: {
                    "label": role.label,
                    "may_approve": sorted(r.value for r in role.may_approve),
                }
                for name, role in gateway.policy.roles.items()
            },
            "unclassified_defaults": {
                "read": gateway.policy.unclassified_read,
                "write": gateway.policy.unclassified_write,
                "reason": gateway.policy.unclassified_reason,
            },
            "rules": rules,
        }

    # -- incidents ------------------------------------------------------------

    @app.get("/api/incidents")
    async def incidents(state: str | None = None, limit: int = 50) -> dict[str, Any]:
        args: dict[str, Any] = {"limit": limit}
        if state:
            args["state"] = state
        result = await _tool("itsm_search_incidents", args)
        with session_scope() as db:
            linked = {
                i.incident_number: {"investigation_id": i.id, "state": i.state}
                for i in db.query(Investigation).all()
            }
        for incident in result.get("incidents", []):
            incident["investigation"] = linked.get(incident["number"])
        return result

    @app.get("/api/incidents/{number}")
    async def incident(number: str) -> dict[str, Any]:
        result = await _tool("itsm_get_incident", {"number": number})
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=f"No incident {number}")
        with session_scope() as db:
            record = (
                db.query(Investigation)
                .filter(Investigation.incident_number == number.upper())
                .order_by(Investigation.id.desc())
                .first()
            )
            result["investigation"] = record.as_dict() if record else None
        return result

    @app.post("/api/incidents", status_code=201)
    async def create_incident(body: NewIncident) -> dict[str, Any]:
        created = await _tool(
            "itsm_create_incident",
            {
                "short_description": body.short_description,
                "description": body.description,
                "caller_id": body.caller_id,
                "device_id": body.device_id,
                "priority": body.priority,
            },
        )
        if not created.get("created"):
            raise HTTPException(status_code=400, detail=created.get("error", "Not created"))
        number = created["incident"]["number"]
        if body.investigate_automatically:
            try:
                created["run"] = _start_run(number)
            except HTTPException as exc:
                created["run"] = {"started": False, "reason": exc.detail}
        return created

    @app.post("/api/incidents/{number}/investigate")
    async def investigate(number: str, mode: str = "auto") -> dict[str, Any]:
        """Start an investigation.

        `mode` is auto, live or replay. Auto prefers a live model and falls back
        to a stored trace, which is what lets the demo run with no API key.
        """
        return _start_run(number, mode)

    def _start_run(number: str, mode: str = "auto") -> dict[str, Any]:
        incident = number.upper()
        if mode not in {"auto", "live", "replay"}:
            raise HTTPException(status_code=400, detail="mode must be auto, live or replay")

        live_possible = bool(console.chain and console.chain.usable)
        trace = console.traces.get(incident)

        if mode == "live" and not live_possible:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No model provider has its API key set, so a live run is not possible. "
                    "Set GEMINI_API_KEY or GROQ_API_KEY, or use replay."
                ),
            )
        if mode == "replay" and trace is None:
            raise HTTPException(status_code=404, detail=f"No stored trace for {incident}.")

        replaying = mode == "replay" or (mode == "auto" and not live_possible)
        if replaying and trace is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No model provider has its API key set and there is no stored trace for "
                    f"{incident}. Set GEMINI_API_KEY or GROQ_API_KEY, or drive the gateway "
                    "from an MCP host such as Claude Code and watch it here."
                ),
            )

        existing = console.runs.get(incident)
        if existing and not existing.done():
            return {"started": False, "reason": "An investigation is already running."}

        # Replay swaps out the model and nothing else. The gateway, policy,
        # approval gate, remediation and verification all still run for real.
        chain = ProviderChain(providers=[ReplayProvider(trace)]) if replaying else console.chain

        async def run() -> Any:
            """A background run must not fail silently; the console shows the outcome."""
            try:
                engine = AgentEngine(console.gateway.server, chain)
                result = await engine.run(incident)
                console.last_runs[incident] = result.as_dict()
                return result
            except Exception as exc:
                logger.exception("Engine run for %s failed", incident)
                console.last_runs[incident] = {
                    "incident_number": incident,
                    "finished": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                raise

        console.runs[incident] = asyncio.create_task(run())
        return {
            "started": True,
            "incident_number": incident,
            "mode": "replay" if replaying else "live",
            "trace_origin": trace.origin if replaying and trace else None,
        }
        return {"started": True, "incident_number": number.upper()}

    # -- investigations -------------------------------------------------------

    @app.get("/api/investigations")
    async def investigations(limit: int = 50) -> dict[str, Any]:
        with session_scope() as db:
            rows = db.query(Investigation).order_by(Investigation.id.desc()).limit(limit).all()
            return {"count": len(rows), "investigations": [r.as_dict() for r in rows]}

    @app.get("/api/investigations/{investigation_id}")
    async def investigation(investigation_id: int) -> dict[str, Any]:
        try:
            payload = console.gateway.workflow.get(investigation_id)
        except WorkflowError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        with session_scope() as db:
            payload["evidence"] = [
                e.as_dict()
                for e in db.query(Evidence)
                .filter(Evidence.investigation_id == investigation_id)
                .order_by(Evidence.id)
                .all()
            ]
            payload["events"] = [
                e.as_dict()
                for e in db.query(AuditEvent)
                .filter(AuditEvent.investigation_id == investigation_id)
                .order_by(AuditEvent.id)
                .all()
            ]
        return payload

    # -- approvals ------------------------------------------------------------

    @app.get("/api/approvals")
    async def approvals(include_decided: bool = False, limit: int = 50) -> dict[str, Any]:
        with session_scope() as db:
            query = db.query(Proposal).order_by(Proposal.id.desc())
            if not include_decided:
                query = query.filter(Proposal.state == "pending")
            rows = query.limit(limit).all()
            payload = []
            for p in rows:
                entry = p.as_dict()
                entry["policy"] = console.gateway.policy.evaluate(p.action).as_dict()
                payload.append(entry)
            return {"count": len(payload), "proposals": payload}

    @app.post("/api/approvals/{proposal_id}")
    async def decide_proposal(
        proposal_id: int,
        body: Decision,
        persona: Persona = Depends(current_persona),
    ) -> dict[str, Any]:
        try:
            result = decide(
                proposal_id,
                approve=body.approve,
                approver=persona.name,
                role=persona.role,
                note=body.note,
                policy=console.gateway.policy,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # A manual action has no agent waiting on it, so the console runs it.
        if body.approve and result.get("investigation_id") is None:
            await console.manual.execute(proposal_id, persona.name)
            result = {**result, "executed": True}
        return result

    # -- devices --------------------------------------------------------------

    @app.get("/api/devices/{device_id}")
    async def device(device_id: str) -> dict[str, Any]:
        detail = await _tool("endpoint_get_device", {"identifier": device_id})
        if not detail.get("found"):
            raise HTTPException(status_code=404, detail=f"No device {device_id}")
        resolved = detail["device"]["device_id"]
        health, software, patches, history, incidents_, asset = await asyncio.gather(
            _tool("endpoint_get_device_health", {"device_id": resolved}),
            _tool("endpoint_get_installed_software", {"device_id": resolved}),
            _tool("endpoint_get_patch_status", {"device_id": resolved}),
            _tool("endpoint_get_health_history", {"device_id": resolved}),
            _tool("itsm_search_incidents", {"device_id": resolved, "limit": 20}),
            _tool("itam_get_asset", {"identifier": resolved}),
        )
        return {
            "device": detail["device"],
            "health": health,
            "software": software,
            "patches": patches,
            "history": history,
            "incidents": incidents_,
            "asset": asset.get("asset"),
        }

    @app.post("/api/devices/{device_id}/actions", status_code=201)
    async def manual_action(
        device_id: str,
        body: ManualAction,
        persona: Persona = Depends(current_persona),
    ) -> dict[str, Any]:
        arguments = {"device_id": device_id.upper(), **body.arguments}
        try:
            return console.manual.raise_proposal(
                body.action, arguments, body.rationale, raised_by=persona.name
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- audit and live stream ------------------------------------------------

    @app.get("/api/audit")
    async def audit(
        investigation_id: int | None = None, since_id: int = 0, limit: int = 300
    ) -> dict[str, Any]:
        with session_scope() as db:
            query = db.query(AuditEvent).filter(AuditEvent.id > since_id)
            if investigation_id is not None:
                query = query.filter(AuditEvent.investigation_id == investigation_id)
            rows = query.order_by(AuditEvent.id).limit(limit).all()
            return {"count": len(rows), "events": [e.as_dict() for e in rows]}

    @app.get("/api/stream")
    async def stream(request: Request, since_id: int = 0) -> StreamingResponse:
        """Server-sent events, tailing the audit trail.

        The console watches exactly what an auditor reads later, so the live
        view and the record cannot disagree.
        """

        async def events() -> AsyncIterator[str]:
            cursor = since_id
            if cursor == 0:
                with session_scope() as db:
                    latest = db.query(AuditEvent).order_by(AuditEvent.id.desc()).first()
                    cursor = latest.id if latest else 0
            yield f"event: ready\ndata: {json.dumps({'since_id': cursor})}\n\n"
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                with session_scope() as db:
                    rows = (
                        db.query(AuditEvent)
                        .filter(AuditEvent.id > cursor)
                        .order_by(AuditEvent.id)
                        .limit(100)
                        .all()
                    )
                    payloads = [e.as_dict() for e in rows]
                if payloads:
                    cursor = payloads[-1]["id"]
                    idle = 0
                    for payload in payloads:
                        yield f"event: audit\ndata: {json.dumps(payload)}\n\n"
                else:
                    idle += 1
                    if idle % 20 == 0:  # roughly every 10 seconds
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/demo/reset")
    async def reset_demo() -> dict[str, Any]:
        """Rebuild the synthetic enterprise and clear Aegis's own state."""
        from aegis_demo.common import db as demo_db
        from aegis_demo.common import seed

        conn = demo_db.connect()
        try:
            counts = seed.build(conn)
        finally:
            conn.close()
        with session_scope() as db:
            for table in (
                "verifications",
                "proposals",
                "investigations",
                "evidence",
                "audit_events",
                "gateway_sessions",
            ):
                db.execute(__import__("sqlalchemy").text(f"DELETE FROM {table}"))
        console.runs.clear()
        return {"reset": True, "seeded": counts}


app = create_app()
