"""`search_files` and `find_files` (roadmap 27 step 3).

The two tools every coding reference has: content search across a tree and
file discovery by glob, paged, `.gitignore` honoured. The Python engine is
what runs where no ripgrep is installed (this machine, the container); the
ripgrep argument line is checked on its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.tools import ToolError, search_tools
from app.tools.search import glob_regex, ripgrep_argv


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 'hello'\n\ndef helper():\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("HELLO = 1\nhello = 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hello\n\nsay hello twice\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"hello\0world")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello inside git", encoding="utf-8")
    return tmp_path


def tools(root: Path, **kw) -> dict:
    return {tool.name: tool for tool in search_tools(root, **kw)}


# --- search_files ------------------------------------------------------------


def test_content_search_lists_path_line_and_text(tree: Path) -> None:
    out = tools(tree)["search_files"].run(pattern="hello")

    lines = out.splitlines()
    assert "README.md:1: # hello" in lines
    assert "README.md:3: say hello twice" in lines
    assert "src/app.py:2:     return 'hello'" in lines
    assert "src/util.py:2: hello = 2" in lines
    # Binary files and .git are not searched; case matters unless asked.
    assert not any("blob.bin" in line or ".git" in line or "HELLO" in line for line in lines)


def test_ignore_case_glob_and_the_other_modes(tree: Path) -> None:
    box = tools(tree)["search_files"]

    assert "src/util.py:1: HELLO = 1" in box.run(pattern="hello", ignore_case=True)
    only_py = box.run(pattern="hello", glob="*.py")
    assert "README" not in only_py and "src/app.py:2" in only_py
    assert box.run(pattern="hello", mode="files").splitlines() == ["README.md", "src/app.py", "src/util.py"]
    assert "README.md: 2" in box.run(pattern="hello", mode="count")


def test_a_search_is_paged_and_says_how_to_continue(tree: Path) -> None:
    box = tools(tree)["search_files"]

    first = box.run(pattern="hello", limit=2)
    assert first.splitlines()[-1].startswith("(showing matches 1-2 of ")
    assert "offset=2" in first
    rest = box.run(pattern="hello", limit=2, offset=2)
    assert "src/app.py:2" in rest or "src/util.py:2" in rest


def test_multiline_and_bad_patterns(tree: Path) -> None:
    box = tools(tree)["search_files"]

    assert "src/app.py:1:" in box.run(pattern=r"def main\(\):\n\s+return", multiline=True)
    with pytest.raises(ToolError, match="not a valid regular expression"):
        box.run(pattern="(")
    with pytest.raises(ToolError, match="mode must be one of"):
        box.run(pattern="x", mode="everything")


def test_nothing_found_is_said_plainly(tree: Path) -> None:
    assert tools(tree)["search_files"].run(pattern="zzz-nowhere") == "no matches"


def test_git_ignored_files_are_skipped_when_git_knows_the_tree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("needle", encoding="utf-8")

    out = tools(tmp_path)["search_files"].run(pattern="needle")

    assert "keep.txt:1: needle" in out
    assert "build" not in out


def test_confined_search_stays_in_the_root_and_an_open_one_does_not(tree: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    elsewhere.mkdir()
    (elsewhere / "far.txt").write_text("hello far away", encoding="utf-8")

    with pytest.raises(ToolError, match="outside the allowed root"):
        tools(tree)["search_files"].run(pattern="hello", path=str(elsewhere))
    assert "far.txt:1: hello far away" in tools(tree, open_reads=True)["search_files"].run(
        pattern="hello", path=str(elsewhere)
    )


def test_the_ripgrep_line_carries_the_same_arguments() -> None:
    argv = ripgrep_argv("rg", "needle", Path("/tree"), "*.py", True, True)

    assert argv[0] == "rg" and "-i" in argv and "-U" in argv and "--glob" in argv and "*.py" in argv
    assert argv[-3:] == ["needle", "--", str(Path("/tree"))]


# --- find_files --------------------------------------------------------------


def test_a_bare_pattern_lists_one_directory_and_a_deep_one_the_tree(tree: Path) -> None:
    box = tools(tree)["find_files"]

    top = box.run(pattern="*").splitlines()
    assert set(top) == {"src/", "README.md", "blob.bin"}
    everything = box.run(pattern="**/*.py").splitlines()
    assert set(everything) == {"src/app.py", "src/util.py"}
    assert box.run(pattern="*.md").splitlines() == ["README.md"]
    assert box.run(pattern="*", path="src").splitlines()[:2] and all(
        line.startswith("src/") for line in box.run(pattern="*", path="src").splitlines()
    )


def test_find_files_is_paged_too(tree: Path) -> None:
    out = tools(tree)["find_files"].run(pattern="**/*", limit=2)

    assert "(showing entries 1-2 of" in out and "find_files again" in out


def test_glob_translation() -> None:
    assert glob_regex("*.py").match("a/b/c.py")
    assert glob_regex("src/**/*.ts").match("src/x/y/z.ts")
    assert glob_regex("src/**/*.ts").match("src/z.ts")
    assert not glob_regex("src/*.ts").match("src/x/z.ts")
    assert glob_regex("test_?.py").match("dir/test_a.py")


def test_the_contracts_say_what_the_tools_return(tree: Path) -> None:
    for tool in search_tools(tree):
        assert tool.returns and tool.leaves == "nothing."
        assert "instead of" not in tool.description
