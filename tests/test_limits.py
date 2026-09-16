"""Roadmap 31: a limit is derived, not written, and every cut has a way back.

`reports/2026-09-16_item31_references.md` §4: the whole-request limits are
shares of the budget, one tool's page is a setting, a cut names an offset, a
file or a locator, and a timeout above the most is never clamped in silence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.context.summary import verbatim_floor
from app.context.window import Context, dropped
from app.documents import read_sections
from app.instructions import (
    INSTRUCTIONS_FILE,
    InstructionsError,
    read_instructions,
    write_instructions,
)
from app.limits import DEFAULT_LIMITS, Limits
from app.models import ContentPart, Message, ToolCall
from app.tools import Tool, Toolbox, ToolExecutor
from app.tools.execution import Spill
from app.tools.filesystem import filesystem_tools
from app.tools.shell import Finished, LocalRunner, describe, shell_tools
from app.web import Rendered
from tests.fakes import ScriptedBackend


def text(value: str) -> list[ContentPart]:
    return [ContentPart(kind="text", text=value)]


# --- the shares ----------------------------------------------------------------


def test_the_whole_request_limits_grow_with_the_budget_and_the_pages_do_not() -> None:
    small = Limits(budget=131_072, chars_per_token=3.0)
    large = Limits(budget=524_288, chars_per_token=3.0)

    for derived in ("result_chars", "page_chars", "keep_recent_tokens", "stub_min_chars", "instruction_bytes"):
        assert getattr(large, derived) > getattr(small, derived), derived
    assert large.summary_tokens == large.summary_ceiling_tokens  # the ceiling holds
    assert small.summary_tokens == int(131_072 * 0.05)
    for setting in ("read_lines", "line_chars", "search_page", "shell_output_chars", "web_text_chars", "command_timeout_max"):
        assert getattr(large, setting) == getattr(small, setting), setting


def test_the_result_cap_is_one_eighth_of_the_budget_in_the_models_characters() -> None:
    limits = Limits(budget=256_000, chars_per_token=4.0)

    assert limits.result_chars == 256_000 // 8 * 4
    assert limits.keep_recent_tokens == int(256_000 * 0.15)
    assert limits.instruction_bytes == min(32_768, int(256_000 * 0.02 * 4))


def test_the_settings_fill_the_limits_and_the_model_says_its_media() -> None:
    class Agent:
        read_lines = 1234
        shell_output_chars = 5000
        result_share = 0.25

    class Model:
        max_images = 20
        max_audio = 2
        max_tokens = 4096

    limits = Limits.from_settings(Agent(), Model())

    assert limits.read_lines == 1234 and limits.shell_output_chars == 5000
    assert limits.result_share == 0.25
    assert limits.media_budget == {"image": 20, "audio": 2}
    assert limits.output_tokens == 4096
    assert limits.with_budget(1_000, 2.0).result_chars == int(1_000 * 0.25 * 2.0)


# --- the executor's cap and the spill file ----------------------------------------


async def test_a_result_past_the_cap_is_kept_whole_in_a_file_the_result_names(tmp_path: Path) -> None:
    limits = Limits(budget=4_000, chars_per_token=1.0)  # result_chars = 500
    big = "x" * 100 + "y" * 2_000 + "END"
    box = Toolbox([Tool(name="big", description="", parameters={"type": "object", "properties": {}}, run=lambda: big)])
    executor = ToolExecutor(box, limits=limits, spill=Spill(tmp_path / ".agent" / "results"))

    message = await executor.call(ToolCall(id="c7", name="big", arguments={}))

    shown = message.content[0].text or ""
    assert shown.startswith("x" * 100) and shown.endswith("END")
    assert "the whole result, 2103 characters, is in .agent/results/big-c7.txt: read_file pages it" in shown
    assert (tmp_path / ".agent" / "results" / "big-c7.txt").read_text(encoding="utf-8") == big
    # The way back walks: read_file pages the file.
    reader = Toolbox(filesystem_tools(tmp_path, limits=limits))
    paged = await reader.run_async(ToolCall(id="r", name="read_file", arguments={"path": ".agent/results/big-c7.txt"}))
    assert (paged.content[0].text or "").startswith("1: " + "x" * 100)


async def test_without_a_spill_the_cut_says_the_rest_is_not_kept() -> None:
    limits = Limits(budget=4_000, chars_per_token=1.0)
    box = Toolbox([Tool(name="big", description="", parameters={"type": "object", "properties": {}}, run=lambda: "z" * 2_000)])

    message = await ToolExecutor(box, limits=limits).call(ToolCall(id="c8", name="big", arguments={}))

    assert "the rest is not kept" in (message.content[0].text or "")


# --- the shell: the spill file, the background page, the timeout --------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the local runner's boundary is Windows-only")
async def test_a_long_command_output_is_whole_in_a_file_under_the_folder(tmp_path: Path) -> None:
    limits = Limits(shell_output_chars=1_000)
    tools = Toolbox(shell_tools(tmp_path, LocalRunner(), limits=limits))
    python = f'& "{sys.executable}" -c "print(\'line\\n\' * 400)"'

    message = await tools.run_async(ToolCall(id="c", name="run_command", arguments={"command": python}))

    shown = message.content[0].text or ""
    assert "cut in the middle" in shown and "is in .agent/commands/1.txt: read_file pages it" in shown
    kept = (tmp_path / ".agent" / "commands" / "1.txt").read_text(encoding="utf-8")
    assert kept.count("line") == 400


def test_describe_names_the_file_and_the_size_when_the_output_was_cut() -> None:
    told = describe(Finished(exit_code=0, output="head … tail", cut=True, seconds=0.1, total=90_000, spilled=".agent/commands/3.txt"))

    assert "the whole output, 90000 characters, is in .agent/commands/3.txt: read_file pages it" in told


class Keeper:
    """A runner that can keep a process, scripted: what `start` is given."""

    where = "here"

    def __init__(self) -> None:
        self.started: list[str] = []
        self.ran: list[tuple[str, float]] = []

    async def run(self, command: str, cwd: Path, timeout: float, output_chars: int = 30_000) -> Finished:
        self.ran.append((command, timeout))
        return Finished(exit_code=0, output="", cut=False, seconds=0.0)

    async def start(self, command: str, cwd: Path):
        from app.tools.shell import Running

        self.started.append(command)

        class Done:
            returncode = None

            def poll(self):
                return None

        return Running(id="bg-1", command=command, process=Done(), output=cwd / "none.out", started=0.0)

    def peek(self, id: str):
        return None

    def stop(self, id: str):
        return None


async def test_a_timeout_above_the_most_starts_the_command_in_the_background_where_it_can(tmp_path: Path) -> None:
    runner = Keeper()
    tools = Toolbox(shell_tools(tmp_path, runner, limits=Limits(command_timeout_max=10)))

    message = await tools.run_async(
        ToolCall(id="c", name="run_command", arguments={"command": "sleep 100", "timeout_seconds": 100})
    )

    shown = message.content[0].text or ""
    assert runner.started == ["sleep 100"] and runner.ran == []
    assert "timeout_seconds=100 is above the 10 s most" in shown and "bg-1" in shown
    assert "maximum" not in tools.get("run_command").parameters["properties"]["timeout_seconds"]


async def test_a_background_output_is_paged_by_offset(tmp_path: Path) -> None:
    from app.tools.shell import Running, _background_report

    out = tmp_path / "bg.out"
    out.write_text("".join(f"{n:04d}\n" for n in range(400)), encoding="utf-8", newline="\n")

    class Alive:
        returncode = None

        def poll(self):
            return None

    running = Running(id="bg-2", command="x", process=Alive(), output=out, started=0.0)

    newest = _background_report(running, output_chars=1_600)
    assert "the newest 400 of 2000 characters" in newest and newest.rstrip().endswith("0399")
    first = _background_report(running, offset=0, output_chars=1_600)
    assert "0000\n" in first and "command_output 'bg-2' again with offset=400" in first


# --- the context: the media placeholder, the stub, the verbatim floor -------------


def test_a_dropped_picture_names_the_way_to_see_it_again() -> None:
    on_disk = ContentPart(kind="image", data=b"\x89", media_type="image/png", path="inbox/photo.png")
    nowhere = ContentPart(kind="image", data=b"\x89", media_type="image/png")

    assert dropped(on_disk) == "[image image/png, not carried in this request; read_file 'inbox/photo.png' shows it]"
    assert dropped(nowhere) == "[image image/png, not carried in this request]"


def test_the_media_budget_is_the_models_number() -> None:
    def picture(tag: int) -> Message:
        return Message(role="user", content=[ContentPart(kind="image", data=bytes([tag]), media_type="image/png")])

    history = [picture(index) for index in range(6)]
    wide = Context(history=history, limits=Limits(max_images=6))
    narrow = Context(history=history, limits=Limits(max_images=1))

    assert sum(1 for m in wide.prompt([]) if m.content[0].kind == "image") == 6
    assert sum(1 for m in narrow.prompt([]) if m.content[0].kind == "image") == 1


def test_the_verbatim_floor_moves_earlier_with_the_budgets_share() -> None:
    messages = []
    for index in range(6):
        messages.append(Message(role="user", content=text(f"question {index} " + "w" * 300)))
        messages.append(Message(role="assistant", content=text(f"answer {index} " + "w" * 300)))
    backend = ScriptedBackend()

    by_turns = verbatim_floor(messages, keep_turns=2)
    roomy = verbatim_floor(messages, keep_turns=2, backend=backend, keep_tokens=100_000)
    tight = verbatim_floor(messages, keep_turns=2, backend=backend, keep_tokens=1)

    assert by_turns == 8
    assert roomy == 2  # everything fits the share: only the oldest exchange is left to fold
    assert tight == 8  # the share is tiny: the two exchanges stay, as the floor


# --- the pages: a CSV in sections, the rendered page by offset, the instructions ---


def test_a_csv_is_read_in_sections_so_every_row_is_reachable() -> None:
    data = "\n".join(f"r{n},{n}" for n in range(501)).encode()

    sections = read_sections(data, "text/csv", csv_rows=200)

    assert [s.label for s in sections] == ["rows 1-200 of 501", "rows 201-400 of 501", "rows 401-501 of 501"]
    assert sections[2].text.endswith("r500 | 500")


def test_a_rendered_page_is_read_by_offset() -> None:
    rendered = Rendered(url="https://x", title="t", text="a" * 50 + "b" * 50, screenshot=b"", console_errors=[], refused=[])

    first = rendered.as_text(limit=50)
    second = rendered.as_text(limit=50, offset=50)

    assert "for the rest, view_web_page again with offset=50" in first
    assert "Continuing from character 50" in second and second.rstrip().endswith("b" * 50)


def test_the_instruction_file_share_names_the_rest_and_refuses_by_number(tmp_path: Path) -> None:
    (tmp_path / INSTRUCTIONS_FILE).write_text("q" * 300, encoding="utf-8")

    shown = read_instructions(tmp_path, max_bytes=100)

    assert shown.startswith("q" * 100)
    assert f"100 of 300 bytes of {INSTRUCTIONS_FILE} shown" in shown and "read_file 'AGENTS.md' shows it whole" in shown
    with pytest.raises(InstructionsError, match="must fit in 100 bytes; this is 300"):
        write_instructions(tmp_path, "q" * 300, max_bytes=100)


def test_the_default_limits_are_the_references_numbers() -> None:
    assert DEFAULT_LIMITS.read_lines == 2_000
    assert DEFAULT_LIMITS.command_timeout == 120 and DEFAULT_LIMITS.command_timeout_max == 600
    assert DEFAULT_LIMITS.shell_output_chars == 30_000
