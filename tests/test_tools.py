"""Tools, and the boundary they are not allowed to cross.

The confinement tests are the point: a path-taking tool handed to a model is the
one place where a wrong answer becomes a wrong file read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import ToolCall, ToolFailure
from app.tools import BAD_ARGUMENTS, Tool, Toolbox, ToolError, filesystem_tools, tool_failed


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("kept inside", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deeper", encoding="utf-8")
    return tmp_path


def tools(root: Path) -> dict[str, Tool]:
    return {tool.name: tool for tool in filesystem_tools(root)}


# --- confinement -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["..", "../secret.txt", "sub/../../secret.txt", "C:/Windows/win.ini", "/etc/passwd"],
)
@pytest.mark.parametrize("name", ["read_file", "write_file", "edit_file"])
def test_paths_outside_the_root_are_refused(workspace: Path, name: str, path: str) -> None:
    escapee = workspace.parent / "secret.txt"
    escapee.write_text("must not be touched", encoding="utf-8")
    arguments = {"path": path}
    if name == "write_file":
        arguments["content"] = "overwritten"
    elif name == "edit_file":
        arguments.update(old_text="inside", new_text="outside")

    with pytest.raises(ToolError, match="outside the allowed root"):
        tools(workspace)[name].run(**arguments)

    assert escapee.read_text(encoding="utf-8") == "must not be touched"


def test_a_root_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        filesystem_tools(target)


# --- reading -----------------------------------------------------------------


def test_read_file_returns_the_text(workspace: Path) -> None:
    assert tools(workspace)["read_file"].run(path="notes.txt") == "1: kept inside"


def test_read_file_reaches_a_subdirectory(workspace: Path) -> None:
    assert tools(workspace)["read_file"].run(path="sub/deep.txt") == "1: deeper"


def test_read_file_accepts_an_absolute_path_inside_the_root(workspace: Path) -> None:
    target = workspace / "sub" / "deep.txt"

    assert tools(workspace)["read_file"].run(path=str(target.resolve())) == "1: deeper"


# --- writing -----------------------------------------------------------------


def test_writing_the_same_content_again_says_nothing_changed(workspace: Path) -> None:
    """Run `9c42241c`, 2026-09-03: `index.html` written seven times, identical."""

    write = tools(workspace)["write_file"]
    write.run(path="page.html", content="<p>hi</p>")

    again = write.run(path="page.html", content="<p>hi</p>")
    changed = write.run(path="page.html", content="<p>hi!</p>")

    assert again.startswith("unchanged: page.html already has exactly this content")
    assert 'send_file(path="page.html")' in again
    assert changed.startswith("overwrote page.html")
    assert (workspace / "page.html").read_text(encoding="utf-8") == "<p>hi!</p>"


def test_write_file_creates_a_file(workspace: Path) -> None:
    result = tools(workspace)["write_file"].run(path="fresh.txt", content="hello")

    assert (workspace / "fresh.txt").read_text(encoding="utf-8") == "hello"
    assert result == 'created fresh.txt (1 line); to hand it to the person: send_file(path="fresh.txt"); nothing is sent otherwise'


def test_write_file_accepts_an_absolute_path_inside_the_root(workspace: Path) -> None:
    target = workspace / "absolute.txt"

    tools(workspace)["write_file"].run(path=str(target.resolve()), content="hello")

    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_says_when_it_replaced_something(workspace: Path) -> None:
    result = tools(workspace)["write_file"].run(path="notes.txt", content="replaced")

    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "replaced"
    assert result.startswith("overwrote notes.txt (+1 -1 lines, now 1 line)")


def test_write_file_makes_the_directories_it_needs(workspace: Path) -> None:
    tools(workspace)["write_file"].run(path="a/b/c.txt", content="deep")

    assert (workspace / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "deep"


def test_write_file_on_a_directory_is_refused(workspace: Path) -> None:
    with pytest.raises(ToolError, match="is a directory"):
        tools(workspace)["write_file"].run(path="sub", content="x")


@pytest.mark.parametrize("path", ["Task Board/", "Task Board\\", "a/b/"])
def test_a_trailing_separator_never_makes_a_file_with_a_folders_name(
    workspace: Path, path: str
) -> None:
    """Live on 2026-08-31 this cost three turns twice over.

    `pathlib` drops the trailing separator, so a call plainly meant to make a
    folder made a file with the folder's name, and every write into that folder
    afterwards failed. A trailing separator is how everyone writes a directory,
    so the call is not wrong — the answer is to say what to do instead.
    """

    with pytest.raises(ToolError, match="names a directory"):
        tools(workspace)["write_file"].run(path=path, content="# notes")

    assert not (workspace / path.rstrip("/\\")).exists()


def test_a_file_standing_where_a_folder_should_be_says_so(workspace: Path) -> None:
    """Instead of `FileExistsError [WinError 183]`, which nothing can act on."""

    tools(workspace)["write_file"].run(path="Board", content="x")

    with pytest.raises(ToolError, match="'Board' is a file"):
        tools(workspace)["write_file"].run(path="Board/index.html", content="<h1>hi</h1>")


def test_the_advice_it_gives_is_advice_that_works(workspace: Path) -> None:
    """The refusal says directories are made for you. They are."""

    tools(workspace)["write_file"].run(path="Task Board/index.html", content="<h1>hi</h1>")

    assert (workspace / "Task Board" / "index.html").is_file()


def test_edit_file_replaces_one_exact_match(workspace: Path) -> None:
    result = tools(workspace)["edit_file"].run(
        path="notes.txt", old_text="kept", new_text="stayed"
    )

    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "stayed inside"
    assert result == "edited notes.txt: replaced 1 match at line 1; now 1 line"


@pytest.mark.parametrize("text", ["absent", "e"])
def test_edit_file_refuses_non_unique_matches(workspace: Path, text: str) -> None:
    before = (workspace / "notes.txt").read_text(encoding="utf-8")

    with pytest.raises(ToolError, match="was not found|occurs 2 times"):
        tools(workspace)["edit_file"].run(path="notes.txt", old_text=text, new_text="x")

    assert (workspace / "notes.txt").read_text(encoding="utf-8") == before


def test_edit_file_refuses_an_empty_match(workspace: Path) -> None:
    with pytest.raises(ToolError, match="cannot be empty"):
        tools(workspace)["edit_file"].run(path="notes.txt", old_text="", new_text="x")


def test_edit_file_on_a_missing_file_is_refused(workspace: Path) -> None:
    with pytest.raises(ToolError, match="does not exist") as refused:
        tools(workspace)["edit_file"].run(
            path="missing.txt", old_text="old", new_text="new"
        )

    assert refused.value.code == "fs.not_found"


def test_edit_file_leaves_the_original_when_atomic_replace_fails(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (workspace / "notes.txt").read_text(encoding="utf-8")

    def fail_replace(source: str, destination: Path) -> None:
        raise PermissionError(13, "permission denied")

    monkeypatch.setattr("app.tools.filesystem.os.replace", fail_replace)
    with pytest.raises(ToolError, match="could not be written") as refused:
        tools(workspace)["edit_file"].run(
            path="notes.txt", old_text="kept", new_text="changed"
        )

    assert refused.value.code == "fs.io" and refused.value.detail == "permission denied"

    assert (workspace / "notes.txt").read_text(encoding="utf-8") == before
    assert sorted(path.name for path in workspace.iterdir()) == ["notes.txt", "sub"]


def test_workspace_write_and_edit_are_autonomous(workspace: Path) -> None:
    box = toolbox(workspace)

    assert [name for name in box.names if box.requires_approval(name)] == []


def test_an_unknown_tool_needs_no_approval(workspace: Path) -> None:
    # It never runs, so there is nothing to ask about — it only produces the
    # error that tells the model the tool does not exist.
    assert toolbox(workspace).requires_approval("rm") is False


# --- the toolbox -------------------------------------------------------------


def toolbox(workspace: Path) -> Toolbox:
    return Toolbox(filesystem_tools(workspace))


def test_schemas_describe_every_tool(workspace: Path) -> None:
    schemas = toolbox(workspace).schemas()

    assert [schema["function"]["name"] for schema in schemas] == [
        "read_file",
        "write_file",
        "edit_file",
    ]
    assert schemas[0]["function"]["parameters"]["required"] == ["path"]
    assert all(schema["type"] == "function" for schema in schemas)


def test_a_call_becomes_a_tool_message_carrying_its_id(workspace: Path) -> None:
    call = ToolCall(id="call_1", name="read_file", arguments={"path": "notes.txt"})

    message = toolbox(workspace).run(call)

    assert message.role == "tool"
    assert message.tool_call_id == "call_1"
    assert message.content[0].text == "1: kept inside"


def test_a_refused_path_reaches_the_model_instead_of_raising(workspace: Path) -> None:
    call = ToolCall(id="call_1", name="read_file", arguments={"path": "../secret.txt"})

    message = toolbox(workspace).run(call)

    assert message.content[0].text.startswith("error: path")
    assert message.tool_call_id == "call_1"


def test_an_unknown_tool_is_reported_with_the_available_names(workspace: Path) -> None:
    call = ToolCall(id="call_1", name="rm_rf", arguments={})

    text = toolbox(workspace).run(call).content[0].text

    assert "unknown tool 'rm_rf'" in text
    assert "read_file" in text


def test_wrong_arguments_are_reported_rather_than_crashing(workspace: Path) -> None:
    call = ToolCall(id="call_1", name="read_file", arguments={"filename": "notes.txt"})

    text = toolbox(workspace).run(call).content[0].text

    assert text.startswith("error: bad arguments for read_file")


def test_schema_rejects_wrong_types_and_unexpected_arguments(workspace: Path) -> None:
    wrong_type = ToolCall(id="a", name="read_file", arguments={"path": 3})
    extra = ToolCall(
        id="b", name="write_file", arguments={"path": "x", "content": "y", "force": True}
    )

    assert "argument 'path' must be string" in toolbox(workspace).run(wrong_type).content[0].text
    assert "unexpected argument(s): force" in toolbox(workspace).run(extra).content[0].text


def test_an_empty_result_still_produces_content(workspace: Path) -> None:
    box = Toolbox([Tool(name="quiet", description="", parameters={}, run=lambda: "")])

    message = box.run(ToolCall(id="call_1", name="quiet", arguments={}))

    assert message.content[0].text == "(empty)"


def test_an_operating_system_failure_a_tool_did_not_catch_is_internal() -> None:
    """A family wraps its own OS errors with its codes; one that escaped is a surprise."""

    def denied() -> str:
        raise PermissionError(13, "permission denied")

    box = Toolbox([Tool(name="blocked", description="", parameters={}, run=denied)])

    message = box.run(ToolCall(id="call_1", name="blocked", arguments={}))

    assert message.content[0].text == "error: blocked failed: PermissionError (permission denied)"
    assert message.failure is not None and message.failure.code == "internal"


def test_a_programming_error_becomes_an_internal_failure_the_model_can_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The turn goes on and the developer gets the traceback, in the log.

    Killing the run over a bug in one tool loses the conversation; hiding the
    bug loses the developer. The model reads that the tool failed and why in
    one sentence, and the full traceback is in the process log.
    """

    def broken() -> str:
        raise ValueError("bug")

    box = Toolbox([Tool(name="broken", description="", parameters={}, run=broken)])

    with caplog.at_level("ERROR", logger="app.tools.execution"):
        message = box.run(ToolCall(id="call_1", name="broken", arguments={}))

    assert message.content[0].text == "error: broken failed: ValueError (bug)"
    assert message.failure == ToolFailure(code="internal", message="broken failed: ValueError", detail="bug")
    assert "ValueError: bug" in caplog.text


