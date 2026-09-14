"""The folder a conversation works in (roadmap 27, step 2).

On the person's own machine a conversation works in the folder they named,
reading reaches anywhere and a write outside the folder asks; deployed,
nothing here applies and every path stays inside the root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.folder import clear_folder, folder_of, last_folder, set_folder
from app.models import ToolCall
from app.tools import CapabilityRegistry, ToolError, filesystem_tools
from app.tools.capabilities import FILESYSTEM_READ, FILESYSTEM_WRITE
from app.tools.filesystem import outside


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    (path / "README.md").write_text("the project", encoding="utf-8")
    return path


# --- the choice, kept in the personal workspace ------------------------------


def test_a_folder_is_named_per_conversation_and_the_last_one_is_inherited(
    workspace: Path, project: Path
) -> None:
    assert folder_of(workspace, "t1") is None
    assert last_folder(workspace) is None

    chosen = set_folder(workspace, "t1", project)

    assert chosen == project.resolve()
    assert folder_of(workspace, "t1") == project.resolve()
    assert folder_of(workspace, "t2") is None
    assert last_folder(workspace) == project.resolve()
    assert (workspace / ".agent" / "folders.json").is_file(), "beside the other switches"

    clear_folder(workspace, "t1")
    assert folder_of(workspace, "t1") is None
    assert last_folder(workspace) == project.resolve(), "the last choice stands for new ones"


def test_a_folder_must_exist_and_be_absolute(workspace: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        set_folder(workspace, "t1", "project")
    with pytest.raises(ValueError, match="not a folder"):
        set_folder(workspace, "t1", tmp_path / "absent")


def test_a_folder_that_disappeared_is_not_offered(workspace: Path, tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    set_folder(workspace, "t1", gone)
    gone.rmdir()

    assert folder_of(workspace, "t1") is None


# --- the tools: read anywhere, write outside asks, deployed confined ------------


def test_open_tools_read_any_path_and_confined_ones_do_not(
    workspace: Path, project: Path
) -> None:
    open_tools = {tool.name: tool for tool in filesystem_tools(workspace, open_reads=True)}
    confined = {tool.name: tool for tool in filesystem_tools(workspace)}

    assert open_tools["read_file"].run(path=str(project / "README.md")) == "the project"
    assert "README.md" in open_tools["list_files"].run(path=str(project))
    with pytest.raises(ToolError, match="outside the allowed root"):
        confined["read_file"].run(path=str(project / "README.md"))


def test_a_write_outside_the_folder_asks_and_one_inside_does_not(
    workspace: Path, project: Path
) -> None:
    registry = CapabilityRegistry(workspace, open=True)
    toolbox = registry.toolbox(registry.grant(project, (FILESYSTEM_READ, FILESYSTEM_WRITE)))
    elsewhere = str(workspace / "note.txt")

    assert not toolbox.requires_approval("write_file", {"path": "made.txt", "content": "x"})
    assert not toolbox.requires_approval("read_file", {"path": elsewhere})
    assert toolbox.requires_approval("write_file", {"path": elsewhere, "content": "x"})
    assert toolbox.requires_approval("edit_file", {"path": elsewhere, "old_text": "a", "new_text": "b"})

    # Approved, the write lands; nothing about the path is refused.
    toolbox.run(ToolCall("w", "write_file", {"path": elsewhere, "content": "kept"}))
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "kept"


def test_a_confined_registry_still_refuses_a_root_outside_the_workspace(
    workspace: Path, project: Path
) -> None:
    """The deployed profile: several people share one Volume."""

    with pytest.raises(PermissionError, match="outside"):
        CapabilityRegistry(workspace).grant(project, (FILESYSTEM_READ,))
    confined = CapabilityRegistry(workspace).toolbox(CapabilityRegistry(workspace).grant())
    assert not confined.requires_approval("write_file", {"path": str(project / "x"), "content": ""})


def test_outside_is_about_the_resolved_path(project: Path) -> None:
    assert not outside(project, "sub/file.txt")
    assert not outside(project, None)
    assert outside(project, str(project.parent / "other.txt"))
    assert outside(project, "../other.txt")


# --- the command --------------------------------------------------------------


class _Agent:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.rewired = 0

    def rewire(self) -> None:
        self.rewired += 1

    def folder(self, thread_id: str) -> Path:
        return folder_of(self.workspace, thread_id) or self.workspace


def test_the_workspace_command_sets_shows_and_clears(workspace: Path, project: Path) -> None:
    from app.agent.commands import workspace_reply

    agent = _Agent(workspace)
    assert str(workspace) in workspace_reply(agent, "t1", "")
    assert "Not set" in workspace_reply(agent, "t1", "nowhere")
    assert agent.rewired == 0

    said = workspace_reply(agent, "t1", str(project))
    assert str(project.resolve()) in said and "asks you first" in said
    assert agent.rewired == 1 and folder_of(workspace, "t1") == project.resolve()

    assert str(workspace) in workspace_reply(agent, "t1", "off")
    assert folder_of(workspace, "t1") is None and agent.rewired == 2


def test_an_open_document_tool_reads_a_document_anywhere(workspace: Path, tmp_path: Path) -> None:
    """The person's own machine: a document is read by its path, wherever
    it lies (the CV in the session's upload folder, 2026-09-14)."""

    from app.tools.documents import document_tools

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.md").write_text("# Notes\n\nkept elsewhere", encoding="utf-8")
    tools = {tool.name: tool for tool in document_tools(workspace, open_reads=True)}

    assert "kept elsewhere" in tools["read_document"].run(path=str(elsewhere / "notes.md"))
