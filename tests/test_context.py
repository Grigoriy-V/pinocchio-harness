"""The four context layers and the fold that keeps them bounded."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from app.context import Context, ContextPolicy, build_prelude, fold_older_messages, transcript
from app.context.window import (
    DEFAULT_SYSTEM_PROMPT,
    facts_layer,
    shortened,
    turn_boundary,
    system,
)
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import (
    Completion,
    CompletionDone,
    ContentPart,
    Message,
    ModelBackend,
    StreamEvent,
    TextDelta,
    ToolCall,
)


class EchoBackend(ModelBackend):
    """Returns a fixed summary and remembers what it was asked to summarize."""

    def __init__(self, summary: str = "they talked about files") -> None:
        self.summary = summary
        self.requests: list[list[Message]] = []

    async def invoke(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        self.requests.append(list(messages))
        return Completion(text=self.summary)

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta(self.summary)
        yield CompletionDone(Completion(text=self.summary))


def user(value: str) -> Message:
    return Message(role="user", content=[ContentPart(kind="text", text=value)])


def assistant(value: str) -> Message:
    return Message(role="assistant", content=[ContentPart(kind="text", text=value)])


def exchange(count: int) -> list[Message]:
    messages: list[Message] = []
    for index in range(count):
        messages.append(user(f"question {index}"))
        messages.append(assistant(f"answer {index}"))
    return messages


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


# --- assembly ----------------------------------------------------------------


def test_a_prelude_without_summary_or_facts_is_just_the_system_prompt() -> None:
    [message] = build_prelude(None)

    assert message.role == "system"
    assert message.content[0].text == DEFAULT_SYSTEM_PROMPT


def test_the_core_prompt_points_at_the_layers_below_it() -> None:
    """It cannot say what the assistant can do; it says where that is written.

    The rules about observing before describing moved to the capability that
    owns the observing — `tests/test_capability_brief.py` asserts them there,
    where they appear only for an agent that actually has the tool.
    """

    assert "generated from what is wired up" in DEFAULT_SYSTEM_PROMPT
    # The sentence about standing instructions left on 2026-09-07: the
    # overlay's own frame says it (`tests/test_instructions.py`).
    assert "standing instructions" not in DEFAULT_SYSTEM_PROMPT


def test_the_summary_and_the_facts_become_readable_layers() -> None:
    prelude = build_prelude("earlier they discussed cats")
    [facts] = facts_layer(["The human has two cats"])

    bodies = [message.content[0].text for message in prelude]
    assert all(message.role == "system" for message in prelude)
    assert "earlier they discussed cats" in bodies[1]
    assert facts.role == "system"
    assert "- The human has two cats" in facts.content[0].text
    assert facts_layer([]) == []


def test_the_prompt_is_prelude_history_facts_then_the_new_turn() -> None:
    """Facts change every turn, so they go behind everything that does not:
    a served prefix cache survives up to the first layer that changed."""

    context = Context(
        prelude=[system("rules")],
        history=[user("old"), assistant("older answer")],
        facts=[system("facts")],
    )

    prompt = context.prompt([user("new")])

    assert [m.content[0].text for m in prompt] == ["rules", "old", "older answer", "facts", "new"]


# --- shortening on the surface ------------------------------------------------


def call(identifier: str, name: str = "read_file", **arguments: Any) -> Message:
    return Message(
        role="assistant",
        tool_calls=(ToolCall(id=identifier, name=name, arguments=arguments),),
    )


def result(identifier: str, text: str) -> Message:
    return Message(
        role="tool", tool_call_id=identifier, content=[ContentPart(kind="text", text=text)]
    )


def test_old_tool_results_become_stubs_and_the_newest_stay_whole() -> None:
    """Run `9c42241c`, 2026-09-03: twelve steps, every earlier result re-sent
    each time. Run `a459c70e` the same day: shortening the model's own file
    arguments as well made it write every file again, so those stay whole."""

    messages = [
        user("build it"),
        call("c1", "write_file", path="a.html", content="x" * 1000),
        result("c1", "created a.html (1000 characters); " + "y" * 300),
        call("c2", path="b.html"),
        result("c2", "z" * 500),
        call("c3", path="c.html"),
        result("c3", "w" * 500),
    ]

    surface, count = shortened(messages, keep=2)

    assert count == 1, "one result; the model's own call arguments are never touched"
    assert surface[1].tool_calls[0].arguments == {"path": "a.html", "content": "x" * 1000}
    first = surface[2].content[0].text
    assert first.startswith("[write_file a.html: 334 characters; shortened")
    assert "read_history 2" in first
    assert surface[4].content[0].text == "z" * 500
    assert surface[6].content[0].text == "w" * 500
    assert messages[2].content[0].text.endswith("y" * 300), "history itself is untouched"


def test_short_results_and_failures_are_never_stubbed() -> None:
    from app.tools.base import ToolFailure

    messages = [
        call("c1"),
        result("c1", "pong"),
        call("c2"),
        Message(
            role="tool",
            tool_call_id="c2",
            content=[ContentPart(kind="text", text="e" * 400)],
            failure=ToolFailure(code="io", message="e" * 400),
        ),
        call("c3"),
        result("c3", "k" * 400),
    ]

    surface, count = shortened(messages, keep=1)

    assert count == 0
    assert [m.content[0].text for m in surface if m.role == "tool"] == ["pong", "e" * 400, "k" * 400]


def test_an_old_screenshot_in_a_result_becomes_a_placeholder() -> None:
    shot = Message(
        role="tool",
        tool_call_id="c1",
        content=[
            ContentPart(kind="text", text="screenshot: a.png"),
            ContentPart(kind="image", data=b"png", media_type="image/png"),
        ],
    )
    messages = [call("c1", "inspect_page", path="a.html"), shot, call("c2"), result("c2", "fine")]

    surface, count = shortened(messages, keep=1)

    assert count == 1
    assert [part.kind for part in surface[1].content] == ["text"]
    assert surface[1].content[0].text.startswith("[inspect_page a.html: 17 characters, image; shortened")


def test_the_surface_counts_what_it_did_and_keeps_the_layers_apart() -> None:
    context = Context(
        prelude=[system("rules")],
        history=[user("old"), call("c1"), result("c1", "o" * 300)],
        facts=[system("facts")],
        keep_results=1,
    )

    new = [user("new"), call("c2"), result("c2", "n" * 300)]
    surface = context.surface(new)

    assert surface.stubbed == 1
    assert [m for m in surface.history if m.role == "tool"][-1].content[0].text.startswith("[read_file")
    assert surface.turn[-1].content[0].text == "n" * 300
    assert surface.messages == context.prompt(new)


def voice(data: bytes = b"OggS") -> Message:
    return Message(
        role="user", content=[ContentPart(kind="audio", data=data, media_type="audio/ogg")]
    )


def test_a_second_voice_message_does_not_replay_the_first() -> None:
    """The served model accepts one audio per prompt; two is an HTTP 400."""

    context = Context(history=[voice(b"first")])

    prompt = context.prompt([voice(b"second")])

    assert [part.kind for part in prompt[0].content] == ["text"]
    assert prompt[0].content[0].text == "[audio audio/ogg]"
    assert prompt[1].content[0].data == b"second"


def test_the_turn_s_own_pictures_share_the_budget_newest_first() -> None:
    """Test 8, 2026-09-03: two screenshots of one turn re-sent on every step.
    The budget is one prompt's, whichever turn a picture arrived in."""

    def picture(tag: bytes) -> Message:
        return Message(role="user", content=[ContentPart(kind="image", data=tag, media_type="image/png")])

    context = Context(history=[picture(b"h1"), picture(b"h2")])

    prompt = context.prompt([picture(b"t1"), picture(b"t2"), picture(b"t3")])

    kept = [m.content[0].data for m in prompt if m.content[0].kind == "image"]
    assert kept == [b"h2", b"t1", b"t2", b"t3"]
    assert prompt[0].content[0].text == "[image image/png]"


