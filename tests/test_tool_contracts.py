"""Every tool the model is given states what it does, returns and leaves.

Roadmap 16 (2026-09-07). The three parts are fields on `Tool`, rendered into
the one description the model reads, so a tool cannot state one and leave
the others to be guessed. This is the offline test that refuses a wired tool
without all three; a test's own throwaway `Tool(...)` may still leave them
empty, which is why the check is here and not in the constructor.
"""

from __future__ import annotations

from pathlib import Path

from app.memory import SqliteStore
from app.tools import (
    CapabilityRegistry,
    Tool,
    goal_tools,
    history_tools,
    memory_tools,
    todo_tools,
)


def every_wired_tool(workspace: Path) -> list[Tool]:
    registry = CapabilityRegistry(workspace)
    with SqliteStore() as store:
        return [
            *(registry.toolbox(registry.grant()).get(name) for name in registry.toolbox(registry.grant()).names),
            *memory_tools(store, "someone", "thread", 5),
            *history_tools(store, "someone", "thread"),
            *todo_tools(),
            *goal_tools(),
        ]


def test_every_wired_tool_states_all_three_parts(tmp_path: Path) -> None:
    for tool in every_wired_tool(tmp_path):
        assert tool.description.strip(), f"{tool.name} does not say what it does"
        assert tool.returns.strip(), f"{tool.name} does not say what it returns"
        assert tool.leaves.strip(), f"{tool.name} does not say what it leaves"


def test_the_schema_the_model_reads_carries_the_three_parts(tmp_path: Path) -> None:
    for tool in every_wired_tool(tmp_path):
        described = tool.schema()["function"]["description"]
        assert described.startswith(tool.description.strip())
        assert "\nReturns: " in described, tool.name
        assert "\nLeaves: " in described, tool.name


def test_a_file_tool_says_what_shell_command_it_replaces(tmp_path: Path) -> None:
    """Hermes's rule, taken: the tool that overlaps with the shell says so,
    and the shell says the reverse, so the model does not pick by habit."""

    by_name = {tool.name: tool for tool in every_wired_tool(tmp_path)}

    assert "instead of cat" in by_name["read_file"].description
    assert "instead of sed" in by_name["edit_file"].description
    assert "instead of ls" in by_name["list_files"].description
    assert "instead of echo" in by_name["write_file"].description
    assert "not cat, echo, sed or ls" in by_name["run_command"].description


def test_the_page_tool_teaches_its_use_through_what_the_model_knows(tmp_path: Path) -> None:
    """The human's condition on the one-tool shape (2026-09-07)."""

    page = {tool.name: tool for tool in every_wired_tool(tmp_path)}["use_page"]
    described = page.schema()["function"]["description"]

    assert "Playwright or Puppeteer" in described
    assert "Call this first" in described
    assert "the person has not" in page.leaves or "sends nothing to the person" in page.leaves
    for action in ("open", "click", "type", "press", "select", "evaluate", "screenshot", "console"):
        assert action in page.parameters["properties"]["action"]["enum"]
