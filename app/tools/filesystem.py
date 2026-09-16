"""File tools on one root: read by lines, write, edit.

Searching the tree and finding files are `app/tools/search.py`; a patch over
several files is `app/tools/patch.py`; the three share this module's path
rules (roadmap 27 step 3, `reports/2026-09-14_item27_step3_references.md`).

The root is passed in, never read from the environment by the tool itself, so a
caller cannot accidentally hand the model the whole disk. Every path the model
supplies is resolved and checked against that root before anything is opened.

The granted root is the autonomy boundary: reads, writes and edits inside it do
not ask one call at a time. Resolution and confinement still apply every time.

One implementation, parameterized by the root. That is the whole difference
between a Windows workspace, a Linux one and a mounted volume, so there is no
`Filesystem` protocol here; the first real second implementation is the
sandbox, and it arrives with its own interface when it arrives.

Every failure is an `fs.*` code with a message in the words a person would use
and the operating system's own `strerror` as detail. Nothing platform-specific
— no `[WinError 183]`, no resolved absolute path — reaches the model.
"""

from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from app.models import ContentPart
from app.limits import DEFAULT_LIMITS, Limits
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError, handover

# A page of a file, in lines, and the width a line is cut at: the limits'
# (`app/limits.py`, roadmap 31): the lines and the width are settings with
# the references' defaults, the page's characters a share of the budget under
# the executor's result cap, so the middle of a page is never cut.
DEFAULT_LINES = DEFAULT_LIMITS.read_lines
PAGE_CHARS = DEFAULT_LIMITS.page_chars
LINE_CHARS = DEFAULT_LIMITS.line_chars

# The family's codes. Added only when something has to branch on one.
OUTSIDE_ROOT = "fs.outside_root"
NOT_FOUND = "fs.not_found"
NOT_A_FILE = "fs.not_a_file"
NOT_A_DIRECTORY = "fs.not_a_directory"
IS_DIRECTORY = "fs.is_directory"
BLOCKED_BY_FILE = "fs.blocked_by_file"
AMBIGUOUS_EDIT = "fs.ambiguous_edit"
TOO_LARGE = "fs.too_large"
IO = "fs.io"


def _detail(error: BaseException) -> str:
    return getattr(error, "strerror", None) or str(error) or type(error).__name__


# What a corrupted emission looks like inside a path: the served string
# delimiter, or the quotes the delimiter was meant to replace. Live on
# 2026-09-03 a model wrote `"Task Board test 4/index.html"<|"|>` and the tool
# created a file of that name, which every later call by the real name could
# not find (ISSUES.md ISS-0012). No path a person means contains these.
_NOT_IN_A_PATH = ("<|", "|>")


def corrupted_path(path: str) -> bool:
    text = path.strip()
    return any(mark in text for mark in _NOT_IN_A_PATH) or (
        len(text) > 1 and text[0] == text[-1] and text[0] in "\'\""
    )


def resolve_path(root: Path, path: str, *, confined: bool = True) -> Path:
    """Resolve a model-supplied path against the root; with `confined` off
    any absolute path on the machine is allowed (the person's own machine,
    where the references read anywhere too)."""

    if confined:
        return resolve_in_root(root, path)
    if corrupted_path(path or ""):
        raise ToolError(
            f"path {path!r} contains quotes or a delimiter no path has; the call "
            "arrived corrupted, send it again with the plain path",
            code=BAD_ARGUMENTS,
        )
    try:
        supplied = Path(path or ".")
        return supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    except (OSError, RuntimeError) as error:
        raise ToolError(
            f"path {path!r} cannot be resolved", code=IO, detail=_detail(error)
        ) from error


def outside(root: Path, path: str | None) -> bool:
    """Whether a model-supplied path leaves the root: what a write there
    asks about. A path that cannot be resolved is left to the tool to
    refuse."""

    try:
        target = resolve_path(root, path or ".", confined=False)
    except ToolError:
        return False
    return target != root and root not in target.parents


