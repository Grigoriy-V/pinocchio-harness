"""MCP servers as the assistant's tools (roadmap 26).

A configured server (`[mcp.servers.<name>]` in `config.toml`) becomes a
capability `mcp.<name>`; each tool it offers and the allowlist names becomes
a `Tool` with the contract the model reads: the server's description, what
it returns (its output schema, or its text), what it leaves (nothing when
the owner marked it read-only, else a change on the server's side).

The sessions live in `McpSessions`, owned by the runtime for the life of
the process the way the command runner is: a toolbox is built per thread,
synchronously and more than once, so the connection cannot belong to it.
The sessions run on a loop of their own in a thread, because a stdio server
is a subprocess (asyncio needs the proactor loop for one on Windows, and
the host's loop is whichever the host chose) and because listing the tools
has to be possible from the synchronous toolbox build.

What a server sends — descriptions, results — is untrusted model input, as
every tool result is. A result the assistant has no way to answer (a
server asking the person something mid-call) is a tool error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from app.config import McpServerConfig, McpSettings
from app.tools.base import Tool, ToolError

log = logging.getLogger(__name__)

CAPABILITY_PREFIX = "mcp."
MCP_ERROR = "mcp.error"
MCP_UNREACHABLE = "mcp.unreachable"

# How a session is opened; replaceable so a test connects a server in memory.
Connect = Callable[[str, McpServerConfig, str], Any]


@asynccontextmanager
async def open_session(name: str, config: McpServerConfig, token: str) -> AsyncIterator[Any]:
    """A connected, initialised `ClientSession` for one configured server."""

    from mcp import ClientSession

    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            if not config.command:
                raise ValueError(f"mcp server {name!r} has no command")
            # `python` means this interpreter: the one the `mcp` group was
            # installed into, wherever the process's PATH points.
            command = sys.executable if config.command[0] == "python" else config.command[0]
            params = StdioServerParameters(command=command, args=list(config.command[1:]))
            read, write = await stack.enter_async_context(stdio_client(params))
        else:
            from mcp.client.streamable_http import streamablehttp_client

            headers = {"Authorization": f"Bearer {token}"} if token else None
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(config.url, headers=headers, timeout=config.timeout)
            )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield session


class McpSessions:
    """The open sessions to the configured servers, one loop, one thread."""

    def __init__(self, settings: McpSettings, connect: Connect = open_session) -> None:
        self.settings = settings
        self._connect = connect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._keepers: dict[str, tuple[asyncio.Task[None], asyncio.Future[Any], asyncio.Event]] = {}
        self._sessions: dict[str, Any] = {}
        self._listed: dict[str, list[Any]] = {}
        self._lock = threading.Lock()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.settings.servers)

    def config(self, name: str) -> McpServerConfig:
        return self.settings.servers[name]

    # --- the loop of its own ---------------------------------------------------

    def _loop_running(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def spin() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                self._thread = threading.Thread(target=spin, name="mcp-sessions", daemon=True)
                self._thread.start()
                ready.wait()
                self._loop = loop
            return self._loop

    def _submit(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop_running())

    async def _keep(self, name: str, ready: asyncio.Future[Any], stop: asyncio.Event) -> None:
        """One task per server that enters the session's context, hands the
        session out, and leaves the context itself when told to stop: the
        transports' task groups may only be exited by the task that entered
        them, and a call comes from whichever task the loop is running."""

        try:
            async with self._connect(name, self.config(name), self.settings.token_for(name)) as session:
                self._sessions[name] = session
                ready.set_result(session)
                await stop.wait()
        except BaseException as error:  # noqa: BLE001 - reported to the waiter
            if not ready.done():
                ready.set_exception(error if isinstance(error, Exception) else RuntimeError(str(error)))
        finally:
            self._sessions.pop(name, None)

    async def _session(self, name: str) -> Any:
        if name in self._sessions:
            return self._sessions[name]
        if name not in self._keepers:
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[Any] = loop.create_future()
            stop = asyncio.Event()
            self._keepers[name] = (loop.create_task(self._keep(name, ready, stop)), ready, stop)
        _task, ready, _stop = self._keepers[name]
        try:
            return await ready
        except Exception:
            self._keepers.pop(name, None)
            raise

    async def _list(self, name: str) -> list[Any]:
        session = await self._session(name)
        return list((await session.list_tools()).tools)

    async def _call(self, name: str, tool: str, arguments: dict[str, Any]) -> Any:
        session = await self._session(name)
        return await session.call_tool(tool, arguments)

    async def _close_all(self) -> None:
        for name, (task, _ready, stop) in list(self._keepers.items()):
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=10)
            except Exception:  # noqa: BLE001 - closing is best effort
                log.warning("mcp server %s did not close cleanly", name, exc_info=True)
        self._keepers.clear()
        self._sessions.clear()

    # --- what the tools use --------------------------------------------------------

    def list_tools(self, name: str) -> list[Any]:
        """The server's tools, listed once and kept; blocks the caller briefly."""

        if name not in self._listed:
            self._listed[name] = self._submit(self._list(name)).result(timeout=self.config(name).timeout)
        return self._listed[name]

    async def call(self, name: str, tool: str, arguments: dict[str, Any]) -> Any:
        return await asyncio.wrap_future(self._submit(self._call(name, tool, arguments)))

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_all(), loop).result(timeout=10)
        except Exception:  # noqa: BLE001
            log.warning("mcp sessions did not close cleanly", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None


# --- from a server's tool to the assistant's -------------------------------------


def render_result(result: Any) -> str:
    """What the model reads: the text parts; the structured content when
    there is no text; a line for each part that is not text (first version:
    text only). An error result is a tool error, not a result."""

    texts = [part.text for part in result.content if getattr(part, "text", None)]
    others = [type(part).__name__ for part in result.content if not getattr(part, "text", None)]
    if getattr(result, "isError", False):
        raise ToolError("\n".join(texts) or "the server reported an error", code=MCP_ERROR)
    text = "\n".join(texts)
    if not text and getattr(result, "structuredContent", None) is not None:
        text = json.dumps(result.structuredContent, ensure_ascii=False)
    if others:
        text = (text + "\n" if text else "") + f"({len(others)} non-text part(s) not shown: {', '.join(others)})"
    return text or "(the server returned nothing)"


def contract_of(listed: Any, read_only: bool, server: str) -> tuple[str, str, str]:
    """The three parts the model reads, from what the server declared."""

    description = (listed.description or listed.title or listed.name).strip()
    schema = getattr(listed, "outputSchema", None)
    if schema:
        names = ", ".join((schema.get("properties") or {}).keys()) or "structured content"
        returns = f"from the {server} server: {names}"
    else:
        returns = f"the {server} server's text result"
    leaves = "nothing" if read_only else f"whatever the {server} server changes on its side"
    return description, returns, leaves


def mcp_tools(sessions: McpSessions, name: str) -> list[Tool]:
    """The tools of one configured server, as the toolbox wires them.

    A server that cannot be reached contributes no tools and a warning in
    the log rather than a toolbox that cannot be built: the assistant's
    other capabilities are not hostage to a third party's process.
    """

    config = sessions.config(name)
    try:
        listed = sessions.list_tools(name)
    except Exception:  # noqa: BLE001 - the server's failure, logged, not the turn's
        log.warning("mcp server %s could not be listed; its tools are absent", name, exc_info=True)
        return []
    tools: list[Tool] = []
    for entry in listed:
        if entry.name not in config.tools:
            continue
        read_only = entry.name in config.read_only
        description, returns, leaves = contract_of(entry, read_only, name)

        async def run(_tool: str = entry.name, **arguments: Any) -> str:
            try:
                result = await sessions.call(name, _tool, arguments)
            except ToolError:
                raise
            except Exception as error:  # noqa: BLE001 - the transport's failure, named
                raise ToolError(
                    f"the {name} server did not answer", code=MCP_UNREACHABLE, detail=str(error)
                ) from error
            return render_result(result)

        tools.append(
            Tool(
                name=f"{name}_{entry.name}",
                description=description,
                parameters=dict(entry.inputSchema or {"type": "object", "properties": {}}),
                run=run,
                returns=returns,
                leaves=leaves,
                requires_approval=not read_only,
                timeout_seconds=config.timeout,
                replay_safe=read_only,
                mutates=False,
            )
        )
    return tools
