"""Workspace autonomy and approval for effects beyond the conversation.

Only the model is faked. The checkpoint file, the store and the tools are real,
because the property under test is that a question outlives the process that
asked it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime import Agent
from app.memory import SqliteStore
from app.tools import Tool, Toolbox
from tests.fakes import ScriptedBackend, body, calls, says, user


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    (room / "notes.txt").write_text("the answer is 42", encoding="utf-8")
    return room


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "memory.sqlite3", tmp_path / "checkpoints.sqlite3"


def open_agent(
    paths: tuple[Path, Path],
    workspace: Path,
    backend: ScriptedBackend,
    checkpointed: bool = True,
    consequential: bool = False,
) -> Agent:
    database, checkpoints = paths
    agent = Agent(
        backend,
        SqliteStore(database),
        workspace,
        checkpoints=checkpoints if checkpointed else None,
    )
    if consequential:
        def publish(path: str) -> str:
            (workspace / "published.txt").write_text(path, encoding="utf-8")
            return f"published {path}"

        tool = Tool(
            name="publish_file",
            description="Publish a file outside the conversation.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            run=publish,
            requires_approval=True,
        )
        agent.toolbox = lambda _thread_id: Toolbox([tool])  # type: ignore[assignment]
    return agent


def writes(path: str, content: str) -> ScriptedBackend:
    return ScriptedBackend(calls("write_file", path=path, content=content), says("Done."))


def edits(path: str, old_text: str, new_text: str) -> ScriptedBackend:
    return ScriptedBackend(
        calls("edit_file", path=path, old_text=old_text, new_text=new_text), says("Done.")
    )


def publishes(path: str) -> ScriptedBackend:
    return ScriptedBackend(calls("publish_file", path=path), says("Done."))


# --- autonomous workspace work ----------------------------------------------


async def test_a_workspace_write_runs_without_asking(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(paths, workspace, writes("notes.txt", "43"))

    produced = await agent.answer("t1", user("Change notes.txt to 43."))
    question = await agent.pending("t1")

    assert [message.role for message in produced] == ["assistant", "tool", "assistant"]
    assert question is None
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "43"
    await agent.aclose()


async def test_a_read_is_never_asked_about(paths: tuple[Path, Path], workspace: Path) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), says("42."))
    agent = open_agent(paths, workspace, backend)

    await agent.answer("t1", user("What does notes.txt say?"))

    assert await agent.pending("t1") is None
    await agent.aclose()


async def test_an_invalid_write_is_rejected_before_confirmation(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    backend = ScriptedBackend(
        calls("write_file", content="missing path"), says("I could not write the file.")
    )
    agent = open_agent(paths, workspace, backend)

    produced = await agent.answer("t1", user("Write a file."))

    assert await agent.pending("t1") is None
    assert body(produced[1]) == (
        "error: bad arguments for write_file: missing required argument(s): path. "
        "write_file takes: path (string), content (string)"
    )
    assert not (workspace / "missing path").exists()
    await agent.aclose()


# --- consequential effects --------------------------------------------------


async def test_a_consequential_call_stops_the_turn_and_asks(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(paths, workspace, publishes("notes.txt"), consequential=True)

    produced = await agent.answer("t1", user("Publish notes.txt."))
    question = await agent.pending("t1")

    assert [message.role for message in produced] == ["assistant"]
    assert question == [
        {
            "id": "call_publish_file",
            "name": "publish_file",
            "arguments": {"path": "notes.txt"},
        }
    ]
    assert not (workspace / "published.txt").exists()
    await agent.aclose()


async def test_approving_runs_the_consequential_call_and_finishes_the_turn(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(paths, workspace, publishes("notes.txt"), consequential=True)
    await agent.answer("t1", user("Publish notes.txt."))

    rest = [message async for message in agent.resume("t1", {"call_publish_file": True})]

    assert (workspace / "published.txt").read_text(encoding="utf-8") == "notes.txt"
    assert [message.role for message in rest] == ["tool", "assistant"]
    assert body(rest[0]) == "published notes.txt"
    assert await agent.pending("t1") is None
    await agent.aclose()


async def test_a_workspace_edit_runs_without_approval_and_changes_one_match(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(paths, workspace, edits("notes.txt", "42", "43"))
    await agent.answer("t1", user("Change 42 to 43."))

    assert await agent.pending("t1") is None
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "the answer is 43"
    await agent.aclose()


async def test_declining_leaves_the_file_alone_and_tells_the_model(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    backend = publishes("notes.txt")
    agent = open_agent(paths, workspace, backend, consequential=True)
    await agent.answer("t1", user("Publish notes.txt."))

    rest = [message async for message in agent.resume("t1", {"call_publish_file": False})]

    assert not (workspace / "published.txt").exists()
    assert body(rest[0]).startswith("error: the user declined")
    # The refusal is what the model sees next, so it can say so rather than retry.
    assert "declined" in body(backend.requests[-1][-1])
    await agent.aclose()


async def test_the_whole_turn_is_stored_once_it_finishes(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(paths, workspace, publishes("notes.txt"), consequential=True)
    await agent.answer("t1", user("Publish notes.txt."))

    assert agent.history("t1") == []

    async for _ in agent.resume("t1", {"call_publish_file": True}):
        pass

    assert [message.role for message in agent.history("t1")] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    await agent.aclose()


# --- the question outlives the process ---------------------------------------


async def test_a_pending_question_survives_a_restart(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    first = open_agent(paths, workspace, publishes("notes.txt"), consequential=True)
    await first.answer("t1", user("Publish notes.txt."))
    await first.aclose()

    second = open_agent(
        paths, workspace, ScriptedBackend(says("Done.")), consequential=True
    )
    question = await second.pending("t1")
    rest = [
        message async for message in second.resume("t1", {"call_publish_file": True})
    ]

    assert question is not None and question[0]["name"] == "publish_file"
    assert (workspace / "published.txt").read_text(encoding="utf-8") == "notes.txt"
    assert [message.role for message in rest] == ["tool", "assistant"]
    await second.aclose()


# --- without a checkpointer there is nowhere to wait -------------------------


async def test_without_checkpoints_a_consequential_call_is_refused_rather_than_run(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    agent = open_agent(
        paths,
        workspace,
        publishes("notes.txt"),
        checkpointed=False,
        consequential=True,
    )

    produced = await agent.answer("t1", user("Publish notes.txt."))

    assert not (workspace / "published.txt").exists()
    # The answer is no, said as what it is: nobody declined, there was
    # nowhere to ask (audit 2026-09-14 §2).
    assert body(produced[1]).startswith("error: publish_file needs the person's approval")
    assert "nowhere to ask" in body(produced[1])
    assert "declined" not in body(produced[1])
    await agent.aclose()


# --- one turn does not leak into the next ------------------------------------


async def test_a_second_turn_starts_from_an_empty_state(
    paths: tuple[Path, Path], workspace: Path
) -> None:
    backend = ScriptedBackend(says("One."), says("Two."))
    agent = open_agent(paths, workspace, backend)

    await agent.answer("t1", user("First question."))
    produced = await agent.answer("t1", user("Second question."))

    # Everything earlier reaches the model through the context layers, and the
    # store holds each message once — the checkpointed state carries none of it.
    assert [message.role for message in produced] == ["assistant"]
    assert [body(message) for message in agent.history("t1")] == [
        "First question.",
        "One.",
        "Second question.",
        "Two.",
    ]
    await agent.aclose()
