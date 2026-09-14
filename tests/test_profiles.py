"""The two profiles stay two (the human, 2026-09-14).

Since roadmap 27 the local app opens things the deployed profile must keep
shut: any folder as a root, reading anywhere, a write outside the folder
after a yes, localhost in the browser, a command kept running. Every one of
them hangs on one flag, `open`, that only the local app passes. This file
is the check that the deployed wiring is what it was before, so a return to
the deploy does not find a harness rebuilt for one machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime import create_agent
from app.capabilities import Delivery
from app.config import AgentSettings
from app.tools import CapabilityRegistry, Finished
from app.tools.capabilities import FILESYSTEM_READ


class RemoteRunner:
    """What the deploy hands the agent (`ModalRunner`): a command run to its
    end elsewhere; nothing here can keep a process."""

    where = "in a Function"

    async def run(self, command: str, cwd: Path, timeout: float) -> Finished:
        return Finished(0, "", False, 0.0)


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    (tmp_path / "workspace").mkdir()
    return AgentSettings(
        database=str(tmp_path / "memory.sqlite3"),
        database_url="",
        checkpoints=str(tmp_path / "checkpoints.sqlite3"),
        telemetry=False,
        workspace=str(tmp_path / "workspace"),
    )


@pytest.mark.asyncio
async def test_the_deployed_wiring_is_confined(settings: AgentSettings, tmp_path: Path) -> None:
    """`create_agent` without `open`, as the deploy, Telegram and the
    scripts call it: the root is the person's workspace and nothing outside
    it is read, written or granted; no background command is offered."""

    agent = create_agent(
        agent_settings=settings, delivery=Delivery(media=("image",)), user_id="u1", runner=RemoteRunner()
    )
    try:
        registry = agent.capability_registry
        assert registry.open is False
        assert registry.pages.open_addresses is False
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "secret.txt").write_text("no", encoding="utf-8")
        with pytest.raises(PermissionError, match="outside"):
            registry.grant(elsewhere, (FILESYSTEM_READ,))

        toolbox = agent.toolbox("t1")
        assert "command_output" not in toolbox.names and "stop_command" not in toolbox.names
        assert toolbox.get("read_file").asks is None
        assert toolbox.get("write_file").asks is None, "a write outside the root is refused, never asked"
        assert "background" not in str(toolbox.schemas())
    finally:
        await agent.aclose()


def test_only_an_open_registry_opens_anything(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shut = CapabilityRegistry(workspace)
    opened = CapabilityRegistry(workspace, open=True)

    assert (shut.open, shut.pages.open_addresses) == (False, False)
    assert (opened.open, opened.pages.open_addresses) == (True, True)