def test_every_way_a_call_can_fail_is_recognisable_as_a_failure() -> None:
    """Telemetry asks the toolbox whether a result went wrong, not a string.

    A tool failure is a message the model reads rather than an exception, so
    `tool_failed` is the only signal there is. If a failure path ever stops
    matching it, a failing tool starts being counted as a successful one.
    """

    def denied() -> str:
        raise PermissionError(13, "permission denied")

    def refused() -> str:
        raise ToolError("that path is outside the workspace")

    box = Toolbox(
        [
            Tool(name="blocked", description="", parameters={}, run=denied),
            Tool(name="refuses", description="", parameters={}, run=refused),
            Tool(
                name="strict",
                description="",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                run=lambda path: path,
            ),
            Tool(name="fine", description="", parameters={}, run=lambda: "all good"),
        ]
    )
    failures = [
        ToolCall(id="c1", name="blocked", arguments={}),
        ToolCall(id="c2", name="refuses", arguments={}),
        ToolCall(id="c3", name="strict", arguments={}),  # missing argument
        ToolCall(id="c4", name="absent", arguments={}),  # unknown tool
    ]

    assert all(tool_failed(box.run(call)) for call in failures)
    assert not tool_failed(box.run(ToolCall(id="c5", name="fine", arguments={})))


