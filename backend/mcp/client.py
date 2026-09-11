"""MCP client manager.

THE PROBLEM THIS SOLVES
-----------------------
``mcp.client.stdio.stdio_client`` and ``ClientSession`` are async context
managers implemented with anyio task groups. anyio cancel scopes are bound to
the task that entered them, so the obvious implementation --

    stack = AsyncExitStack()                      # in FastAPI lifespan
    session = await stack.enter_async_context(...)  # entered in task A
    ...
    await stack.aclose()                          # exited in task B  <-- boom

-- raises ``RuntimeError: Attempted to exit cancel scope in a different task``
the moment the lifespan's shutdown runs on a different task than startup, or
when a request task is cancelled mid-call. It is the single most common way
MCP integrations break in a web server, and it fails *intermittently*, which
is worse than failing always.

THE SOLUTION
------------
Each server gets a dedicated **supervisor task** that owns the entire session
lifecycle from open to close. Callers never touch the session; they push a
request onto an ``asyncio.Queue`` and await a future that the supervisor
resolves. Enter and exit therefore always happen in the same task, and a
cancelled HTTP request cannot tear down the transport.

This also buys three things for free:

* **Serialised access.** One in-flight request per session, which is what the
  stdio framing wants anyway.
* **Crash isolation.** A server that dies is marked down and restarted on the
  next call; the rest of the system keeps working.
* **Clean instrumentation.** Every call passes one chokepoint, so metrics and
  spans are impossible to forget.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import settings
from ..observability import MCP_SERVERS_UP, record_mcp_call, span
from .registry import MCPServerSpec, enabled_specs

logger = logging.getLogger(__name__)


class ServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"


class MCPError(RuntimeError):
    """An MCP call failed. Carries the server and tool for context."""

    def __init__(self, message: str, server: str = "", tool: str = "") -> None:
        super().__init__(message)
        self.server = server
        self.tool = tool


@dataclass
class _Request:
    tool: str
    arguments: dict[str, Any]
    future: asyncio.Future


_SHUTDOWN = _Request(tool="__shutdown__", arguments={}, future=None)  # type: ignore[arg-type]


def _unwrap(result: Any) -> Any:
    """Turn an MCP ``CallToolResult`` into plain Python.

    FastMCP serialises a tool's dict return value as JSON inside a text
    content block; newer SDK versions additionally provide ``structuredContent``.
    We prefer the structured field when present and fall back to parsing the
    text, so this works across SDK versions.
    """
    if result is None:
        return None

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps non-dict returns under a "result" key.
        if set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured

    chunks: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)

    if not chunks:
        return None

    joined = "\n".join(chunks).strip()
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


class MCPConnection:
    """A supervised session to one MCP server."""

    def __init__(self, spec: MCPServerSpec) -> None:
        self.spec = spec
        self.state = ServerState.STOPPED
        self.error: str | None = None
        self.tools: list[str] = []
        self._queue: asyncio.Queue[_Request] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._start_lock = asyncio.Lock()

    # ---------------- lifecycle ----------------

    async def start(self) -> bool:
        """Launch the supervisor and wait for the session to initialise."""
        async with self._start_lock:
            if self.state == ServerState.READY and self._task and not self._task.done():
                return True

            self._ready.clear()
            self.state = ServerState.STARTING
            self.error = None
            self._task = asyncio.create_task(
                self._supervise(), name=f"mcp-{self.spec.name}"
            )

            try:
                await asyncio.wait_for(
                    self._ready.wait(), timeout=settings.mcp_startup_timeout
                )
            except TimeoutError:
                self.state = ServerState.FAILED
                self.error = (
                    f"Server did not initialise within {settings.mcp_startup_timeout}s."
                )
                logger.error("MCP server %s startup timed out.", self.spec.name)
                await self.stop()
                return False

            return self.state == ServerState.READY

    async def stop(self) -> None:
        """Signal the supervisor to close the session and wait for it."""
        if self._task is None:
            return
        with contextlib.suppress(Exception):
            self._queue.put_nowait(_SHUTDOWN)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("MCP %s shutdown raised: %s", self.spec.name, exc)
        finally:
            self._task = None
            self.state = ServerState.STOPPED
            MCP_SERVERS_UP.labels(server=self.spec.name).set(0)

    async def _supervise(self) -> None:
        """Own the session from open to close. Runs as its own task."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover
            self.state = ServerState.FAILED
            self.error = f"MCP SDK not installed: {exc}"
            self._ready.set()
            return

        executable, args = self.spec.command()
        params = StdioServerParameters(
            command=executable,
            args=args,
            env=settings.mcp_server_env(),
            cwd=str(self.spec.script_path.parent.parent),
        )

        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    try:
                        listing = await session.list_tools()
                        self.tools = [t.name for t in listing.tools]
                    except Exception as exc:  # pragma: no cover
                        logger.debug("list_tools failed for %s: %s", self.spec.name, exc)
                        self.tools = list(self.spec.tools)

                    self.state = ServerState.READY
                    MCP_SERVERS_UP.labels(server=self.spec.name).set(1)
                    logger.info(
                        "MCP server '%s' ready with %d tools: %s",
                        self.spec.name, len(self.tools), ", ".join(self.tools),
                    )
                    self._ready.set()

                    await self._serve(session)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = ServerState.FAILED
            self.error = str(exc)
            logger.error("MCP server '%s' failed: %s", self.spec.name, exc)
        finally:
            MCP_SERVERS_UP.labels(server=self.spec.name).set(0)
            if self.state != ServerState.READY:
                self._ready.set()  # Unblock start() on failure.
            self._drain_pending()

    async def _serve(self, session: Any) -> None:
        """Process queued tool calls until shutdown."""
        while True:
            request = await self._queue.get()
            if request is _SHUTDOWN or request.tool == "__shutdown__":
                logger.debug("MCP server '%s' shutting down.", self.spec.name)
                return

            if request.future.done():  # Caller gave up (timeout/disconnect).
                continue

            try:
                result = await asyncio.wait_for(
                    session.call_tool(request.tool, request.arguments),
                    timeout=settings.mcp_tool_timeout,
                )
                if getattr(result, "isError", False):
                    payload = _unwrap(result)
                    raise MCPError(
                        f"Tool '{request.tool}' reported an error: {payload}",
                        server=self.spec.name,
                        tool=request.tool,
                    )
                if not request.future.done():
                    request.future.set_result(_unwrap(result))
            except asyncio.CancelledError:
                if not request.future.done():
                    request.future.set_exception(
                        MCPError("Session cancelled.", self.spec.name, request.tool)
                    )
                raise
            except TimeoutError:
                if not request.future.done():
                    request.future.set_exception(
                        MCPError(
                            f"Tool '{request.tool}' timed out after "
                            f"{settings.mcp_tool_timeout}s.",
                            self.spec.name, request.tool,
                        )
                    )
            except Exception as exc:
                if not request.future.done():
                    request.future.set_exception(
                        MCPError(str(exc), self.spec.name, request.tool)
                    )

    def _drain_pending(self) -> None:
        """Fail every queued request when the session goes away."""
        while not self._queue.empty():
            try:
                request = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if request.future is not None and not request.future.done():
                request.future.set_exception(
                    MCPError(
                        f"MCP server '{self.spec.name}' is unavailable: "
                        f"{self.error or 'session closed'}",
                        self.spec.name, request.tool,
                    )
                )

    # ---------------- calling ----------------

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on this server."""
        if self.state != ServerState.READY:
            started = await self.start()
            if not started:
                raise MCPError(
                    f"MCP server '{self.spec.name}' is not available: "
                    f"{self.error or 'unknown error'}",
                    self.spec.name, tool,
                )

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Request(tool=tool, arguments=arguments, future=future))
        # Allow generous headroom over the per-tool timeout so a queued call
        # behind a slow one is not killed prematurely.
        return await asyncio.wait_for(future, timeout=settings.mcp_tool_timeout + 30)


class MCPManager:
    """Owns every MCP connection and exposes a single ``call`` entry point."""

    def __init__(self) -> None:
        self.connections: dict[str, MCPConnection] = {}
        self._started = False

    async def startup(self) -> dict[str, str]:
        """Start all enabled servers concurrently. Returns name -> state."""
        if self._started:
            return self.status()

        specs = enabled_specs()
        self.connections = {spec.name: MCPConnection(spec) for spec in specs}

        results = await asyncio.gather(
            *(conn.start() for conn in self.connections.values()),
            return_exceptions=True,
        )
        for (name, conn), outcome in zip(self.connections.items(), results, strict=False):
            if isinstance(outcome, Exception):
                conn.state = ServerState.FAILED
                conn.error = str(outcome)
                logger.error("MCP server '%s' raised during startup: %s", name, outcome)

        self._started = True
        ready = [n for n, c in self.connections.items() if c.state == ServerState.READY]
        failed = [n for n, c in self.connections.items() if c.state != ServerState.READY]
        logger.info(
            "MCP startup complete. Ready: %s%s",
            ", ".join(ready) or "none",
            f" | Failed: {', '.join(failed)}" if failed else "",
        )
        return self.status()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(conn.stop() for conn in self.connections.values()), return_exceptions=True
        )
        self.connections.clear()
        self._started = False

    def status(self) -> dict[str, str]:
        return {name: conn.state.value for name, conn in self.connections.items()}

    def is_ready(self, server: str) -> bool:
        conn = self.connections.get(server)
        return conn is not None and conn.state == ServerState.READY

    def available_tools(self) -> dict[str, list[str]]:
        return {
            name: list(conn.tools)
            for name, conn in self.connections.items()
            if conn.state == ServerState.READY
        }

    async def call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        default: Any = None,
        raise_on_error: bool = False,
    ) -> Any:
        """Call an MCP tool with tracing, metrics and a safe default.

        Args:
            server: Registry name of the server.
            tool: Tool name.
            arguments: Tool arguments.
            default: Returned when the call fails and ``raise_on_error`` is
                false. This is the mechanism that lets an agent lose one data
                source and still produce a plan.
            raise_on_error: Raise :class:`MCPError` instead of returning
                ``default``. Used where the data is genuinely load-bearing.
        """
        arguments = arguments or {}
        conn = self.connections.get(server)

        if conn is None:
            message = f"MCP server '{server}' is not enabled."
            if raise_on_error:
                raise MCPError(message, server, tool)
            logger.warning(message)
            return default

        started = time.perf_counter()
        with span(
            f"mcp.{server}.{tool}",
            **{"mcp.server": server, "mcp.tool": tool,
               "mcp.arg_keys": ",".join(sorted(arguments.keys()))},
        ) as current:
            try:
                result = await conn.call(tool, arguments)
                elapsed = time.perf_counter() - started
                record_mcp_call(server, tool, elapsed, True)
                current.set_attribute("mcp.ok", True)
                current.set_attribute("mcp.duration_ms", round(elapsed * 1000, 2))
                return result
            except (TimeoutError, MCPError, Exception) as exc:
                elapsed = time.perf_counter() - started
                record_mcp_call(server, tool, elapsed, False)
                current.set_attribute("mcp.ok", False)
                current.set_attribute("mcp.error", str(exc)[:300])
                logger.warning("MCP %s.%s failed after %.2fs: %s",
                               server, tool, elapsed, exc)
                if raise_on_error:
                    raise MCPError(str(exc), server, tool) from exc
                return default


# Process-wide manager, started and stopped by the FastAPI lifespan.
mcp_manager = MCPManager()
