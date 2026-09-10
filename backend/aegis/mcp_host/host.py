"""The MCP client side of Aegis.

Aegis connects to each enterprise system as an MCP client, discovers what tools
it offers, and namespaces them. Nothing here knows about policy or auditing;
this layer's only job is to reach the upstream systems and report faithfully
what they said.

An upstream that is unreachable at startup does not stop Aegis. It is recorded
as unavailable and the investigation proceeds with the systems that answered,
which is what a real integration layer has to do.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Self

from mcp import Client

from .upstreams import UpstreamConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredTool:
    """A tool offered by an upstream system, as Aegis will re-publish it."""

    upstream: str
    prefix: str
    remote_name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] | None = None
    title: str | None = None
    capability: str = ""

    @property
    def declares_read_only(self) -> bool:
        return bool(self.annotations and self.annotations.get("read_only_hint") is True)


@dataclass
class UpstreamStatus:
    name: str
    url: str
    connected: bool
    tool_count: int = 0
    error: str | None = None
    connected_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "connected": self.connected,
            "tool_count": self.tool_count,
            "error": self.error,
        }


@dataclass
class ToolCallOutcome:
    """What an upstream returned, plus how long it took."""

    ok: bool
    structured: dict[str, Any] | None
    text: str | None
    latency_ms: float
    error: str | None = None


class MCPHost:
    """Holds a live MCP session to each configured enterprise system."""

    def __init__(self, upstreams: list[UpstreamConfig], *, timeout: float = 20.0) -> None:
        self._configs = [u for u in upstreams if u.enabled]
        self._timeout = timeout
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, Client] = {}
        self._tools: dict[str, DiscoveredTool] = {}
        self._status: dict[str, UpstreamStatus] = {}
        # Upstream sessions are owned by one long-lived task. An MCP client
        # opens anyio cancel scopes, and those must be entered and exited in the
        # same task, so the sessions cannot be tied to whichever caller happened
        # to open them. Calls are made from any task; only the lifecycle is pinned.
        self._runner: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._startup_error: BaseException | None = None

    # -- lifecycle ------------------------------------------------------------

    async def connect(self) -> list[UpstreamStatus]:
        """Open a session to every enabled upstream and discover its tools."""
        if self._runner is not None:
            return list(self._status.values())
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._startup_error = None
        self._runner = asyncio.create_task(self._session_owner(), name="aegis-mcp-host")
        await self._ready.wait()
        if self._startup_error is not None:
            await self.close()
            raise self._startup_error
        return list(self._status.values())

    async def _session_owner(self) -> None:
        """Hold every upstream session open until close() is called."""
        try:
            async with AsyncExitStack() as stack:
                self._stack = stack
                for config in self._configs:
                    await self._connect_one(config)
                self._ready.set()
                await self._shutdown.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to connect()
            self._startup_error = exc
        finally:
            self._stack = None
            self._clients.clear()
            self._ready.set()

    async def _connect_one(self, config: UpstreamConfig) -> UpstreamStatus:
        assert self._stack is not None
        try:
            # No asyncio.wait_for here. It runs the awaitable in a separate task,
            # which would enter the client's cancel scope in that task and make it
            # impossible to close from this one. The SDK's own read timeout covers
            # a slow upstream.
            client = await self._stack.enter_async_context(
                Client(config.url, read_timeout_seconds=self._timeout)
            )
            listed = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 - an upstream may fail in many ways
            logger.warning("Upstream %s unavailable at %s: %s", config.name, config.url, exc)
            status = UpstreamStatus(
                name=config.name,
                url=config.url,
                connected=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._status[config.name] = status
            return status

        self._clients[config.name] = client
        discovered = 0
        for tool in listed.tools:
            qualified = config.qualify(tool.name)
            if qualified in self._tools:
                existing = self._tools[qualified].upstream
                raise ValueError(
                    f"Tool name collision: {qualified!r} is offered by both {existing!r} "
                    f"and {config.name!r}. Give one upstream a different prefix."
                )
            annotations = (
                tool.annotations.model_dump(exclude_none=True) if tool.annotations else None
            )
            self._tools[qualified] = DiscoveredTool(
                upstream=config.name,
                prefix=config.prefix,
                remote_name=tool.name,
                qualified_name=qualified,
                description=tool.description or "",
                input_schema=tool.input_schema or {"type": "object", "properties": {}},
                annotations=annotations,
                title=tool.title,
                capability=config.capability,
            )
            discovered += 1

        status = UpstreamStatus(
            name=config.name,
            url=config.url,
            connected=True,
            tool_count=discovered,
            connected_at=time.time(),
        )
        self._status[config.name] = status
        logger.info("Connected to %s (%d tools)", config.name, discovered)
        return status

    async def close(self) -> None:
        if self._runner is None:
            return
        self._shutdown.set()
        try:
            await self._runner
        finally:
            self._runner = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- discovery ------------------------------------------------------------

    @property
    def tools(self) -> dict[str, DiscoveredTool]:
        return dict(self._tools)

    @property
    def status(self) -> list[UpstreamStatus]:
        return list(self._status.values())

    @property
    def connected_upstreams(self) -> list[str]:
        return [s.name for s in self._status.values() if s.connected]

    @property
    def unavailable_upstreams(self) -> list[str]:
        return [s.name for s in self._status.values() if not s.connected]

    def get(self, qualified_name: str) -> DiscoveredTool | None:
        return self._tools.get(qualified_name)

    # -- invocation -----------------------------------------------------------

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Invoke an upstream tool. Never raises for an upstream-side failure."""
        tool = self._tools.get(qualified_name)
        if tool is None:
            return ToolCallOutcome(
                ok=False,
                structured=None,
                text=None,
                latency_ms=0.0,
                error=f"No upstream offers a tool named {qualified_name!r}",
            )

        client = self._clients.get(tool.upstream)
        if client is None:
            return ToolCallOutcome(
                ok=False,
                structured=None,
                text=None,
                latency_ms=0.0,
                error=f"Upstream {tool.upstream!r} is not connected",
            )

        started = time.perf_counter()
        try:
            result = await client.call_tool(
                tool.remote_name, arguments, read_timeout_seconds=self._timeout
            )
        except TimeoutError:
            elapsed = (time.perf_counter() - started) * 1000
            return ToolCallOutcome(
                ok=False,
                structured=None,
                text=None,
                latency_ms=elapsed,
                error=f"Upstream {tool.upstream!r} did not respond within {self._timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            return ToolCallOutcome(
                ok=False,
                structured=None,
                text=None,
                latency_ms=elapsed,
                error=f"{type(exc).__name__}: {exc}",
            )

        elapsed = (time.perf_counter() - started) * 1000
        text = None
        if result.content:
            parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
            text = "\n".join(parts) if parts else None

        return ToolCallOutcome(
            ok=not result.is_error,
            structured=result.structured_content,
            text=text,
            latency_ms=round(elapsed, 1),
            error=text if result.is_error else None,
        )
