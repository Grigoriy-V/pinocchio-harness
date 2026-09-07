"""The rolling summary: how older conversation is folded instead of dropped.

Folding is the only way a message leaves the verbatim window. The summary and
the position it covers move together, so a reader can always tell which
messages the summary accounts for.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.context.window import ContextPolicy, shortened, system, transcript, turn_boundary
from app.memory import ConversationStore
from app.models import ContentPart, Message, ModelBackend

# Structured rather than prose: a summary is read by a model that has to act
# on it, and what it acts on is the goal, what is already done, the names of
# things, and what is still open. Prose loses the file names first.
INSTRUCTION = (
    "You maintain a running summary of a conversation. Rewrite the summary so it "
    "covers the earlier summary and the new exchange together, at most {words} words, "
    "as four short sections: Goal (what the person wants), Done (what has been done, "
    "naming files, paths, numbers and decisions exactly as written), Open (questions "
    "or work not finished), Preferences (how the person wants things). Leave out a "
    "section that would be empty. Plain text, no preamble."
)

# How long a summary may be, from how much it is asked to cover. A fold by
# size takes in everything but the newest window at once — forty messages
# is ordinary — and two hundred words for forty messages loses the file
# names first. Fifteen words a message, from a floor, to a ceiling that is
# still a small share of any window.
WORDS_FLOOR = 150
WORDS_PER_MESSAGE = 15
WORDS_CEILING = 600


def summary_words(covered: int) -> int:
    return min(WORDS_CEILING, WORDS_FLOOR + WORDS_PER_MESSAGE * covered)


async def summarize(
    backend: ModelBackend,
    previous: str | None,
    messages: Sequence[Message],
    first_position: int = 0,
) -> str:
    """One summarizer call over the surface of `messages`, not their whole text.

    Every tool result the summarizer reads is the stub the model itself reads
    once a result is old: the tool, its subject, the size and the stored
    position. What a tool said back is not what the summary is for — the
    model's own next message already says what it made of it — and whole
    results made a fold the largest prefill of a conversation, and on a long
    tool turn a request that could exceed the window (ISS-0029, ISS-0030).
    A summary that carries positions is also one the model can follow with
    `read_history`.
    """

    surface, _ = shortened(messages, keep=0, stored=len(messages), base=first_position)
    body = transcript(surface)
    if previous:
        body = f"Earlier summary:\n{previous}\n\nNew exchange:\n{body}"
    instruction = INSTRUCTION.format(words=summary_words(len(messages)))
    completion = await backend.invoke(
        [system(instruction), Message(role="user", content=[ContentPart(kind="text", text=body)])]
    )
    return completion.text.strip()


# What a fold has to free beyond the overshoot itself: room for the summary
# that replaces the folded text, which grows with what it covers (at most
# `WORDS_CEILING` words, roughly this many tokens).
SUMMARY_ALLOWANCE = 800

# Inside one long tool-using exchange there is no earlier exchange to keep,
# so the floor is the newest steps instead: the result the model is reading
# and the one before it, the same two `keep_results` carries verbatim.
KEEP_STEPS = 2


def verbatim_floor(messages: Sequence[Message], keep_turns: int) -> int:
    """Where the part that always stays verbatim begins.

    The start of the `keep_turns`-th newest exchange, an exchange being a
    person's message and everything up to the next one. With fewer whole
    exchanges than that — one long tool turn — the floor is the start of the
    newest `KEEP_STEPS` assistant steps instead, so a turn of thirty calls
    can still be folded down to what the model is working on (2026-09-03:
    a 26-message turn could not be folded at all). Zero means nothing is
    older than the floor.
    """

    turns = [index for index, message in enumerate(messages) if message.role == "user"]
    if len(turns) >= keep_turns:
        return turns[-keep_turns]
    steps = [index for index, message in enumerate(messages) if message.role == "assistant"]
    if len(steps) >= KEEP_STEPS:
        return steps[-KEEP_STEPS]
    return 0


def cut_for(
    backend: ModelBackend,
    messages: Sequence[Message],
    floor: int,
    needed: int,
    first_position: int,
) -> int:
    """The earliest exchange boundary at or below `floor` whose folding frees `needed` tokens.

    Oldest first, one exchange at a time, on the surface the model actually
    carries (old results are stubs there): a request a little over budget
    folds one exchange, not the whole conversation. The floor is the answer
    when nothing short of it is enough.
    """

    boundaries = [
        index for index, message in enumerate(messages) if message.role == "user" and 0 < index < floor
    ]
    for boundary in boundaries:
        surface, _ = shortened(messages[:boundary], keep=0, stored=boundary, base=first_position)
        if backend.estimate_tokens(surface) >= needed:
            return boundary
    return floor


async def fold_older_messages(
    backend: ModelBackend,
    store: ConversationStore,
    thread_id: str,
    policy: ContextPolicy,
    used_tokens: int | None = None,
    force: bool = False,
    reason: str | None = None,
    excess: int | None = None,
) -> str | None:
    """Summarize older exchanges, as many as have to go. Returns the new summary.

    `excess` is how far over budget the request is, when the caller knows
    (`fitted`, from its estimate); with `used_tokens` it is derived. A fold
    by size folds the oldest exchanges until that much is freed and no more;
    a fold by count or on request (`/compact`) folds everything older than
    the verbatim floor.

    Two things can trigger a fold: a request that grew past
    `max_input_tokens`, or — as the fallback for a server that does not say
    how large a request may be — too many messages. The first is the rule:
    the decision of 2026-09-03 says the summary is spent only when the
    shortened surface is still above budget, and until ISS-0032 the count
    fired first in every conversation, folding every twelve messages with
    most of the window empty.

    `used_tokens` is what the model reported for the request just made, not an
    estimate of it. That makes the trigger exact and one turn late, which is why
    the budget is a fraction of the model's limit: the turn that overshoots the
    budget still fits, and the fold happens before the next one.

    Every fold leaves a record — what it covered and why — so a later reader
    can tell which messages the summary stands for and recover them exactly.
    `reason` names the trigger when the caller knows it better than this
    function does (`asked`, for `/compact`).
    """

    previous, through = store.summary(thread_id)
    pending = store.messages(thread_id, after=through - 1)
    oversized = (
        policy.max_input_tokens is not None
        and used_tokens is not None
        and used_tokens > policy.max_input_tokens
    )
    if not oversized and not force:
        return None
    if excess is None and oversized:
        excess = used_tokens - policy.max_input_tokens  # type: ignore[operator]

    floor = verbatim_floor(pending, policy.keep_turns)
    cut = (
        cut_for(backend, pending, floor, excess + SUMMARY_ALLOWANCE, through)
        if excess is not None and excess > 0
        else floor
    )
    cut = turn_boundary(pending, cut)
    if cut <= 0 or cut >= len(pending):
        return None

    updated = await summarize(backend, previous, pending[:cut], first_position=through)
    if not updated:
        return None
    store.set_summary(thread_id, updated, through + cut)
    store.record_compaction(
        thread_id,
        through=through + cut,
        folded=cut,
        trigger=reason or ("size" if oversized else "forced"),
        summary_chars=len(updated),
    )
    return updated
