"""The context size a person chose, kept in their own workspace.

A marker file, like the plan switch: it survives a restarted worker, is the
same in every interface, and the person can see it beside `AGENTS.md`. The
sizes are token counts (the human, 2026-09-13: small 128K, normal 256K,
large 512K), clamped by the runtime to the window the model reports, so a
choice can ask for less than the model allows and never for more.
"""

from __future__ import annotations

from pathlib import Path

CONTEXT_CHOICE = Path(".agent") / "context"

SIZES: dict[str, int] = {"small": 131_072, "normal": 262_144, "large": 524_288}
DEFAULT_SIZE = "normal"


def context_choice(workspace: Path | str) -> str:
    """Never raises: an unreadable or unknown marker is the default."""

    try:
        chosen = (Path(workspace) / CONTEXT_CHOICE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return DEFAULT_SIZE
    return chosen if chosen in SIZES else DEFAULT_SIZE


def set_context_choice(workspace: Path | str, size: str) -> None:
    if size not in SIZES:
        raise ValueError(f"not a context size: {size!r}")
    marker = Path(workspace) / CONTEXT_CHOICE
    if size == DEFAULT_SIZE:
        marker.unlink(missing_ok=True)
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{size}\n", encoding="utf-8")


def tokens_of(size: str) -> int:
    return SIZES.get(size, SIZES[DEFAULT_SIZE])


def budget_of(size: str, window: int | None) -> int:
    """The size, or the window when the window is smaller."""

    chosen = tokens_of(size)
    return min(chosen, window) if window else chosen