def test_a_stored_voice_message_is_replayed_when_the_new_turn_is_text() -> None:
    """The budget is spent by the turn, not by the calendar: text asks nothing."""

    context = Context(history=[voice(b"first")])

    prompt = context.prompt([user("what did I just say?")])

    assert prompt[0].content[0].data == b"first"


def test_pictures_past_the_budget_become_placeholders_newest_first() -> None:
    def picture(tag: bytes) -> Message:
        return Message(role="user", content=[ContentPart(kind="image", data=tag, media_type="image/png")])

    history = [picture(bytes([index])) for index in range(6)]
    context = Context(history=history)

    prompt = context.prompt([user("compare them")])

    kept = [m.content[0].data for m in prompt if m.content[0].kind == "image"]
    assert kept == [bytes([2]), bytes([3]), bytes([4]), bytes([5])]


def test_a_past_turn_keeps_its_text_beside_the_placeholder() -> None:
    asked = Message(
        role="user",
        content=[
            ContentPart(kind="text", text="what is this?"),
            ContentPart(kind="audio", data=b"OggS", media_type="audio/ogg"),
        ],
    )
    context = Context(history=[asked])

    recalled_turn, _new = context.prompt([voice()])

    assert [part.text for part in recalled_turn.content] == ["what is this?", "[audio audio/ogg]"]


