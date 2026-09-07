"""The goal: the request's parts, written once by the model (2026-09-05).

One call, one line per thing asked, never updated. Offered always; the brief
says why; nothing in the loop reads it back. No model, no network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.capabilities import capability_brief
from app.tools.capabilities import CapabilityRegistry
from app.models import ToolCall
from app.tools import ToolExecutor, Toolbox, goal_tools
from app.tools.base import ToolError
from app.tools.goal import DESCRIPTION, MAX_PARTS, TOOL_NAME, normalise


def _run(**arguments):
    executor = ToolExecutor(Toolbox(goal_tools()))
    prepared = executor.pre_execute(ToolCall(id="g1", name=TOOL_NAME, arguments=arguments))
    return asyncio.run(executor.execute(prepared))


def test_a_goal_is_noted_and_counted_back() -> None:
    outcome = _run(parts=["a PDF about Japan, in Russian", "send it to me"])

    assert outcome.failure is None
    assert "2 thing(s)" in outcome.content[0].text
    assert "not updated" in outcome.content[0].text


def test_lines_are_recorded_as_the_model_will_read_them_back() -> None:
    assert normalise(["  first ", "second"]) == ["first", "second"]


@pytest.mark.parametrize(
    "parts",
    [
        [],
        "one string",
        [""],
        ["same", "same"],
        ["x" * 201],
        ["p"] * (MAX_PARTS + 1),
    ],
)
def test_a_goal_that_cannot_be_recorded_honestly_is_refused(parts) -> None:
    with pytest.raises(ToolError):
        normalise(parts)


def test_the_refusal_reaches_the_model_as_a_tool_error() -> None:
    outcome = _run(parts=[])

    assert outcome.failure is not None
    assert outcome.failure.code == "goal.invalid"


def test_the_description_says_once_and_never_updated() -> None:
    assert "once" in DESCRIPTION
    assert "do not update" in DESCRIPTION
    assert "more than one thing" in DESCRIPTION


def test_the_brief_says_why_only_when_the_tool_is_there(tmp_path: Path) -> None:
    registry = CapabilityRegistry(tmp_path)
    grant = registry.grant(capabilities=())

    with_goal = capability_brief(registry.toolbox(grant, goal_tools()))
    without = capability_brief(registry.toolbox(grant, []))

    assert "set_goal" in with_goal and "more than one thing" in with_goal
    assert "set_goal" not in without


def test_the_product_offers_the_goal_by_default(tmp_path: Path, monkeypatch) -> None:
    from app.agent.runtime import create_agent
    from app.config import AgentSettings, ModelSettings

    monkeypatch.setenv("AGENT_DATABASE_URL", "")
    settings = AgentSettings(
        workspace=str(tmp_path / "ws"),
        database=str(tmp_path / "c.db"),
        checkpoints=str(tmp_path / "ck.sqlite3"),
    )
    agent = create_agent(ModelSettings(), settings)
    try:
        names = agent.toolbox("t").names
    finally:
        asyncio.run(agent.aclose())

    assert TOOL_NAME in names
    assert "todo_write" not in names  # the plan stays behind /plan