def resolve_in_root(root: Path, path: str) -> Path:
    """Resolve a model-supplied path inside the root, or refuse it.

    Resolution happens before the check, so `..` segments, absolute paths and
    symlinks that leave the root are all refused by the same comparison. A path
    that carries a served delimiter or wrapping quotes is refused before that:
    it is a corrupted call, and the honest answer is to ask for it again rather
    than to make a file nobody named.
    """

    if corrupted_path(path or ""):
        raise ToolError(
            f"path {path!r} contains quotes or a delimiter no path has; the call "
            "arrived corrupted, send it again with the plain path",
            code=BAD_ARGUMENTS,
        )
    try:
        supplied = Path(path or ".")
        candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    except (OSError, RuntimeError) as error:
        raise ToolError(
            f"path {path!r} cannot be resolved", code=IO, detail=_detail(error)
        ) from error
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"path {path!r} is outside the allowed root", code=OUTSIDE_ROOT)
    return candidate


def _existing_file(root: Path, path: str, *, confined: bool = True) -> Path:
    """The file this path names, or the reason it does not."""

    target = resolve_path(root, path, confined=confined)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_file():
        raise ToolError(f"path {path!r} is not a file", code=NOT_A_FILE)
    return target


# A file the model reads is shown in its own kind: text as text, a picture
# as a picture. Until 2026-09-04 a PNG came back as replacement characters,
# so a chart the model had just made was the one thing it could not look at
# (the human's ask, after scenario R exposed it). The same types the chat
# accepts from a person (`app/attachments.py`), by suffix.
IMAGE_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _read_file(
    root: Path,
    path: str,
    offset: int = 1,
    limit: int | None = None,
    *,
    confined: bool = True,
    limits: Limits = DEFAULT_LIMITS,
) -> str | list[ContentPart]:
    target = _existing_file(root, path, confined=confined)
    media_type = IMAGE_SUFFIXES.get(target.suffix.lower())
    try:
        if media_type:
            data = target.read_bytes()
        else:
            text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ToolError(
            f"path {path!r} could not be read", code=IO, detail=_detail(error)
        ) from error
    if media_type:
        return [
            ContentPart(
                kind="text",
                text=f"{target.name}: an image ({media_type}, {len(data)} bytes), shown to you below.",
            ),
            ContentPart(kind="image", data=data, media_type=media_type),
        ]
    return numbered_page(
        text,
        int(offset),
        int(limit) if limit else limits.read_lines,
        f"read_file {path!r}",
        page_chars=limits.page_chars,
        line_chars=limits.line_chars,
    )


def numbered_page(
    text: str,
    offset: int,
    limit: int,
    call: str,
    page_chars: int = PAGE_CHARS,
    line_chars: int = LINE_CHARS,
) -> str:
    """`limit` lines of `text` from line `offset` (1-based), each as `N: text`,
    and a last line that says how to get the rest. A page also stops at
    `page_chars`, so one result never has its middle cut by the executor."""

    if offset < 1 or limit < 1:
        raise ToolError("offset must be 1 or more and limit at least 1", code=BAD_ARGUMENTS)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if total == 0:
        return "(empty file)"
    if offset > total:
        raise ToolError(f"offset {offset} is past the end: the file has {total} lines", code=BAD_ARGUMENTS)
    width = len(str(min(total, offset + limit - 1)))
    out: list[str] = []
    size = 0
    number = offset
    while number <= total and number < offset + limit:
        line = lines[number - 1].rstrip("\r")
        if len(line) > line_chars:
            line = line[:line_chars] + f"… (line cut at {line_chars} chars)"
        rendered = f"{number:>{width}}: {line}"
        # The footer that names the next offset fits in the page too.
        if size + len(rendered) + 1 > page_chars - 120 and out:
            break
        out.append(rendered)
        size += len(rendered) + 1
        number += 1
    last = number - 1
    body = "\n".join(out)
    if last >= total:
        return body if offset == 1 else f"{body}\n(end of file, {total} lines)"
    return f"{body}\n(showing lines {offset}-{last} of {total}; for the rest, {call} again with offset={last + 1})"