def test_a_text_only_history_turn_is_left_untouched() -> None:
    original = user("old")
    context = Context(history=[original])

    [recalled_turn] = context.prompt([])

    assert recalled_turn is original


def test_the_context_does_not_mutate_when_a_prompt_is_built() -> None:
    context = Context(prelude=[system("rules")], history=[user("old")])

    context.prompt([user("new")])

    assert len(context.history) == 1


# --- rendering ---------------------------------------------------------------


def test_a_transcript_names_media_instead_of_carrying_it() -> None:
    message = Message(
        role="user",
        content=[
            ContentPart(kind="text", text="what is this"),
            ContentPart(kind="image", data=b"\x89PNG", media_type="image/png"),
        ],
    )

    assert transcript([message]) == "user: what is this [image image/png]"


def test_a_transcript_shows_which_tool_was_called() -> None:
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})

    rendered = transcript([Message(role="assistant", tool_calls=(call,))])

    assert "calls read_file" in rendered


# --- where a cut may land ----------------------------------------------------


def test_a_cut_moves_forward_to_the_start_of_a_turn() -> None:
    messages = [
        user("ask"),
        Message(role="assistant", tool_calls=(ToolCall(id="c1", name="t", arguments={}),)),
        Message(role="tool", content=[ContentPart(kind="text", text="r")], tool_call_id="c1"),
        user("ask again"),
    ]

    assert turn_boundary(messages, 1) == 1, "before the call, with its result behind it"
    assert turn_boundary(messages, 2) == 3, "never between the call and its result"


def test_a_cut_with_no_later_turn_lands_at_the_end() -> None:
    assert turn_boundary([user("only")], 1) == 1


async def test_a_long_tool_turn_can_still_be_folded(store: SqliteStore) -> None:
    """2026-09-03: a thread whose tail was one 26-message tool turn had no user
    boundary to cut at, so `/compact` folded nothing."""

    messages = [user("build it")]
    for index in range(12):
        messages.append(
            Message(role="assistant", tool_calls=(ToolCall(id=f"c{index}", name="t", arguments={}),))
        )
        messages.append(
            Message(role="tool", content=[ContentPart(kind="text", text="r")], tool_call_id=f"c{index}")
        )
    store.append("t1", messages, LOCAL_USER_ID)

    folded = await fold_older_messages(
        EchoBackend(), store, "t1", ContextPolicy(keep_turns=2), force=True
    )

    assert folded == "they talked about files"
    _, through = store.summary("t1")
    assert through == 21, "the newest two steps stay: one exchange has no earlier one to keep"
    assert store.messages("t1", after=through - 1)[0].role == "assistant"


def test_a_cut_before_the_first_message_is_clamped_not_taken_from_the_end() -> None:
    """A caller asking to keep more messages than exist passes a negative start.

    Python would read that as an index from the end; here it means the beginning.
    """

    assert turn_boundary([user("only")], -4) == 0
    assert turn_boundary([], -4) == 0


# --- folding -----------------------------------------------------------------


async def test_a_short_thread_is_not_summarized(store: SqliteStore) -> None:
    backend = EchoBackend()
    store.append("t1", exchange(3), LOCAL_USER_ID)

    result = await fold_older_messages(backend, store, "t1", ContextPolicy())

    assert result is None
    assert backend.requests == []
    assert store.summary("t1") == (None, 0)


async def test_a_long_thread_is_folded_and_the_summary_records_its_reach(
    store: SqliteStore,
) -> None:
    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(EchoBackend(), store, "t1", ContextPolicy(), force=True)

    summary, through = store.summary("t1")
    assert summary == "they talked about files"
    assert 0 < through < 24
    [record] = store.compactions("t1")
    assert (record.through, record.folded, record.trigger) == (through, through, "forced")
    assert record.summary_chars == len("they talked about files")


