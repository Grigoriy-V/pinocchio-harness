"""The folder a conversation works in, chosen by the person.

A code agent works in a project: Claude Code in the directory it was started
from, Codex in its workspace root. Here the person names one per
conversation (`/workspace <path>`), and a new conversation starts in the
last one named. Kept as a file in the person's own workspace beside the
other switches (`.agent/`), so it survives a restart and is the same in
every interface; nothing about it lives in the project folder itself.

The personal workspace stays what it is: where `inbox/` receives sent files
and where the switches live. The folder is where the tools work.
"""

from __future__ import annotations

import json
from pathlib import Path

FOLDERS = Path(".agent") / "folders.json"
LAST = "last"


def _read(workspace: Path | str) -> dict[str, str]:
    try:
        data = json.loads((Path(workspace) / FOLDERS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(workspace: Path | str, data: dict[str, str]) -> None:
    path = Path(workspace) / FOLDERS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def folder_of(workspace: Path | str, thread_id: str) -> Path | None:
    """The folder this conversation works in, or `None` when none was named
    or the named one is gone."""

    chosen = _read(workspace).get(thread_id)
    if not chosen:
        return None
    path = Path(chosen)
    return path if path.is_dir() else None


def last_folder(workspace: Path | str) -> Path | None:
    """The folder named most recently in any conversation."""

    chosen = _read(workspace).get(LAST)
    if not chosen:
        return None
    path = Path(chosen)
    return path if path.is_dir() else None


def set_folder(workspace: Path | str, thread_id: str, folder: Path | str) -> Path:
    """Name the folder a conversation works in. It must exist and be a
    directory, named absolutely: a relative path would mean a different
    place from every process."""

    path = Path(folder)
    if not path.is_absolute():
        raise ValueError(f"the folder must be an absolute path: {folder!r}")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"not a folder: {folder!r}")
    data = _read(workspace)
    data[thread_id] = str(path)
    data[LAST] = str(path)
    _write(workspace, data)
    return path


def clear_folder(workspace: Path | str, thread_id: str) -> None:
    """Back to the personal workspace for this conversation."""

    data = _read(workspace)
    if data.pop(thread_id, None) is not None:
        _write(workspace, data)
