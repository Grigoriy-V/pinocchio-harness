"""Explicitly present a workspace file to the person.

Observation and presentation are separate actions. Reading, rendering or
inspecting a file gives evidence to the agent; only this tool marks a concrete
item as outbound. Interfaces translate that mark to their own transport.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Sequence
from pathlib import Path

from app.attachments import MEDIA_KINDS
from app.models import ContentPart
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError
from app.tools.filesystem import NOT_A_FILE, NOT_FOUND, TOO_LARGE, resolve_in_root

MAX_OUTBOUND_BYTES = 50 * 1024 * 1024

# The one failure that is this family's own: there is nothing to deliver.
EMPTY = "presentation.empty"


MAX_ITEMS = 10  # Telegram's album limit, and more than anyone hands over at once


def send_file(
    root: Path, path: str | None = None, paths: Sequence[str] | None = None
) -> list[ContentPart]:
    """Return the explicit outbound items selected from the granted workspace.

    One path or several: several is one call for the files of one app, where
    live on 2026-09-03 the model spent a model call on each. The items ride in
    one result, and an interface may deliver them as one album.
    """

    chosen = [path] if path else list(paths or [])
    if not chosen:
        raise ToolError("send_file needs a path, or paths", code=BAD_ARGUMENTS)
    if len(chosen) > MAX_ITEMS:
        raise ToolError(f"send_file takes at most {MAX_ITEMS} paths in one call", code=BAD_ARGUMENTS)
    parts = [_outbound(root, one) for one in chosen]
    names = ", ".join(part.name or "" for part in parts)
    return [
        ContentPart(kind="text", text=f"Selected {names} for delivery to the person."),
        *parts,
    ]


def _outbound(root: Path, path: str) -> ContentPart:
    target = resolve_in_root(root, path)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_file():
        raise ToolError(f"path {path!r} is not a file", code=NOT_A_FILE)
    size = target.stat().st_size
    if size == 0:
        raise ToolError(f"{target.name} is empty", code=EMPTY)
    if size > MAX_OUTBOUND_BYTES:
        raise ToolError(
            f"{target.name} is larger than the {MAX_OUTBOUND_BYTES // (1024 * 1024)} MB "
            "delivery limit",
            code=TOO_LARGE,
        )
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    kind = MEDIA_KINDS.get(media_type, "file")
    return ContentPart(
        kind=kind,
        data=target.read_bytes(),
        media_type=media_type,
        name=target.name,
        outbound=True,
    )


def presentation_tools(root: Path) -> list[Tool]:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")
    return [
        Tool(
            name="send_file",
            description=(
                "Send one or more files from your workspace to the person. This is the "
                "only way anything but your text reaches them: a file you wrote, a "
                "screenshot you took, a page you rendered stays in the workspace until "
                "you send it. Several files of one piece of work go in one call, as "
                "`paths`. Not a way to look at a file: it shows you nothing."
            ),
            returns="which files were delivered; a refusal names the file and why.",
            leaves="the files in the person's chat, as pictures, sound or attachments.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "One path inside the workspace.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Several paths inside the workspace, sent together.",
                    },
                },
                "additionalProperties": False,
            },
            run=lambda path=None, paths=None: send_file(resolved, path, paths),
            delivers=True,
        )
    ]
