"""What the model is actually sent: the surface of a canonical history.

Layers, in the order sent and by how rarely each one changes:

1. The core prompt and the capability brief, stable per grant.
2. The person's standing instructions, changed when they decide to.
3. A rolling summary of everything folded, changed at every fold.
4. Stored history, verbatim except where this module shortens it.
5. The facts retrieved for this turn, changed every turn — last among the
   stable layers so a served prefix cache survives everything above them.
6. The current turn.

Everything here is a projection (`DECISIONS.md` 2026-08-30): the store keeps
the whole conversation and nothing in this module writes back to it. A
message leaves the verbatim window only once the summary covers it, and a
tool result older than the newest few is shown as a stub that says how to
get the full text again — shortened on the surface, whole in history.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from app.instructions import instruction_message
from app.models import ContentPart, Message

# How many media items of each kind one request may carry. This mirrors the
# served model's own per-prompt limits — `MM_LIMITS` in `deploy/modal/model_app.py`
# — and is duplicated rather than imported because the application never depends
# on a deployment. A model served with different limits needs this changed too;
# exceeding them is an HTTP 400, not a degraded answer.
MEDIA_BUDGET = {"image": 4, "audio": 1}

# The core names no tool, no file format and no workflow, and it is meant to
# stay this short. Anything true only because a particular capability is wired
# up is generated from that wiring in `app/capabilities.py`, and anything true
# only for one person is their own standing instructions. What is left is what
# has to hold whatever this assistant is given — which is also why this text is
# the most stable layer of the prompt and goes first, ahead of everything that
# changes per grant, per person and per turn.
# How an agent works, whatever model is behind it (the human's ask,
# 2026-09-04, after a live turn that assumed what was installed, rewrote a
# script six times against errors that named their own fix, and handed over a
# document it had not opened). Written as a method, not as a list of cases:
# nothing here names a tool, a file type or a library, so it applies to the
# next kind of work as much as to the one that prompted it. Whether it changes
# what the model does is measured with the scenario suite, never assumed.
WORKING_METHOD = (
    "How to work. You are an agent, not an oracle: what you do not know about the "
    "place you work in, you find out with a tool before you assume it — which files "
    "are there, what is installed, where something lives, how a library is actually "
    "called. Look before you write, and read what came back before you write again. "
    "Prefer what is already there over installing something new. Check every step's "
    "result against what you meant: run what you made, open what you produced and "
    "look at it, and only then hand it over or call it done. An error message names "
    "its cause; fix that one thing rather than starting over. Take steps small enough "
    "to check. Never claim what you have not seen: if you did not run it, open it or "
    "read it, say so. "
    # Persistence, as Codex's prompt has it and ours did not (2026-09-07, ISS-0059:
    # a screenshot sent mid-turn was answered and the task paused on "continue?").
    # Literal conditions: what a message during the work is, and when to stop.
    "Finish the task in this turn: do not stop to ask whether to continue. A "
    "message from the person that arrives while you work is a comment on the work "
    "in progress: answer it, then continue the task. Stop only if the message says "
    "to stop or changes the task."
)

DEFAULT_SYSTEM_PROMPT = (
    "You are a general-purpose assistant with tools. What you can actually do is "
    "listed below, generated from what is wired up rather than written from memory: "
    "trust that list about yourself. After it may come standing instructions from "
    "the person you are talking to, saying how they want you to work; follow them "
    "wherever they do not contradict what is above them. "
    "Text you write together with a tool call reaches the person at once. After "
    "the tool's result, add only what is new; if nothing is new, say nothing. "
    "Answer briefly.\n\n" + WORKING_METHOD
)


@dataclass(frozen=True)
class ContextPolicy:
    """How much conversation stays verbatim, and when the rest is folded away.

    `max_input_tokens` is the size a request may reach before the conversation
    is folded, and is resolved at runtime from the model's own limit rather than
    configured here. `None` means the size is unknown and only the message
    counts bound the request.
    """

    # How many of the newest exchanges — a person's message and everything
    # the assistant did up to the next one — always stay verbatim. A floor,
    # not a trigger: folding is decided by size, and how much is folded by
    # how much has to go. Two is the exchange being answered and the one it
    # follows. Until 2026-09-04 this was `keep_recent = 8` messages, a count
    # that was two short sentences in one conversation and half a window of
    # tool results in another.
    keep_turns: int = 2
    retrieved_facts: int = 5
    # How many of the newest tool results in *stored history* a request
    # carries verbatim. Older ones are shown as stubs: the model has already
    # said what it made of them, and can read them back. The turn in progress
    # is never shortened (ISS-0041): its results are what the model is working
    # on, and its size is the size fold's business.
    keep_results: int = 2
    max_input_tokens: int | None = None


@dataclass(frozen=True)
class Surface:
    """One request as the model will see it, by layer, with what was done to it.

    The layers are kept apart so a trace can say how large each one is and a
    person can be shown the same numbers. `messages` is what is sent.
    """

    prelude: list[Message]
    history: list[Message]
    facts: list[Message]
    turn: list[Message]
    stubbed: int = 0
    placeholders: int = 0

    @property
    def messages(self) -> list[Message]:
        return [*self.prelude, *self.history, *self.facts, *self.turn]


@dataclass(frozen=True)
class Context:
    """One turn's assembled context, kept apart from the turn itself.

    `prelude` and `facts` are synthetic and must never be written back to the
    store; `history` is already stored. Only the new messages of the turn are
    new. `facts` sit behind history rather than in the prelude because they
    change every turn and everything sent after them is re-prefilled.
    """

    prelude: list[Message] = field(default_factory=list)
    history: list[Message] = field(default_factory=list)
    facts: list[Message] = field(default_factory=list)
    keep_results: int = 2
    # The stored position of `history[0]`, so a stub can say where the whole
    # result is. The turn's own messages have no position yet.
    first_position: int = 0

    def surface(self, new: Sequence[Message]) -> Surface:
        """The request, shortened on the surface only.

        The media budget is one prompt's, whichever turn a picture arrived
        in. Tool results are shortened in stored history only: the turn in
        progress is shown whole, because what a tool said back is why the
        model does what it does next, and until the turn ends it has not said
        what it made of it (ISS-0041: with its tracebacks stubbed, the model
        repeated the first attempt's error at the fourth). The person's own
        words are never touched.
        """

        combined = [*self.history, *new]
        combined, stubbed = shortened(
            combined, self.keep_results, stored=len(self.history), base=self.first_position
        )
        combined, placeholders = within_media_budget(combined, dict(MEDIA_BUDGET))
        split = len(self.history)
        return Surface(
            prelude=list(self.prelude),
            history=combined[:split],
            facts=list(self.facts),
            turn=combined[split:],
            stubbed=stubbed,
            placeholders=placeholders,
        )

    def prompt(self, new: Sequence[Message]) -> list[Message]:
        return self.surface(new).messages


def system(text: str) -> Message:
    return Message(role="system", content=[ContentPart(kind="text", text=text)])


def describe(part: ContentPart) -> str:
    if part.kind == "text":
        return part.text or ""
    return f"[{part.kind} {part.media_type}]"


def count_media(messages: Sequence[Message]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        for part in message.content:
            if part.kind != "text" and not part.outbound:
                counts[part.kind] = counts.get(part.kind, 0) + 1
    return counts


# A tool result shorter than this is left alone wherever it is: the stub
# would not be much shorter, and a one-line result is usually the point.
STUB_MIN_CHARS = 200


def shortened(
    messages: Sequence[Message], keep: int, stored: int | None = None, base: int = 0
) -> tuple[list[Message], int]:
    """Stored tool results older than the newest `keep` become stubs.

    A stub names the tool, what it was asked about, the size of what it said
    and the way back: the first `stored` messages (all of them, by default)
    are in history at positions from `base`, and a stub names the position
    for `read_history`. The rest are this turn's, not stored yet, and are
    never shortened — they are what the model is working on, and a stub that
    says "call the tool again" is, for a command, "run the failing script
    again" (ISS-0041). Failures are kept too: they are short, and they are why
    the model did what it did next. The count is what a trace reports.

    The model's own words — its text and the arguments of its calls — are
    never shortened. They were, for one deployed afternoon (run `a459c70e`,
    2026-09-03): with the content of its earlier `write_file` shown as
    `<1104 characters, shortened>`, the model wrote every file again, three
    times round, and never reached the screenshot. What it wrote is what it
    remembers doing; what a tool said back is what it can ask for again.
    """

    if stored is None:
        stored = len(messages)
    results = [index for index, message in enumerate(messages) if message.role == "tool"]
    old = {index for index in results[: max(0, len(results) - keep)] if index < stored}
    if not old:
        return list(messages), 0
    calls = {
        call.id: call
        for message in messages
        for call in message.tool_calls
    }
    out: list[Message] = []
    count = 0
    for index, message in enumerate(messages):
        if index in old and message.failure is None:
            text = "".join(part.text or "" for part in message.content if part.kind == "text")
            media = [part for part in message.content if part.kind != "text"]
            if len(text) > STUB_MIN_CHARS or media:
                call = calls.get(message.tool_call_id or "")
                out.append(
                    replace(message, content=[ContentPart(kind="text", text=stub(call, text, media, base + index))])
                )
                count += 1
                continue
        out.append(message)
    return out, count


def stub(call, text: str, media: Sequence[ContentPart], position: int) -> str:
    what = call.name if call is not None else "tool"
    about = ""
    if call is not None:
        for value in call.arguments.values():
            if isinstance(value, str) and value and len(value) <= 80:
                about = f" {value}"
                break
    size = f"{len(text)} characters" if text else ""
    if media:
        kinds = ", ".join(f"{part.kind}" for part in media)
        size = f"{size}, {kinds}" if size else kinds
    # Only where the result is. The first wording offered "or call the tool
    # again for a fresh one" as well, and live (run `live-90`, 2026-09-03)
    # the model took that, found the file gone, and never came back for the
    # stored words. It can call any tool anyway; the stub's one job is to
    # say where the whole result is.
    return f"[{what}{about}: {size}; shortened — the full result is stored: read_history {position}]"


def within_media_budget(
    history: Sequence[Message], budget: dict[str, int]
) -> tuple[list[Message], int]:
    """Replay recent media, but only as much of it as one prompt may carry.

    A server caps how many items of each kind a single prompt may contain, and
    the whole conversation is re-sent every turn. Without a budget the second
    voice message in a thread is refused outright, for a reason that has nothing
    to do with what the person asked — that happened. Older media past the cap
    becomes the same placeholder summaries use, so the model still knows a voice
    message or a picture was there.

    The newest media survives: it is the one a follow-up question is about.
    """

    kept: list[Message] = []
    remaining = dict(budget)
    placeholders = 0
    for message in reversed(history):
        if all(part.kind == "text" for part in message.content):
            kept.append(message)
            continue
        content: list[ContentPart] = []
        for part in message.content:
            if part.kind == "text":
                content.append(part)
            elif part.outbound:
                content.append(ContentPart(kind="text", text=describe(part)))
            elif remaining.get(part.kind, 0) > 0:
                remaining[part.kind] -= 1
                content.append(part)
            else:
                content.append(ContentPart(kind="text", text=describe(part)))
                placeholders += 1
        kept.append(replace(message, content=content))
    kept.reverse()
    return kept, placeholders


def transcript(messages: Sequence[Message]) -> str:
    """Render messages as plain text for summarization.

    Media becomes a placeholder: a summary of a picture is the model's job, not
    a base64 blob's.
    """

    lines = []
    for message in messages:
        body = " ".join(describe(part) for part in message.content).strip()
        for call in message.tool_calls:
            body = f"{body} [calls {call.name}({call.arguments})]".strip()
        lines.append(f"{message.role}: {body}")
    return "\n".join(lines)


def turn_boundary(messages: Sequence[Message], start: int) -> int:
    """Move a cut forward to the next place a step begins.

    Cutting between an assistant's tool call and the tool's reply would leave
    an orphan result the provider rejects, so a cut lands before a user
    message or before an assistant message — never before a tool result. A
    cut inside a long tool-using turn is allowed on purpose: on 2026-09-03 a
    thread whose newest 26 messages were one turn's calls and results could
    not be folded at all, and `/compact` said there was nothing to fold.

    A negative `start` means the caller wanted to keep more messages than exist;
    it is clamped rather than left to index from the end of the list.
    """

    for index in range(max(0, start), len(messages)):
        if messages[index].role in ("user", "assistant"):
            return index
    return len(messages)


def build_prelude(
    summary: str | None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    instructions: str = "",
) -> list[Message]:
    """The stable synthetic layers, ordered by how rarely each one changes.

    The system message is the same for weeks, a person's standing instructions
    change when they decide to, the summary changes when a conversation is
    folded. A layer that changes invalidates the served prefix cache for
    everything after it, so the ones that change least go first — and the
    retrieved facts, which change every turn, are not here at all: see
    `facts_layer`, sent after history.
    """

    prelude = [system(system_prompt)]
    overlay = instruction_message(instructions)
    if overlay is not None:
        prelude.append(overlay)
    if summary:
        # The summary is a projection and may have lost a detail; the words
        # behind it are stored and reachable, and the model is told so here
        # rather than asked to keep more of them.
        prelude.append(
            system(
                f"Summary of the earlier conversation:\n{summary}\n\n"
                "The exact words behind this summary are kept: search_history finds "
                "them, read_history returns them."
            )
        )
    return prelude


def facts_layer(facts: Sequence[str]) -> list[Message]:
    """The facts retrieved for this turn, as one system message or none.

    Sent after history and before the turn: they were retrieved for the
    question that follows, and they are the one layer that changes on every
    message, so nothing that could be cached is sent behind them.
    """

    if not facts:
        return []
    listed = "\n".join(f"- {fact}" for fact in facts)
    return [system(f"Facts you saved in earlier conversations:\n{listed}")]