async def test_folding_leaves_the_recent_window_verbatim(store: SqliteStore) -> None:
    policy = ContextPolicy(keep_turns=2)
    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(EchoBackend(), store, "t1", policy, force=True)

    _, through = store.summary("t1")
    remaining = store.messages("t1", after=through - 1)
    assert sum(1 for m in remaining if m.role == "user") == policy.keep_turns
    assert remaining[0].role == "user"


async def test_the_summarizer_is_shown_the_older_messages_not_the_recent_ones(
    store: SqliteStore,
) -> None:
    backend = EchoBackend()
    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(backend, store, "t1", ContextPolicy(), force=True)

    [request] = backend.requests
    body = request[-1].content[0].text
    assert "question 0" in body
    assert "question 11" not in body


async def test_folding_twice_carries_the_earlier_summary_forward(store: SqliteStore) -> None:
    backend = EchoBackend()
    store.append("t1", exchange(12), LOCAL_USER_ID)
    await fold_older_messages(backend, store, "t1", ContextPolicy(), force=True)
    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(backend, store, "t1", ContextPolicy(), force=True)

    body = backend.requests[1][-1].content[0].text
    assert "Earlier summary:" in body


# --- folding because the request grew, not because it got long ---------------


async def test_a_short_thread_that_filled_the_request_is_folded(store: SqliteStore) -> None:
    """Eight turns of text and eight turns of images are the same message count."""

    policy = ContextPolicy(keep_turns=2, max_input_tokens=1000)
    store.append("t1", exchange(6), LOCAL_USER_ID)

    result = await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=1200)

    assert result == "they talked about files"
    assert store.summary("t1")[1] > 0


async def test_a_request_within_the_budget_leaves_the_thread_alone(store: SqliteStore) -> None:
    policy = ContextPolicy(keep_turns=2, max_input_tokens=1000)
    store.append("t1", exchange(6), LOCAL_USER_ID)

    result = await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=400)

    assert result is None
    assert store.summary("t1") == (None, 0)


async def test_an_oversized_thread_shorter_than_the_window_is_left_alone(
    store: SqliteStore,
) -> None:
    """A request can be over budget with nothing older than the verbatim window.

    Folding cannot help there — every message is one the policy says to keep —
    and the fold must decline rather than cut at a negative position.
    """

    policy = ContextPolicy(keep_turns=2, max_input_tokens=100)
    store.append("t1", exchange(1), LOCAL_USER_ID)

    result = await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=5000)

    assert result is None
    assert store.summary("t1") == (None, 0)


async def test_an_oversized_thread_folded_to_nothing_stops_folding(store: SqliteStore) -> None:
    """Once everything foldable is folded, the next turn must not fold again.

    This is what a budget too small for the system prompt alone produces: every
    request overshoots, so the trigger fires on a `pending` that is empty.
    """

    policy = ContextPolicy(keep_turns=1, max_input_tokens=100)
    store.append("t1", exchange(4), LOCAL_USER_ID)
    await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=5000)
    _, through = store.summary("t1")

    result = await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=5000)

    assert result is None
    assert store.summary("t1")[1] == through


async def test_without_a_known_limit_nothing_folds_by_size(store: SqliteStore) -> None:
    """A model that does not state its context length is not guessed at."""

    policy = ContextPolicy(keep_turns=2)
    store.append("t1", exchange(6), LOCAL_USER_ID)

    result = await fold_older_messages(EchoBackend(), store, "t1", policy, used_tokens=999_999)

    assert result is None


async def test_nothing_is_lost_when_a_thread_is_folded(store: SqliteStore) -> None:
    """The fold moves the window; it never deletes a message."""

    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(EchoBackend(), store, "t1", ContextPolicy(), force=True)

    assert store.message_count("t1") == 24


def test_a_stub_in_history_says_where_the_whole_result_is() -> None:
    """4.6b: a shortened result names its stored position, so the model can
    read it back rather than run the tool again. The turn's own results are
    never shortened (ISS-0041), however many there are."""

    history = [
        user("fetch it"),
        call("c1", "fetch_page", url="https://example.com/"),
        result("c1", "p" * 500),
    ]
    context = Context(history=history, first_position=40)
    turn = [
        call("c2", "fetch_page", url="https://example.com/2"),
        result("c2", "q" * 500),
        call("c3", path="a"),
        result("c3", "r" * 500),
        call("c4", path="b"),
        result("c4", "s" * 500),
    ]

    surface = context.surface(turn)

    assert surface.history[2].content[0].text == (
        "[fetch_page https://example.com/: 500 characters; shortened — the full result is "
        "stored: read_history 42]"
    )
    assert [m.content[0].text for m in surface.turn if m.role == "tool"] == ["q" * 500, "r" * 500, "s" * 500]
    assert surface.stubbed == 1


