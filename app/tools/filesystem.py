"""File tools, confined to one directory.

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

import os
import tempfile
from pathlib import Path

from app.models import ContentPart
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError, handover
from app.tools.paging import page

MAX_ENTRIES = 200
MAX_CHARS = 20_000

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


def _existing_file(root: Path, path: str) -> Path:
    """The file this path names, or the reason it does not."""

    target = resolve_in_root(root, path)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_file():
        raise ToolError(f"path {path!r} is not a file", code=NOT_A_FILE)
    return target


def _list_files(root: Path, path: str = ".") -> str:
    target = resolve_in_root(root, path)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_dir():
        raise ToolError(f"path {path!r} is not a directory", code=NOT_A_DIRECTORY)
    try:
        entries = sorted(
            f"{entry.name}/" if entry.is_dir() else entry.name for entry in target.iterdir()
        )
    except OSError as error:
        raise ToolError(
            f"path {path!r} could not be listed", code=IO, detail=_detail(error)
        ) from error
    if not entries:
        return f"{path}: empty"
    shown = entries[:MAX_ENTRIES]
    listing = "\n".join(shown)
    if len(entries) > len(shown):
        listing += f"\n... {len(entries) - len(shown)} more entries"
    return listing


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


def _read_file(root: Path, path: str, offset: int = 0) -> str | list[ContentPart]:
    target = _existing_file(root, path)
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
    return page(text, offset, MAX_CHARS, f"read_file {path!r} again with offset={{offset}}")


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


def _same_content(target: Path, content: str) -> bool:
    try:
        return target.read_text(encoding="utf-8") == content
    except (OSError, UnicodeDecodeError):
        return False


def _write_file(root: Path, path: str, content: str) -> str:
    if _names_a_directory(path):
        raise ToolError(
            f"path {path!r} names a directory, not a file. Write the file you want "
            "and any directories it needs are created for you.",
            code=IS_DIRECTORY,
        )
    target = resolve_in_root(root, path)
    if target.is_dir():
        raise ToolError(f"path {path!r} is a directory", code=IS_DIRECTORY)
    existed = target.is_file()
    if existed and _same_content(target, content):
        # Seen seven times in one turn on 2026-09-03 (run `9c42241c`): the same
        # page written again and again. A result that says "overwrote" reads as
        # progress; this one does not.
        return (
            f"unchanged: {path} already had exactly this content "
            f"({len(content)} characters), so nothing was written; {handover(path)}"
        )
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
    verb = "overwrote" if existed else "created"
    return f"{verb} {path} ({len(content)} characters); {handover(path)}"


def _edit_file(root: Path, path: str, old_text: str, new_text: str) -> str:
    target = _existing_file(root, path)
    if not old_text:
        raise ToolError("old_text cannot be empty", code=BAD_ARGUMENTS)

    try:
        current = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ToolError(
            f"path {path!r} could not be read", code=IO, detail=_detail(error)
        ) from error
    matches = current.count(old_text)
    if matches != 1:
        raise ToolError(
            f"old_text must occur exactly once in {path!r}; found {matches} matches",
            code=AMBIGUOUS_EDIT,
        )
    updated = current.replace(old_text, new_text, 1)
    try:
        _replace_atomically(target, updated)
    except OSError as error:
        raise ToolError(
            f"path {path!r} could not be written", code=IO, detail=_detail(error)
        ) from error
    return f"edited {path} (replaced 1 match; {len(updated)} characters)"


def filesystem_tools(root: Path) -> list[Tool]:
    """Build the filesystem tools confined to `root`."""

    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")

    return [
        Tool(
            name="list_files",
            replay_safe=True,
            description=(
                "List what a directory in your workspace holds. Use this instead of ls "
                "or dir in run_command."
            ),
            returns="one line per entry, a directory with a / after its name; a long listing says how many more there are.",
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute path inside the workspace root, or a path relative to "
                            "that root. Defaults to the root."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
            run=lambda path=".": _list_files(resolved, path),
        ),
        Tool(
            name="read_file",
            replay_safe=True,
            description=(
                "Read a file in your workspace: a text file as text, an image (png, jpg, "
                "webp) as a picture you look at. Use this instead of cat, head, tail or "
                "type in run_command."
            ),
            returns=(
                "the text, in pages: the end of a page says which offset to ask for next; "
                "for an image, the picture itself."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute path inside the workspace root, or a path relative to it."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Character offset to continue a long file from. Defaults to 0.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            run=lambda path, offset=0: _read_file(resolved, path, int(offset)),
        ),
        Tool(
            name="write_file",
            description=(
                "Write a UTF-8 text file in your workspace, replacing it if it exists. "
                "Missing directories are created. Use this instead of echo, cat or a "
                "heredoc in run_command. Give `path` first and `content` last. `content` "
                "is the exact bytes of the file and nothing else: never wrap it in a "
                "markdown code fence and never add ``` before or after it."
            ),
            returns="the path written and its size; a refusal names why.",
            leaves="the file, at that path in your workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute path inside the workspace root, or a path relative to it."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The complete new contents of the file, with no markdown "
                            "fence around them."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            run=lambda path, content: _write_file(resolved, path, content),
            mutates=True,
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace one exact, unique text fragment in an existing UTF-8 file in your "
                "workspace. Use this instead of sed or awk in run_command. `old_text` must "
                "occur exactly once: include enough surrounding lines to make it unique."
            ),
            returns=(
                "that the replacement was made; when `old_text` occurs zero or several "
                "times, a refusal with the count, and the file is untouched."
            ),
            leaves="the changed file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Absolute path inside the workspace root, or a path relative to it."
                        ),
                    },
                    "old_text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Exact text that must occur once in the file.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text; may be empty to delete the match.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            run=lambda path, old_text, new_text: _edit_file(
                resolved, path, old_text, new_text
            ),
            mutates=True,
        ),
    ]