def test_a_path_wrapped_in_quotes_or_carrying_a_delimiter_is_refused(tmp_path: Path) -> None:
    """Live 2026-09-03: the served parser left `"…index.html"<|"|>` as the path
    and a file of that name was created; every later call by the real name
    failed. A corrupted call is refused, not obeyed."""

    box = Toolbox(filesystem_tools(tmp_path))
    for bad in ('"Task Board test 4/index.html"<|"|>', "'a.txt'", "a<|b.txt"):
        result = box.run(ToolCall("w", "write_file", {"path": bad, "content": "x"}))
        assert result.failure is not None and result.failure.code == BAD_ARGUMENTS, bad
        assert "send it again" in (result.content[0].text or "")
    assert list(tmp_path.iterdir()) == []
    ok = box.run(ToolCall("w", "write_file", {"path": "it's fine.txt", "content": "x"}))
    assert ok.failure is None


def test_a_long_file_comes_in_pages(workspace: Path) -> None:
    """Until 2026-09-03 a file was cut at the limit with no way to the rest."""

    (workspace / "big.txt").write_text("\n".join(f"line {n}" for n in range(1, 701)) + "\n", encoding="utf-8")
    read = tools(workspace)["read_file"]

    first = read.run(path="big.txt")
    rest = read.run(path="big.txt", offset=501)
    some = read.run(path="big.txt", offset=10, limit=2)

    assert first.startswith("  1: line 1\n  2: line 2\n")
    assert first.endswith("500: line 500\n(showing lines 1-500 of 700; for the rest, read_file 'big.txt' again with offset=501)")
    assert rest.startswith("501: line 501\n") and rest.endswith("700: line 700\n(end of file, 700 lines)")
    assert some == "10: line 10\n11: line 11\n(showing lines 10-11 of 700; for the rest, read_file 'big.txt' again with offset=12)"


def test_a_very_long_line_is_cut_and_a_page_stops_under_the_result_cap(workspace: Path) -> None:
    from app.tools.filesystem import LINE_CHARS, PAGE_CHARS

    (workspace / "wide.txt").write_text("x" * (LINE_CHARS + 50) + "\n" + "y\n", encoding="utf-8")
    (workspace / "dense.txt").write_text("".join(f"{'z' * 1000}\n" for _ in range(60)), encoding="utf-8")
    read = tools(workspace)["read_file"]

    wide = read.run(path="wide.txt")
    dense = read.run(path="dense.txt")

    assert f"… (line cut at {LINE_CHARS} chars)" in wide.splitlines()[0] and wide.splitlines()[1] == "2: y"
    assert len(dense) <= PAGE_CHARS and "for the rest, read_file 'dense.txt' again with offset=" in dense


def test_an_offset_past_the_end_is_refused(workspace: Path) -> None:
    with pytest.raises(ToolError, match="offset 99 is past the end: the file has 1 lines"):
        tools(workspace)["read_file"].run(path="notes.txt", offset=99)
