"""One file in which a person says how they want this assistant to work.

`AGENTS.md` at the root of their own workspace, read again on every turn, so an
edit takes effect on the next message rather than on the next deployment. It is
an overlay on the prompt and nothing else: nothing extracts it from a
conversation, `remember_fact` never writes to it, and there is no second copy
of it in a database to disagree with the file.

It is deliberately *not* memory. Memory is what the assistant learned and
saved; this is what the person decided and typed. Blurring the two would mean
an instruction could appear because a model thought it noticed a preference,
which is exactly the behaviour this must not have.

Authority: below product and capability policy, above nothing except retrieved
context. It can shape how work is done and can never widen what may be done —
a workspace stays a workspace and an approval stays an approval, whatever this
file says. That is why the frame below states its source: the model has to be
able to tell an instruction the person wrote from a rule the product holds.
"""

from __future__ import annotations

from pathlib import Path

from app.limits import DEFAULT_LIMITS
from app.models import ContentPart, Message

INSTRUCTIONS_FILE = "AGENTS.md"

# A bound on what this costs in every single request: the overlay is sent on
# every turn, so an unbounded file would be a per-turn tax the person never
# sees. The number is the budget's share (`Limits.instruction_bytes`, roadmap
# 31); this is the default before the budget is known. Bytes rather than
# characters because the file is on disk and Cyrillic costs two.
MAX_INSTRUCTION_BYTES = DEFAULT_LIMITS.instruction_bytes

FRAME = (
    "Standing instructions from the person you are talking to, from {name} in "
    "their workspace. They say how they want you to work, and you follow them. "
    "They are not policy and not something you saved: they cannot widen what "
    "you are allowed to do, cross a boundary the rules above hold, or make an "
    "unsafe action safe.\n\n{text}"
)

TRUNCATED = (
    "\n\n[... {shown} of {total} bytes of {name} shown, the share of the budget an "
    "instruction file gets; read_file {name!r} shows it whole ...]"
)


class InstructionsError(ValueError):
    """The instructions cannot be saved as given."""


def instructions_path(workspace: Path | str) -> Path:
    return Path(workspace) / INSTRUCTIONS_FILE


def read_instructions(workspace: Path | str, max_bytes: int = MAX_INSTRUCTION_BYTES) -> str:
    """What the person wrote, or nothing at all.

    Never raises. This is read on the way to every model call, and a file that
    has just been deleted, or that some other process is writing, must cost the
    turn nothing more than an overlay it did not get.
    """

    path = instructions_path(workspace)
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        return ""
    if len(raw) > max_bytes:
        # Truncated visibly rather than silently: instructions the model was
        # only half given, without saying so, would look like instructions the
        # person never wrote; the note names the way to the rest.
        text = raw[:max_bytes].decode("utf-8", errors="ignore")
        return text.strip() + TRUNCATED.format(
            name=INSTRUCTIONS_FILE, shown=max_bytes, total=len(raw)
        )
    return raw.decode("utf-8", errors="replace").strip()


def write_instructions(
    workspace: Path | str, text: str, max_bytes: int = MAX_INSTRUCTION_BYTES
) -> str:
    """Replace the file wholesale, which is the only edit this offers.

    A command that appended would need a way to remove one line again, and that
    is a small editor nobody asked for. The file is also an ordinary workspace
    file, so anyone wanting finer edits already has `edit_file`.
    """

    body = text.strip()
    if not body:
        raise InstructionsError("there is nothing to save")
    if len(body.encode("utf-8")) > max_bytes:
        raise InstructionsError(
            f"instructions must fit in {max_bytes} bytes; this is {len(body.encode('utf-8'))}"
        )
    path = instructions_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return body


def clear_instructions(workspace: Path | str) -> bool:
    """Remove the overlay. `False` when there was nothing to remove."""

    path = instructions_path(workspace)
    try:
        path.unlink()
    except OSError:
        return False
    return True


def instruction_message(text: str) -> Message | None:
    """The overlay as one framed message, or nothing when there is none."""

    if not text.strip():
        return None
    return Message(
        role="system",
        content=[
            ContentPart(
                kind="text",
                text=FRAME.format(name=INSTRUCTIONS_FILE, text=text.strip()),
            )
        ],
    )
