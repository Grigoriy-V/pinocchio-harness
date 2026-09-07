"""Capability grants decide which model tools exist and where they may act."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import ToolCall
from app.tools import (
    BROWSER_INSPECT,
    FILESYSTEM_READ,
    FILESYSTEM_WRITE,
    CapabilityGrant,
    CapabilityRegistry,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "task").mkdir()
    return root


def test_read_only_grant_exposes_only_read_tools(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    toolbox = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)))

    assert toolbox.names == ("list_files", "read_file")


def test_full_grant_exposes_general_browser_and_filesystem_tools(workspace: Path) -> None:
    """`search_web` is absent here because no provider key is configured.

    That is the wiring being honest rather than an omission: the offline suite
    runs with the search provider unset, so the assistant holds the two web tools
    that need nobody's account and not the one that does.
    """

    registry = CapabilityRegistry(workspace)
    toolbox = registry.toolbox(registry.grant())

    assert toolbox.names == (
        "list_files",
        "read_file",
        "write_file",
        "edit_file",
        "use_page",
        "read_document",
        "view_pages",
        "send_file",
        "fetch_page",
        "view_web_page",
        "run_command",
    )


def test_relative_subdirectory_becomes_the_tool_root(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    grant = registry.grant("task", (FILESYSTEM_WRITE,))

    registry.toolbox(grant).run(
        ToolCall("write", "write_file", {"path": "result.txt", "content": "inside"})
    )

    assert (workspace / "task" / "result.txt").read_text(encoding="utf-8") == "inside"
    assert not (workspace / "result.txt").exists()


def test_forged_or_requested_root_outside_workspace_is_refused(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    outside = workspace.parent

    with pytest.raises(PermissionError, match="outside"):
        registry.grant(outside, (FILESYSTEM_READ,))
    with pytest.raises(PermissionError, match="outside"):
        registry.toolbox(CapabilityGrant(outside, (FILESYSTEM_READ,)))


def test_unknown_capability_is_refused(workspace: Path) -> None:
    with pytest.raises(ValueError, match="unknown capabilities"):
        CapabilityRegistry(workspace).grant(capabilities=("shell.unrestricted",))


def test_capabilities_can_be_combined_without_exposing_others(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    grant = registry.grant(capabilities=(FILESYSTEM_READ, BROWSER_INSPECT))

    assert registry.toolbox(grant).names == ("list_files", "read_file", "use_page")
    assert not grant.allows(FILESYSTEM_WRITE)