def _names_a_directory(path: str) -> bool:
    """A trailing separator means a directory, everywhere except in `pathlib`.

    `Path("notes/")` is `Path("notes")`, so without this check a call meant to
    make a folder makes a file with the folder's name, and every write into
    that folder afterwards fails. Live on 2026-08-31 this cost three turns and
    scattered four files into the root of someone's workspace.
    """

    return path.rstrip().endswith(("/", "\\"))


def _blocked_by(root: Path, target: Path) -> Path | None:
    """The ancestor that is a file, if a file is why nothing can be written."""

    return next((parent for parent in target.parents if parent.is_file()), None)


def _replace_atomically(target: Path, text: str) -> None:
    """Write beside the target and move into place, so no reader sees half a file.

    Temp file, fsync, replace. The bytes are on disk before the name points at
    them, and an interrupted worker leaves either the old file or the new one,
    never a torn artifact with the old name.
    """

    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as output:
            temporary = output.name
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            os.chmod(temporary, target.stat().st_mode)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _text_of(target: Path) -> str | None:
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _lines(n: int) -> str:
    return f"{n} line{'' if n == 1 else 's'}"


def count_lines(text: str) -> int:
    return len(text.split("\n")) - (1 if text.endswith("\n") or not text else 0)


def line_counts(before: str, after: str) -> tuple[int, int]:
    """Lines added and removed between two texts, as a diff would count them."""

    added = removed = 0
    for line in difflib.unified_diff(before.split("\n"), after.split("\n"), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _line_numbers(text: str, needle: str) -> list[int]:
    numbers = []
    start = 0
    while True:
        at = text.find(needle, start)
        if at < 0:
            return numbers
        numbers.append(text.count("\n", 0, at) + 1)
        start = at + max(len(needle), 1)


def _write_file(root: Path, path: str, content: str, *, confined: bool = True) -> str:
    if _names_a_directory(path):
        raise ToolError(
            f"path {path!r} names a directory, not a file. Write the file you want "
            "and any directories it needs are created for you.",
            code=IS_DIRECTORY,
        )
    target = resolve_path(root, path, confined=confined)
    if target.is_dir():
        raise ToolError(f"path {path!r} is a directory", code=IS_DIRECTORY)
    existed = target.is_file()
    before = _text_of(target) if existed else None
    if existed and before == content:
        # Seen seven times in one turn on 2026-09-03 (run `9c42241c`): the same
        # page written again and again. A result that says "overwrote" reads as
        # progress; this one does not.
        return f"unchanged: {path} already has exactly this content, so nothing was written; {handover(path)}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _replace_atomically(target, content)
    except OSError as error:
        # A platform error code is not something a model can act on. Say what is
        # in the way when that is knowable, and otherwise say what failed.
        blocking = _blocked_by(root, target)
        if blocking is not None:
            raise ToolError(
                f"{blocking.name!r} is a file, so nothing can be written inside it",
                code=BLOCKED_BY_FILE,
            ) from error
        raise ToolError(
            f"path {path!r} could not be written", code=IO, detail=_detail(error)
        ) from error
    lines = count_lines(content)
    if existed:
        added, removed = line_counts(before or "", content)
        return f"overwrote {path} (+{added} -{removed} lines, now {_lines(lines)}); {handover(path)}"
    return f"created {path} ({_lines(lines)}); {handover(path)}"


def _edit_file(
    root: Path,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    *,
    confined: bool = True,
) -> str:
    target = _existing_file(root, path, confined=confined)
    if not old_text:
        raise ToolError("old_text cannot be empty", code=BAD_ARGUMENTS)

    try:
        current = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ToolError(
            f"path {path!r} could not be read", code=IO, detail=_detail(error)
        ) from error
    at = _line_numbers(current, old_text)
    if not at:
        raise ToolError(
            f"old_text was not found in {path!r}; read the file and give the text as it is there",
            code=AMBIGUOUS_EDIT,
        )
    if len(at) > 1 and not replace_all:
        places = ", ".join(str(n) for n in at[:10]) + ("…" if len(at) > 10 else "")
        raise ToolError(
            f"old_text occurs {len(at)} times in {path!r} (lines {places}); include more "
            "surrounding lines to name one, or set replace_all",
            code=AMBIGUOUS_EDIT,
        )
    updated = current.replace(old_text, new_text) if replace_all else current.replace(old_text, new_text, 1)
    try:
        _replace_atomically(target, updated)
    except OSError as error:
        raise ToolError(
            f"path {path!r} could not be written", code=IO, detail=_detail(error)
        ) from error
    where = ", ".join(str(n) for n in at[:10]) + ("…" if len(at) > 10 else "")
    plural = "es" if len(at) > 1 else ""
    return f"edited {path}: replaced {len(at)} match{plural} at line{'s' if len(at) > 1 else ''} {where}; now {_lines(count_lines(updated))}"


def filesystem_tools(
    root: Path, *, open_reads: bool = False, limits: Limits = DEFAULT_LIMITS
) -> list[Tool]:
    """Build the filesystem tools on `root`.

    Confined (the default, the deployed profile): every path stays inside
    the root. Open (`open_reads`, the person's own machine): reading reaches
    any path on the machine, as the references do; a write outside the root
    asks the person first (`Tool.asks`), and so stays possible.
    """

    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")
    confined = not open_reads
    where = (
        "A path inside the workspace root, absolute or relative to it."
        if confined
        else "A path relative to the working folder, or any absolute path on this machine."
    )
    asks = None if confined else (lambda arguments: outside(resolved, arguments.get("path")))

    return [
        Tool(
            name="read_file",
            replay_safe=True,
            description=(
                "Read a file: a text file as numbered lines, an image (png, jpg, webp) "
                "as a picture you look at."
            ),
            returns=(
                "the lines as `number: text`, a page at a time; the last line of a page "
                "says which offset to ask for next. For an image, the picture itself."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": where},
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "The first line to show, 1-based. Defaults to 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": f"How many lines to show. Defaults to {limits.read_lines}.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            run=lambda path, offset=1, limit=None: _read_file(
                resolved, path, int(offset), int(limit) if limit else None, confined=confined, limits=limits
            ),
        ),
        Tool(
            name="write_file",
            description=(
                "Write a UTF-8 text file, replacing it if it exists; missing directories "
                "are created."
            ),
            returns=(
                "created or overwrote, the path, and for an overwrite the lines added and "
                "removed; a refusal names why."
            ),
            leaves="the file, at that path.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": where},
                    "content": {
                        "type": "string",
                        "description": "The complete new contents of the file.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            run=lambda path, content: _write_file(resolved, path, content, confined=confined),
            asks=asks,
            mutates=True,
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace one exact text fragment in an existing UTF-8 file, or every "
                "occurrence of it with replace_all. `old_text` is matched exactly, "
                "indentation included, and must occur once unless replace_all is set. "
                "For several places or several files at once, apply_patch."
            ),
            returns=(
                "the number of replacements and the line numbers where they were made; "
                "when `old_text` is missing or occurs several times, a refusal naming the "
                "lines, and the file is untouched."
            ),
            leaves="the changed file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "description": where},
                    "old_text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Exact text as it is in the file; enough lines to be unique.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text; may be empty to delete the match.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence. Default false.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            run=lambda path, old_text, new_text, replace_all=False: _edit_file(
                resolved, path, old_text, new_text, bool(replace_all), confined=confined
            ),
            asks=asks,
            mutates=True,
        ),
    ]
