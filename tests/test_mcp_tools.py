"""MCP servers as the assistant's tools (roadmap 26), with a server in memory.

Offline: the server is a `FastMCP` connected over memory streams on the
sessions' own loop, never a process or a network. What is tested is the
harness's side: the contract a wired tool carries, the allowlist, approval
for what the owner did not mark read-only, the result the model reads, and
a server that cannot be reached leaving the toolbox intact.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import McpServerConfig, McpSettings
from app.models import ToolCall
from app.tools.base import ToolError
from app.tools.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from app.tools.mcp import McpSessions, mcp_tools


def fake_server() -> FastMCP:
    server = FastMCP("clock")

    @server.tool()
    def get_time(timezone: str = "UTC") -> str:
        """The current time in a timezone."""

        return f"12:00 in {timezone}"

    @server.tool()
    def set_alarm(at: str) -> str:
        """Set an alarm."""

        return f"alarm at {at}"

    @server.tool()
    def secret_tool() -> str:
        """Not on the allowlist."""

        return "never"

    @server.tool()
    def broken() -> str:
        """Always fails."""

        raise RuntimeError("the clock is broken")

    return server


def in_memory(server: FastMCP):
    @asynccontextmanager
    async def connect(name: str, config: McpServerConfig, token: str):
        async with create_connected_server_and_client_session(server) as session:
            yield session

    return connect


@pytest.fixture
def settings() -> McpSettings:
    return McpSettings(
        _config_file=None,
        _env_file=None,
        servers={
            "clock": McpServerConfig(
                tools=["get_time", "set_alarm", "broken"], read_only=["get_time", "broken"], timeout=10
            )
        },
    )


@pytest.fixture
def sessions(settings: McpSettings):
    sessions = McpSessions(settings, connect=in_memory(fake_server()))
    yield sessions
    sessions.close()


def test_the_config_section_is_read_from_the_file(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp.servers.time]\ncommand = ["python", "-m", "mcp_server_time"]\ntools = ["get_current_time"]\n'
        'read_only = ["get_current_time"]\n\n[mcp.servers.gh]\ntransport = "http"\nurl = "https://x/mcp/"\n'
        'token = "MCP_GH_TOKEN"\ntools = ["get_issue"]\n',
        encoding="utf-8",
    )
    settings = McpSettings(_config_file=config, _env_file=None)
    assert set(settings.servers) == {"time", "gh"}
    assert settings.servers["time"].transport == "stdio"
    assert settings.servers["gh"].token == "MCP_GH_TOKEN"
    assert settings.servers["gh"].read_only == []


def test_a_server_becomes_a_capability_with_the_contract(tmp_path: Path, sessions: McpSessions) -> None:
    registry = CapabilityRegistry(tmp_path, mcp=sessions)
    assert registry.mcp_names == ("mcp.clock",)
    toolbox = registry.toolbox(registry.grant(capabilities=DEFAULT_CAPABILITIES + registry.mcp_names))
    names = [name for name in toolbox.names if name.startswith("clock_")]
    assert names == ["clock_get_time", "clock_set_alarm", "clock_broken"], "the allowlist, in the server's order"

    get_time = toolbox.get("clock_get_time")
    assert get_time is not None
    assert get_time.description == "The current time in a timezone."
    # FastMCP declares an output schema `{result}` for a scalar return; the
    # contract names what the server declared.
    assert get_time.returns == "from the clock server: result"
    assert get_time.leaves == "nothing"
    assert get_time.requires_approval is False and get_time.replay_safe is True
    assert get_time.parameters["properties"]["timezone"]["type"] == "string"

    set_alarm = toolbox.get("clock_set_alarm")
    assert set_alarm is not None
    assert set_alarm.leaves == "whatever the clock server changes on its side"
    assert set_alarm.requires_approval is True and set_alarm.replay_safe is False
    assert toolbox.requires_approval("clock_set_alarm") and not toolbox.requires_approval("clock_get_time")


async def test_a_call_reaches_the_server_and_the_model_reads_its_text(tmp_path: Path, sessions: McpSessions) -> None:
    tools = {tool.name: tool for tool in mcp_tools(sessions, "clock")}
    assert await tools["clock_get_time"].run(timezone="Europe/Vienna") == "12:00 in Europe/Vienna"
    with pytest.raises(ToolError) as failure:
        await tools["clock_broken"].run()
    assert failure.value.code == "mcp.error"
    assert "broken" in str(failure.value)


def test_a_server_that_cannot_be_reached_leaves_the_toolbox_intact(tmp_path: Path, settings: McpSettings) -> None:
    @asynccontextmanager
    async def refuse(name, config, token):
        raise ConnectionError("no such server")
        yield  # pragma: no cover

    sessions = McpSessions(settings, connect=refuse)
    try:
        registry = CapabilityRegistry(tmp_path, mcp=sessions)
        toolbox = registry.toolbox(registry.grant(capabilities=DEFAULT_CAPABILITIES + registry.mcp_names))
        assert not [name for name in toolbox.names if name.startswith("clock_")]
        assert "read_file" in toolbox.names
    finally:
        sessions.close()


def test_the_toolbox_validates_the_servers_schema_like_its_own(tmp_path: Path, sessions: McpSessions) -> None:
    registry = CapabilityRegistry(tmp_path, mcp=sessions)
    toolbox = registry.toolbox(registry.grant(capabilities=registry.mcp_names))
    assert toolbox.validation_error(ToolCall(id="1", name="clock_set_alarm", arguments={})) is not None
    assert toolbox.validation_error(ToolCall(id="2", name="clock_set_alarm", arguments={"at": "7:00"})) is None
