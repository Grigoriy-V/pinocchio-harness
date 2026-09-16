"""Three ways a conversation may run: `full`, `careful`, or `plan`.

In `full`, the default, everything inside the workspace runs without a
question — reading, writing, running a command — and only effects beyond it
(another person, another system, money, infrastructure) ask first. In
`careful`, the tools that change the workspace ask too, through the same
approval path. In `plan` (roadmap 32; Claude Code's plan mode, OpenClaw's
`read-only`) the tools that change or run anything are not offered at all:
the model reads, searches and looks, and its answer is the plan. Claude
Code's permission modes, in one word: a tool declares that it `mutates`, the
toolbox reads the mode when it decides what needs a yes or is withheld, and
nothing else in the loop knows the mode exists.

Kept the way the other switches are kept: a marker in the person's workspace
(`.agent/mode`, the word), read when the next turn's toolbox is built, so a
change takes effect from the next message and is the same in every
interface. `DECISIONS.md` 2026-09-04, 2026-09-17.
"""

from __future__ import annotations

from pathlib import Path

MODE_FILE = Path(".agent") / "mode"
# The marker `careful` was kept as until 2026-09-17; still read, so a person
# who switched before then is still in careful mode.
CAREFUL_SWITCH = Path(".agent") / "careful.on"
MODES = ("full", "careful", "plan")


def current_mode(workspace: Path | str) -> str:
    """Never raises: an unreadable marker is the default, `full`."""

    try:
        word = (Path(workspace) / MODE_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        word = ""
    if word in MODES:
        return word
    try:
        return "careful" if (Path(workspace) / CAREFUL_SWITCH).is_file() else "full"
    except OSError:
        return "full"


def careful_enabled(workspace: Path | str) -> bool:
    return current_mode(workspace) == "careful"


def plan_enabled(workspace: Path | str) -> bool:
    return current_mode(workspace) == "plan"


def set_mode(workspace: Path | str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
    marker = Path(workspace) / MODE_FILE
    (Path(workspace) / CAREFUL_SWITCH).unlink(missing_ok=True)
    if mode == "full":
        marker.unlink(missing_ok=True)
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{mode}\n", encoding="utf-8")
