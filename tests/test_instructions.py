"""One file per person, saying how they want to be worked with.

The properties that matter are what it is *not*: not memory, not a second
store, not policy, and not something that survives being deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime import Agent
from app.context.window import DEFAULT_SYSTEM_PROMPT, build_prelude
from app.instructions import (
    INSTRUCTIONS_FILE,
    MAX_INSTRUCTION_BYTES,
    InstructionsError,
    clear_instructions,
    instruction_message,
    instructions_path,
    read_instructions,
    write_instructions,
)
from app.memory import SqliteStore
from tests.fakes import ScriptedBackend, prompt_text, says, user


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


# --- the file ----------------------------------------------------------------


def test_nothing_written_means_no_overlay(tmp_path) -> None:
    assert read_instructions(tmp_path) == ""
    assert instruction_message(read_instructions(tmp_path)) is None


def test_what_was_written_is_what_is_read(tmp_path) -> None:
    write_instructions(tmp_path, "Отвечай по-русски и коротко.\n")

    assert read_instructions(tmp_path) == "Отвечай по-русски и коротко."


def test_it_is_an_ordinary_file_in_the_workspace(tmp_path) -> None:
    """So `edit_file` and `read_file` reach it without a second mechanism."""

    write_instructions(tmp_path, "Всегда показывай план.")

    path = tmp_path / INSTRUCTIONS_FILE
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "Всегда показывай план."
    assert instructions_path(tmp_path) == path


def test_writing_replaces_rather_than_appends(tmp_path) -> None:
    write_instructions(tmp_path, "первая версия")
    write_instructions(tmp_path, "вторая версия")

    assert read_instructions(tmp_path) == "вторая версия"


def test_empty_instructions_are_refused_rather_than_saved(tmp_path) -> None:
    with pytest.raises(InstructionsError):
        write_instructions(tmp_path, "   \n  ")
    assert read_instructions(tmp_path) == ""


def test_instructions_that_would_cost_every_turn_are_refused(tmp_path) -> None:
    with pytest.raises(InstructionsError):
        write_instructions(tmp_path, "x" * (MAX_INSTRUCTION_BYTES + 1))


def test_a_file_written_around_the_command_is_truncated_visibly(tmp_path) -> None:
    """`edit_file` can make it any size; a silent half is worse than a marked one."""

    (tmp_path / INSTRUCTIONS_FILE).write_text("y" * (MAX_INSTRUCTION_BYTES * 2), encoding="utf-8")

    text = read_instructions(tmp_path)

    assert f"read_file '{INSTRUCTIONS_FILE}' shows it whole" in text
    assert f"{MAX_INSTRUCTION_BYTES} of {MAX_INSTRUCTION_BYTES * 2} bytes" in text
    assert len(text) < MAX_INSTRUCTION_BYTES + 250


def test_clearing_removes_the_overlay(tmp_path) -> None:
    write_instructions(tmp_path, "что-то")

    assert clear_instructions(tmp_path) is True
    assert read_instructions(tmp_path) == ""
    assert clear_instructions(tmp_path) is False


def test_reading_never_raises_on_a_workspace_that_is_not_there(tmp_path) -> None:
    assert read_instructions(tmp_path / "never-created") == ""


# --- the overlay in the prompt ------------------------------------------------


def test_the_overlay_names_its_source_and_its_limits() -> None:
    message = instruction_message("Пиши коротко.")

    assert message is not None
    body = message.content[0].text
    assert INSTRUCTIONS_FILE in body
    assert "Пиши коротко." in body
    assert "cannot widen what" in body


def test_the_overlay_sits_between_the_system_message_and_the_volatile_layers() -> None:
    """Ordered by how rarely a layer changes, so the prefix cache survives it."""

    prelude = build_prelude(
        "earlier they discussed cats",
        instructions="Отвечай по-русски.",
    )

    bodies = [message.content[0].text for message in prelude]
    assert bodies[0] == DEFAULT_SYSTEM_PROMPT
    assert "Отвечай по-русски." in bodies[1]
    assert "earlier they discussed cats" in bodies[2]


def test_a_prelude_without_instructions_is_unchanged() -> None:
    """Wiring the overlay costs nothing to a person who never wrote one."""

    assert len(build_prelude(None)) == 1
    assert len(build_prelude(None, instructions="   ")) == 1


# --- and it reaches the model, without a restart ------------------------------


async def test_an_edit_reaches_the_next_turn_of_the_same_thread(
    tmp_path: Path, workspace: Path
) -> None:
    """The graph is compiled once per thread and kept. If the overlay were part
    of that compiled prompt, an edit would wait for a restart — which in the
    deployed profile happens to be invisible, because every update is a fresh
    worker, and on a personal machine would simply not work."""

    backend = ScriptedBackend(says("first"), says("second"), says("third"))
    agent = Agent(backend, SqliteStore(tmp_path / "m.sqlite3"), workspace)
    try:
        await agent.answer("thread", user("hi"))
        assert "Отвечай по-русски" not in prompt_text(backend.requests[0])

        write_instructions(workspace, "Отвечай по-русски.")
        await agent.answer("thread", user("again"))
        assert "Отвечай по-русски." in prompt_text(backend.requests[1])

        clear_instructions(workspace)
        await agent.answer("thread", user("once more"))
        assert "Отвечай по-русски" not in prompt_text(backend.requests[2])
    finally:
        await agent.aclose()


async def test_the_overlay_is_not_stored_as_conversation(
    tmp_path: Path, workspace: Path
) -> None:
    """It is prompt, not history: nothing about it is written back to the store."""

    write_instructions(workspace, "Пиши списком.")
    backend = ScriptedBackend(says("done"))
    store = SqliteStore(tmp_path / "m.sqlite3")
    agent = Agent(backend, store, workspace)
    try:
        await agent.answer("thread", user("hi"))
        kept = agent.history("thread")
    finally:
        await agent.aclose()

    written = " ".join(
        part.text or "" for message in kept for part in message.content
    )
    assert "Пиши списком." not in written
