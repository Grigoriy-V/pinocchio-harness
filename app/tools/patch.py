"""`apply_patch`: several files and several hunks in one call (roadmap 27 step 3).

The V4A patch shape Codex, Hermes and OpenClaw share
(`reports/2026-09-14_item27_step3_references.md` §3.3):

    *** Begin Patch
    *** Add File: path
    +each line of the new file
    *** Delete File: path
    *** Update File: path
    *** Move to: new/path            (optional, right after Update File)
    @@ a line that locates the hunk   (optional, may repeat to narrow)
     context line
    -removed line
    +added line
    *** End of File                   (optional: the hunk is anchored at the end)
    *** End Patch

No line numbers: context locates the change, as in Codex's parser. A hunk
is matched exactly first, then with trailing whitespace ignored, then with
both ends trimmed (Codex's `seek_sequence`). One hunk that does not match
fails the whole call with its unmatched lines quoted and nothing written:
all or nothing, so a half-applied patch never has to be undone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.tools.base import BAD_ARGUMENTS, Tool, ToolError
from app.tools.filesystem import (
    IO,
    IS_DIRECTORY,
    NOT_FOUND,
    _detail,
    _replace_atomically,
    outside,
    resolve_path,
)

BEGIN = "*** Begin Patch"
END = "*** End Patch"
ADD = "*** Add File: "
DELETE = "*** Delete File: "
UPDATE = "*** Update File: "
MOVE = "*** Move to: "
END_OF_FILE = "*** End of File"
NO_MATCH = "fs.patch_no_match"


@dataclass
class Hunk:
    anchors: list[str] = field(default_factory=list)
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    at_end: bool = False


@dataclass
class Change:
    kind: str  # add | delete | update
    path: str
    move_to: str | None = None
    lines: list[str] = field(default_factory=list)  # add: the file's lines
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(text: str) -> list[Change]:
    """The patch as a list of changes, or a refusal that names the line."""

    lines = text.replace("\r\n", "\n").split("\n")
    # A patch wrapped in a code fence or with blank lines around it is the
    # same patch.
    while lines and (not lines[0].strip() or lines[0].strip().startswith("```")):
        lines.pop(0)
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith("```")):
        lines.pop()
    if not lines or lines[0].strip() != BEGIN:
        raise ToolError(f"a patch starts with {BEGIN!r}", code=BAD_ARGUMENTS)
    if lines[-1].strip() != END:
        raise ToolError(f"a patch ends with {END!r}", code=BAD_ARGUMENTS)
    changes: list[Change] = []
    i = 1
    last = len(lines) - 1
    while i < last:
        line = lines[i]
        if line.startswith(ADD):
            change = Change("add", line[len(ADD):].strip())
            i += 1
            while i < last and not lines[i].startswith("*** "):
                if not lines[i].startswith("+"):
                    raise ToolError(
                        f"line {i + 1}: every line of an added file starts with +", code=BAD_ARGUMENTS
                    )
                change.lines.append(lines[i][1:])
                i += 1
            changes.append(change)
        elif line.startswith(DELETE):
            changes.append(Change("delete", line[len(DELETE):].strip()))
            i += 1
        elif line.startswith(UPDATE):
            change = Change("update", line[len(UPDATE):].strip())
            i += 1
            if i < last and lines[i].startswith(MOVE):
                change.move_to = lines[i][len(MOVE):].strip()
                i += 1
            hunk: Hunk | None = None
            while i < last and not (lines[i].startswith("*** ") and not lines[i].startswith(END_OF_FILE)):
                current = lines[i]
                if current.startswith("@@"):
                    if hunk is not None and (hunk.old or hunk.new):
                        change.hunks.append(hunk)
                        hunk = None
                    hunk = hunk or Hunk()
                    anchor = current[2:].strip()
                    if anchor:
                        hunk.anchors.append(anchor)
                elif current.startswith(END_OF_FILE):
                    if hunk is None:
                        raise ToolError(f"line {i + 1}: {END_OF_FILE} follows a hunk", code=BAD_ARGUMENTS)
                    hunk.at_end = True
                    change.hunks.append(hunk)
                    hunk = None
                elif current[:1] in (" ", "-", "+") or current == "":
                    hunk = hunk or Hunk()
                    body = current[1:] if current else ""
                    mark = current[:1] or " "
                    if mark != "+":
                        hunk.old.append(body)
                    if mark != "-":
                        hunk.new.append(body)
                else:
                    raise ToolError(
                        f"line {i + 1}: a hunk line starts with a space, - or +; got {current!r}",
                        code=BAD_ARGUMENTS,
                    )
                i += 1
            if hunk is not None and (hunk.old or hunk.new):
                change.hunks.append(hunk)
            if not change.hunks:
                raise ToolError(f"{UPDATE.strip()} {change.path}: no hunk follows", code=BAD_ARGUMENTS)
            changes.append(change)
        elif not line.strip():
            i += 1
        else:
            raise ToolError(f"line {i + 1}: expected {ADD.strip()}, {DELETE.strip()} or {UPDATE.strip()}; got {line!r}", code=BAD_ARGUMENTS)
    if not changes:
        raise ToolError("the patch changes nothing", code=BAD_ARGUMENTS)
    return changes


def patch_paths(text: str) -> list[str]:
    """Every path the patch touches; what a write-outside check asks about."""

    try:
        changes = parse_patch(text)
    except ToolError:
        return []
    paths = []
    for change in changes:
        paths.append(change.path)
        if change.move_to:
            paths.append(change.move_to)
    return paths


def _seek(lines: list[str], wanted: list[str], start: int, at_end: bool) -> int | None:
    """Where `wanted` occurs in `lines` at or after `start`: exact, then with
    trailing whitespace ignored, then trimmed on both sides."""

    if not wanted:
        return len(lines) if at_end else start
    n = len(wanted)
    if at_end:
        # A file that ends with a newline splits into a last empty line.
        candidates = [len(lines) - n] + ([len(lines) - n - 1] if lines and lines[-1] == "" else [])
    else:
        candidates = range(start, len(lines) - n + 1)
    for norm in (lambda s: s, str.rstrip, str.strip):
        target = [norm(w) for w in wanted]
        for i in candidates:
            if i < start or i < 0:
                continue
            if [norm(x) for x in lines[i : i + n]] == target:
                return i
    return None


def apply_hunks(text: str, hunks: list[Hunk], path: str) -> str:
    lines = text.split("\n")
    cursor = 0
    for hunk in hunks:
        for anchor in hunk.anchors:
            found = next((i for i in range(cursor, len(lines)) if lines[i].strip() == anchor.strip()), None)
            if found is None:
                raise ToolError(f"{path}: the @@ line {anchor!r} was not found", code=NO_MATCH)
            cursor = found + 1
        at = _seek(lines, hunk.old, cursor, hunk.at_end)
        if at is None:
            quoted = "\n".join(hunk.old[:6]) + ("\n…" if len(hunk.old) > 6 else "")
            raise ToolError(
                f"{path}: these lines were not found (read the file and patch what is there):\n{quoted}",
                code=NO_MATCH,
            )
        lines[at : at + len(hunk.old)] = hunk.new
        cursor = at + len(hunk.new)
    return "\n".join(lines)


def _counts(before: str, after: str) -> tuple[int, int]:
    import difflib

    added = removed = 0
    for line in difflib.unified_diff(before.split("\n"), after.split("\n"), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def apply_patch(root: Path, text: str, *, confined: bool = True) -> str:
    changes = parse_patch(text)
    planned: list[tuple[str, Path, str | None, Path | None, str]] = []  # kind, target, content, move_target, report
    for change in changes:
        target = resolve_path(root, change.path, confined=confined)
        if change.kind == "add":
            if target.is_dir():
                raise ToolError(f"{change.path} is a directory", code=IS_DIRECTORY)
            content = "\n".join(change.lines)
            planned.append(("add", target, content, None, f"A {change.path} ({len(change.lines)} lines)"))
            continue
        if not target.is_file():
            raise ToolError(f"{change.path} does not exist", code=NOT_FOUND)
        if change.kind == "delete":
            planned.append(("delete", target, None, None, f"D {change.path}"))
            continue
        try:
            before = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ToolError(f"{change.path} could not be read", code=IO, detail=_detail(error)) from error
        after = apply_hunks(before, change.hunks, change.path)
        added, removed = _counts(before, after)
        move_target = resolve_path(root, change.move_to, confined=confined) if change.move_to else None
        report = f"M {change.path} (+{added} -{removed})"
        if change.move_to:
            report = f"R {change.path} -> {change.move_to} (+{added} -{removed})"
        planned.append(("update", target, after, move_target, report))
    # Nothing was written above; now everything is.
    for kind, target, content, move_target, _ in planned:
        try:
            if kind == "delete":
                target.unlink()
            elif kind == "add":
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace_atomically(target, content or "")
            else:
                if move_target is not None and move_target != target:
                    move_target.parent.mkdir(parents=True, exist_ok=True)
                    _replace_atomically(move_target, content or "")
                    target.unlink()
                else:
                    _replace_atomically(target, content or "")
        except OSError as error:
            raise ToolError(f"{target.name} could not be written", code=IO, detail=_detail(error)) from error
    return "Updated:\n" + "\n".join(report for *_, report in planned)


def patch_tools(root: Path, *, open_reads: bool = False) -> list[Tool]:
    resolved = Path(root).resolve()
    confined = not open_reads
    asks = (
        None
        if confined
        else (lambda arguments: any(outside(resolved, p) for p in patch_paths(str(arguments.get("patch", "")))))
    )
    return [
        Tool(
            name="apply_patch",
            description=(
                "Change several files, or several places in one file, in one call, "
                "with a patch in this form:\n"
                "*** Begin Patch\n*** Update File: path/to/file.py\n@@ def main():\n"
                " unchanged context line\n-line to remove\n+line to add\n"
                "*** Add File: new/file.txt\n+first line\n+second line\n"
                "*** Delete File: old.txt\n*** End Patch\n"
                "Context lines start with a space and locate the change; there are no "
                "line numbers. `*** Move to: new/path` right after `*** Update File:` "
                "renames the file. `*** End of File` after a hunk anchors it at the end."
            ),
            returns=(
                "the files changed, one per line: A added, M modified with lines added "
                "and removed, D deleted, R renamed. When one hunk does not match, a "
                "refusal quoting the lines it looked for, and no file is touched."
            ),
            leaves="the changed files.",
            parameters={
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "minLength": len(BEGIN) + len(END),
                        "description": "The whole patch, from *** Begin Patch to *** End Patch.",
                    }
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
            run=lambda patch: apply_patch(resolved, str(patch), confined=confined),
            asks=asks,
            mutates=True,
        )
    ]