def test_the_turn_in_progress_keeps_every_result_it_met() -> None:
    """ISS-0041, deployed 2026-09-04: six rewrites of one script, each answered
    by a traceback with exit code 1 — a result, not a failure — and from the
    fourth step the earlier tracebacks were stubs that said "call the tool
    again". The model repeated the first attempt's error exactly. What a tool
    said back within the turn is why the model does what it does next, so the
    turn is shown whole and only stored history is shortened."""

    context = Context(history=[user("earlier"), call("h1"), result("h1", "h" * 400)], keep_results=2)
    turn = [user("fix it")]
    for index in range(6):
        turn += [call(f"c{index}", "run_command", command="python script.py"), result(f"c{index}", f"Traceback {index} " + "t" * 400)]

    surface = context.surface(turn)

    assert [m.content[0].text[:11] for m in surface.turn if m.role == "tool"] == [f"Traceback {i}" for i in range(6)]
    assert surface.stubbed == 1, "the one stored result, older than the newest two"


def test_the_summary_says_where_its_exact_words_are() -> None:
    [_, summary] = build_prelude("they built a board")
    [alone] = build_prelude(None)

    assert "search_history finds them, read_history returns them" in summary.content[0].text
    assert "search_history" not in alone.content[0].text


# --- what the summarizer reads --------------------------------------------------------


async def test_the_summarizer_reads_stubs_not_whole_results(store: SqliteStore) -> None:
    """ISS-0030: a fold sent every tool result whole, up to 32k each; the
    summarizer now reads the same stubs the model reads, with positions."""

    backend = EchoBackend()
    store.append(
        "t1",
        [
            user("fetch it"),
            call("c1", "fetch_page", url="https://example.com/"),
            result("c1", "p" * 5_000),
            *[user(f"and {index}") for index in range(10)],
        ],
        LOCAL_USER_ID,
    )

    await fold_older_messages(backend, store, "t1", ContextPolicy(keep_turns=2), force=True)

    [request] = backend.requests
    body = request[-1].content[0].text
    assert "p" * 200 not in body, "the whole result is not sent"
    assert "[fetch_page https://example.com/: 5000 characters; shortened — the full result is stored: read_history 2]" in body
    assert "and 3" in body and "and 9" not in body


async def test_the_summary_may_grow_with_what_it_covers(store: SqliteStore) -> None:
    backend = EchoBackend()
    store.append("t1", exchange(12), LOCAL_USER_ID)

    await fold_older_messages(backend, store, "t1", ContextPolicy(), force=True)

    [request] = backend.requests
    instruction = request[0].content[0].text
    covered = store.summary("t1")[1]
    assert f"at most {150 + 15 * covered} words" in instruction


def test_the_summary_length_has_a_floor_and_a_ceiling() -> None:
    from app.context.summary import summary_words

    assert summary_words(0) == 150
    assert summary_words(12) == 330
    assert summary_words(100) == 600


async def test_nothing_folds_on_count_alone(store: SqliteStore) -> None:
    """Since 2026-09-07 the only triggers are size and a request: a thread of
    any length stays whole while it fits (ISS-0032, the count rule gone)."""

    store.append("t1", exchange(80), LOCAL_USER_ID)

    assert await fold_older_messages(EchoBackend(), store, "t1", ContextPolicy()) is None
    assert store.summary("t1") == (None, 0)


def test_the_working_method_is_general_and_part_of_the_core() -> None:
    """The human's ask, 2026-09-04: a block that makes any model work as an agent.

    It is a method — find out before assuming, check the result, fix the named
    cause, claim only what was seen — and it must stay one: no tool name, no
    file type, no library, or it becomes a list of past cases.
    """

    import re

    from app.context.window import WORKING_METHOD

    assert WORKING_METHOD in DEFAULT_SYSTEM_PROMPT
    for phrase in ("before you assume", "look at it", "names its cause", "Never claim"):
        assert phrase in WORKING_METHOD
    assert not re.findall(r"[a-z]+_[a-z_]+", WORKING_METHOD)
    for case in ("PDF", "font", "pip", "reportlab", "fpdf"):
        assert case not in WORKING_METHOD

