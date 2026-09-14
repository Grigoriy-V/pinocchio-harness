"""Search across a tree, and find files by pattern (roadmap 27 step 3).

The two tools every coding reference has (Claude Code's Grep and Glob,
DeepSeek's grep and glob; Codex says "prefer rg") and this harness lacked.
One contract, two engines: ripgrep when a binary is on the machine, else a
Python walk with the same output shape, so the model never learns which ran
(`reports/2026-09-14_item27_step3_references.md` §3.2).

Both page: a `limit` and an `offset` over the results, and a last line that
says what to call for the rest, the shape `read_file` shares.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app.tools.base import BAD_ARGUMENTS, Tool, ToolError
from app.tools.filesystem import IO, NOT_A_DIRECTORY, NOT_FOUND, _detail, resolve_path

DEFAULT_LIMIT = 100
# A match line shown to the model is cut here; the file is read for the rest.
PREVIEW_CHARS = 400
# A file with a NUL in its first bytes is not text and is not searched.
BINARY_PROBE = 8_000
SKIPPED_DIRS = frozenset({".git"})
MODES = ("content", "files", "count")


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    text: str


def glob_regex(pattern: str, *, anywhere: bool = True) -> re.Pattern[str]:
    """A glob over posix-style relative paths: `**` crosses directories,
    `*` and `?` stay inside one segment. With `anywhere` (the search glob)
    a bare `*.py` matches at any depth; without it, a bare pattern names
    the one directory (what `find_files` lists)."""

    pattern = pattern.replace("\\", "/").strip()
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if anywhere and "/" not in pattern:
        pattern = "**/" + pattern
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _candidates(top: Path) -> Iterator[Path]:
    """Every file under `top` the search should see: what git tracks or would
    track when `top` is inside a repository (so `.gitignore` is honoured, as
    the references do), else a walk that skips only `.git`."""

    listed = _git_files(top)
    if listed is not None:
        yield from listed
        return
    for directory, names, files in os.walk(top):
        names[:] = sorted(name for name in names if name not in SKIPPED_DIRS)
        for name in sorted(files):
            yield Path(directory) / name


def _git_files(top: Path) -> list[Path] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        done = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=str(top),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    paths = [top / part.decode("utf-8", "replace") for part in done.stdout.split(b"\0") if part]
    return [path for path in paths if path.is_file()]


def _is_text(path: Path) -> bytes | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(BINARY_PROBE)
            if b"\0" in head:
                return None
            return head + handle.read()
    except OSError:
        return None


def _shown(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _search_python(
    top: Path, root: Path, regex: re.Pattern[str], file_filter: re.Pattern[str] | None, multiline: bool
) -> Iterator[Hit]:
    for path in _candidates(top):
        relative = _shown(path, top)
        if file_filter is not None and not file_filter.match(relative):
            continue
        data = _is_text(path)
        if data is None:
            continue
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        shown = _shown(path, root)
        if multiline:
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                yield Hit(shown, line, text.splitlines()[line - 1] if text else "")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                yield Hit(shown, number, line)


def ripgrep() -> str | None:
    return shutil.which("rg")


def ripgrep_argv(
    binary: str, pattern: str, top: Path, glob: str | None, ignore_case: bool, multiline: bool
) -> list[str]:
    argv = [binary, "--no-heading", "--color", "never", "--line-number", "--no-messages"]
    if ignore_case:
        argv.append("-i")
    if multiline:
        argv += ["-U", "--multiline-dotall"]
    if glob:
        argv += ["--glob", glob]
    argv += ["-e", pattern, "--", str(top)]
    return argv


def _search_ripgrep(binary: str, top: Path, root: Path, pattern: str, glob: str | None, ignore_case: bool, multiline: bool) -> Iterator[Hit]:
    try:
        done = subprocess.run(
            ripgrep_argv(binary, pattern, top, glob, ignore_case, multiline),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolError("the search could not run", code=IO, detail=_detail(error)) from error
    if done.returncode == 2:
        raise ToolError(
            f"the pattern {pattern!r} was refused by the search engine",
            code=BAD_ARGUMENTS,
            detail=done.stderr.decode("utf-8", "replace").strip() or None,
        )
    for raw in done.stdout.decode("utf-8", "replace").splitlines():
        # `path:line:text`; a Windows path carries a drive colon first.
        head, sep, rest = raw.partition(":")
        if len(head) == 1 and sep and rest[:1] in ("\\", "/"):
            head, sep, rest = raw[:2] + rest.partition(":")[0], ":", rest.partition(":")[2]
        number, _, text = rest.partition(":")
        try:
            yield Hit(_shown(Path(head), root), int(number), text)
        except ValueError:
            continue


def _paged(lines: list[str], offset: int, limit: int, unit: str, again: str) -> str:
    if offset < 0 or limit < 1:
        raise ToolError("offset must be zero or more and limit at least 1", code=BAD_ARGUMENTS)
    total = len(lines)
    if total == 0:
        return f"no {unit}"
    if offset and offset >= total:
        raise ToolError(f"offset {offset} is past the end: {total} {unit}", code=BAD_ARGUMENTS)
    shown = lines[offset : offset + limit]
    end = offset + len(shown)
    body = "\n".join(shown)
    if end >= total:
        return f"{body}\n({total} {unit})" if offset else body
    return f"{body}\n(showing {unit} {offset + 1}-{end} of {total}; for the rest, {again} with offset={end})"


def search_files(
    root: Path,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    mode: str = "content",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    ignore_case: bool = False,
    multiline: bool = False,
    *,
    confined: bool = True,
    binary: str | None = None,
) -> str:
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}", code=BAD_ARGUMENTS)
    if not pattern:
        raise ToolError("pattern cannot be empty", code=BAD_ARGUMENTS)
    top = resolve_path(root, path, confined=confined)
    if not top.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not top.is_dir():
        raise ToolError(f"path {path!r} is not a directory", code=NOT_A_DIRECTORY)
    flags = (re.IGNORECASE if ignore_case else 0) | (re.MULTILINE | re.DOTALL if multiline else 0)
    try:
        regex = re.compile(pattern, flags)
    except re.error as error:
        raise ToolError(f"pattern {pattern!r} is not a valid regular expression: {error}", code=BAD_ARGUMENTS) from error
    file_filter = glob_regex(glob) if glob else None
    engine = binary if binary is not None else ripgrep()
    hits: Iterable[Hit] = (
        _search_ripgrep(engine, top, root, pattern, glob, ignore_case, multiline)
        if engine
        else _search_python(top, root, regex, file_filter, multiline)
    )
    again = f"search_files again with the same arguments"
    if mode == "content":
        lines = [f"{hit.path}:{hit.line}: {_cut(hit.text)}" for hit in hits]
        return _paged(lines, int(offset), int(limit), "matches", again)
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.path] = counts.get(hit.path, 0) + 1
    if mode == "files":
        return _paged(list(counts), int(offset), int(limit), "files with matches", again)
    return _paged([f"{name}: {n}" for name, n in counts.items()], int(offset), int(limit), "files with matches", again)


def _cut(text: str) -> str:
    text = text.rstrip("\r\n")
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + f"… (line cut at {PREVIEW_CHARS} chars; read_file shows it whole)"


def find_files(
    root: Path,
    pattern: str = "*",
    path: str = ".",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    *,
    confined: bool = True,
) -> str:
    top = resolve_path(root, path, confined=confined)
    if not top.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not top.is_dir():
        raise ToolError(f"path {path!r} is not a directory", code=NOT_A_DIRECTORY)
    raw = (pattern or "*").replace("\\", "/").strip()
    # A bare name lists that directory's own entries; `**/name` reaches down.
    regex = glob_regex("**/*" if raw == "**" else raw, anywhere=False)
    found: list[tuple[float, str]] = []
    try:
        for directory, names, files in os.walk(top):
            names[:] = sorted(name for name in names if name not in SKIPPED_DIRS)
            here = Path(directory)
            for name in sorted(names):
                relative = _shown(here / name, top)
                if regex.match(relative):
                    found.append((_mtime(here / name), _shown(here / name, root) + "/"))
            for name in sorted(files):
                relative = _shown(here / name, top)
                if regex.match(relative):
                    found.append((_mtime(here / name), _shown(here / name, root)))
    except OSError as error:
        raise ToolError(f"path {path!r} could not be listed", code=IO, detail=_detail(error)) from error
    found.sort(key=lambda item: -item[0])
    return _paged([name for _, name in found], int(offset), int(limit), "entries", "find_files again with the same arguments")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def search_tools(root: Path, *, open_reads: bool = False) -> list[Tool]:
    resolved = Path(root).resolve()
    confined = not open_reads
    where = (
        "A directory inside the workspace root, absolute or relative to it."
        if confined
        else "A directory relative to the working folder, or any absolute directory on this machine."
    )
    paging = {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": f"How many results to show. Defaults to {DEFAULT_LIMIT}.",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "How many results to skip, to continue a long listing. Defaults to 0.",
        },
    }
    return [
        Tool(
            name="search_files",
            replay_safe=True,
            description=(
                "Search the text of every file under a directory for a regular "
                "expression. Files git ignores are skipped, so are binary files."
            ),
            returns=(
                "in mode content, one line per match as path:line: text; in mode files, "
                "the paths that match; in mode count, each path with its number of matches. "
                "Paged: the last line says the offset for the rest."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "minLength": 1,
                        "description": "A regular expression (Python/ripgrep syntax); escape a literal ( ) [ ] { } . with a backslash.",
                    },
                    "path": {"type": "string", "description": where + " Defaults to the working folder."},
                    "glob": {
                        "type": "string",
                        "description": "Only files whose path matches this glob, like *.py or src/**/*.ts.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": list(MODES),
                        "description": "content (default), files, or count.",
                    },
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive. Default false."},
                    "multiline": {
                        "type": "boolean",
                        "description": "Let the pattern span lines (. matches a newline). Default false.",
                    },
                    **paging,
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            run=lambda pattern, path=".", glob=None, mode="content", limit=DEFAULT_LIMIT, offset=0, ignore_case=False, multiline=False: search_files(
                resolved, pattern, path, glob, mode, limit, offset, bool(ignore_case), bool(multiline), confined=confined
            ),
        ),
        Tool(
            name="find_files",
            replay_safe=True,
            description=(
                "Find files and directories by name pattern under a directory. A bare "
                "pattern like * or *.md lists that directory's own entries; **/*.py "
                "reaches every depth."
            ),
            returns=(
                "one path per line, a directory with a / after it, newest first. Paged: "
                "the last line says the offset for the rest."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "A glob: * for everything here, *.py, **/*.py, src/**/test_*.py. Defaults to *.",
                    },
                    "path": {"type": "string", "description": where + " Defaults to the working folder."},
                    **paging,
                },
                "required": [],
                "additionalProperties": False,
            },
            run=lambda pattern="*", path=".", limit=DEFAULT_LIMIT, offset=0: find_files(
                resolved, pattern, path, limit, offset, confined=confined
            ),
        ),
    ]
