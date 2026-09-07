"""Stage 2's closing criteria, driven end to end against a real SQLite file.

Only the model is faked. The store, the tools, the context layers and the graph
are the real ones, because these are the properties that only appear when they
are wired together: a conversation that survives a restart, a fact that outlives
the session that saved it, and a history that folds instead of growing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime import Agent
from app.context import ContextPolicy
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, ContextOverflowError, Message
from tests.fakes import ScriptedBackend, body, calls, prompt_text, says, user


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    (room / "notes.txt").write_text("the answer is 42", encoding="utf-8")
    return room


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "memory.sqlite3"


def open_agent(
    database: Path,
    workspace: Path,
    backend: ScriptedBackend,
    policy: ContextPolicy | None = None,
) -> Agent:
    return Agent(backend, SqliteStore(database), workspace, policy)


# --- conversations survive a restart -----------------------------------------


async def test_a_conversation_survives_a_restart(
    database: Path, workspace: Path
) -> None:
    first = open_agent(database, workspace, ScriptedBackend(says("Hello Anna.")))
    await first.answer("t1", user("My name is Anna."))
    await first.aclose()

    second = open_agent(database, workspace, ScriptedBackend(says("Anna.")))
    stored = second.history("t1")

    assert [body(message) for message in stored] == ["My name is Anna.", "Hello Anna."]


async def test_a_restarted_agent_sends_the_earlier_turns_back_to_the_model(
    database: Path, workspace: Path
) -> None:
    first = open_agent(database, workspace, ScriptedBackend(says("Hello Anna.")))
    await first.answer("t1", user("My name is Anna."))
    await first.aclose()

    backend = ScriptedBackend(says("Anna."))
    second = open_agent(database, workspace, backend)
    await second.answer("t1", user("What is my name?"))

    assert "My name is Anna." in prompt_text(backend.requests[0])


async def test_threads_do_not_leak_into_each_other(database: Path, workspace: Path) -> None:
    backend = ScriptedBackend(says("noted"), default=says("noted"))
    agent = open_agent(database, workspace, backend)
    await agent.answer("t1", user("A secret about thread one."))

    await agent.answer("t2", user("Unrelated question."))

    assert "thread one" not in prompt_text(backend.requests[1])


async def test_a_tool_cycle_is_stored_whole(database: Path, workspace: Path) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), says("42"))
    agent = open_agent(database, workspace, backend)

    await agent.answer("t1", user("What does notes.txt say?"))
    await agent.aclose()

    stored = open_agent(database, workspace, ScriptedBackend()).history("t1")
    assert [message.role for message in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[1].tool_calls[0].name == "read_file"
    assert stored[2].tool_call_id == "call_read_file"


# --- a fact outlives the session that saved it -------------------------------


async def test_a_fact_saved_in_one_session_is_retrieved_in_a_later_one(
    database: Path, workspace: Path
) -> None:
    saver = open_agent(
        database,
        workspace,
        ScriptedBackend(calls("remember_fact", text="Anna runs vLLM in WSL2"), says("Saved.")),
    )
    await saver.answer("first-session", user("Remember that I run vLLM in WSL2."))
    await saver.aclose()

    backend = ScriptedBackend(says("In WSL2."))
    later = open_agent(database, workspace, backend)
    await later.answer("second-session", user("Where do I run vLLM?"))

    assert "Anna runs vLLM in WSL2" in prompt_text(backend.requests[0])


async def test_an_unrelated_question_does_not_drag_facts_in(
    database: Path, workspace: Path
) -> None:
    """Layer four is retrieval, not a dump of everything ever saved."""

    saver = open_agent(
        database,
        workspace,
        ScriptedBackend(calls("remember_fact", text="Anna runs vLLM in WSL2"), says("Saved.")),
    )
    await saver.answer("first-session", user("Remember that."))
    await saver.aclose()

    backend = ScriptedBackend(says("Sure."))
    later = open_agent(database, workspace, backend)
    await later.answer("second-session", user("Tell me a joke about penguins."))

    assert "vLLM" not in prompt_text(backend.requests[0])


async def test_the_model_can_look_a_fact_up_on_purpose(
    database: Path, workspace: Path
) -> None:
    agent = open_agent(
        database,
        workspace,
        ScriptedBackend(
            calls("remember_fact", text="The GPU is an RTX 4090"),
            calls("search_memory", query="GPU"),
            says("An RTX 4090."),
        ),
    )
    await agent.answer("t1", user("Remember my GPU, then tell me what it is."))

    stored = agent.history("t1")
    tool_results = [body(message) for message in stored if message.role == "tool"]
    assert tool_results[0].startswith("saved:")
    assert "RTX 4090" in tool_results[1]


async def test_a_fact_is_only_saved_when_the_model_asks_for_it(
    database: Path, workspace: Path
) -> None:
    agent = open_agent(database, workspace, ScriptedBackend(says("That is interesting.")))

    await agent.answer("t1", user("My GPU is an RTX 4090."))

    assert agent.store.facts(LOCAL_USER_ID) == []


# --- older context folds instead of growing ----------------------------------


async def test_older_context_is_summarized_rather_than_grown(
    database: Path, workspace: Path
) -> None:
    policy = ContextPolicy(keep_turns=2)
    backend = ScriptedBackend(default=says("a summary of what came before", input_tokens=9_000), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, policy, context_fraction=0.6)

    for turn in range(10):
        await agent.answer("t1", user(f"turn {turn}"))

    summary, through = agent.store.summary("t1")
    assert summary == "a summary of what came before"
    assert through > 0
    # The last request is the fold itself; the one before it carried the summary.
    assert any("Summary of the earlier conversation" in prompt_text(r) for r in backend.requests)


async def test_the_full_history_is_never_sent(database: Path, workspace: Path) -> None:
    policy = ContextPolicy(keep_turns=2)
    backend = ScriptedBackend(default=says("ok", input_tokens=9_000), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, policy, context_fraction=0.6)

    for turn in range(12):
        await agent.answer("t1", user(f"turn {turn}"))

    assert agent.store.message_count("t1") == 24
    assert len(backend.requests[-1]) < 24
    assert "turn 0" not in prompt_text(backend.requests[-1])


async def test_folding_never_deletes_a_message(database: Path, workspace: Path) -> None:
    policy = ContextPolicy(keep_turns=2)
    backend = ScriptedBackend(default=says("ok", input_tokens=9_000), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, policy, context_fraction=0.6)

    for turn in range(10):
        await agent.answer("t1", user(f"turn {turn}"))

    assert [body(m) for m in agent.history("t1")][:2] == ["turn 0", "ok"]


# --- multimodal turns --------------------------------------------------------


async def test_an_image_turn_is_stored_and_replayed(database: Path, workspace: Path) -> None:
    picture = Message(
        role="user",
        content=[
            ContentPart(kind="text", text="What is this?"),
            ContentPart(kind="image", data=b"\x89PNG\x00", media_type="image/png"),
        ],
    )
    agent = open_agent(database, workspace, ScriptedBackend(says("A picture.")))
    await agent.answer("t1", picture)
    await agent.aclose()

    backend = ScriptedBackend(says("Still a picture."))
    later = open_agent(database, workspace, backend)
    await later.answer("t1", user("And now?"))

    replayed = [part for message in backend.requests[0] for part in message.content]
    assert any(part.kind == "image" and part.data == b"\x89PNG\x00" for part in replayed)


# --- what the caller gets back -----------------------------------------------


async def test_answer_returns_the_intermediate_steps_and_the_answer(
    database: Path, workspace: Path
) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), says("42"))
    agent = open_agent(database, workspace, backend)

    produced = await agent.answer("t1", user("What does notes.txt say?"))

    assert [message.role for message in produced] == ["assistant", "tool", "assistant"]
    assert body(produced[-1]) == "42"


# --- how full the request was ------------------------------------------------


async def test_the_fill_is_the_size_the_model_reported(database: Path, workspace: Path) -> None:
    backend = ScriptedBackend(says("hello", input_tokens=600), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, context_fraction=0.5)

    await agent.answer("t1", user("hi"))
    fill = await agent.fill()

    assert (fill.used, fill.budget) == (600, 5000)
    assert fill.fraction == pytest.approx(0.12)


async def test_a_model_that_states_no_limit_leaves_the_request_unjudged(
    database: Path, workspace: Path
) -> None:
    backend = ScriptedBackend(says("hello", input_tokens=600))
    agent = Agent(backend, SqliteStore(database), workspace)

    await agent.answer("t1", user("hi"))
    fill = await agent.fill()

    assert fill.budget is None
    assert fill.fraction is None


async def test_there_is_no_fill_before_a_turn(database: Path, workspace: Path) -> None:
    agent = Agent(ScriptedBackend(), SqliteStore(database), workspace)

    assert await agent.fill() is None


async def test_the_limit_is_asked_for_once(database: Path, workspace: Path) -> None:
    backend = ScriptedBackend(default=says("ok", input_tokens=10), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace)
    asked: list[int] = []
    original = backend.context_limit

    async def counted() -> int | None:
        asked.append(1)
        return await original()

    backend.context_limit = counted

    await agent.answer("t1", user("one"))
    await agent.answer("t2", user("two"))
    await agent.fill()

    assert len(asked) == 1


async def test_a_request_over_budget_folds_the_conversation(
    database: Path, workspace: Path
) -> None:
    """The bound is a token bound: nothing here is long enough to fold by count."""

    policy = ContextPolicy(keep_turns=1)
    backend = ScriptedBackend(default=says("ok", input_tokens=9_000), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, policy, context_fraction=0.6)

    for turn in range(4):
        await agent.answer("t1", user(f"turn {turn}"))

    summary, through = agent.store.summary("t1")
    assert summary == "ok"
    assert through > 0


def seed_old_turns(store: SqliteStore, thread_id: str = "t1") -> None:
    messages = []
    for turn in range(3):
        messages.extend(
            [
                user(f"old turn {turn}"),
                Message(
                    role="assistant",
                    content=[ContentPart(kind="text", text=f"old answer {turn}")],
                ),
            ]
        )
    store.append(thread_id, messages, LOCAL_USER_ID)


async def test_a_conversation_over_budget_folds_before_the_request_is_sent(
    database: Path, workspace: Path
) -> None:
    """The fold moved in front of the model call, which is the point of it.

    Nothing overflows here and the endpoint refuses nothing. The size of what is
    about to be sent is estimated first, so the oversized request is the one
    that never gets made — where it used to be sent, refused, and recovered
    from, or worse, sent successfully and folded only on the turn after.
    """

    store = SqliteStore(database)
    seed_old_turns(store)
    backend = ScriptedBackend(
        says("compressed older turns"),
        says("an answer"),
        limit=1_000,
    )
    policy = ContextPolicy(keep_turns=1)
    agent = Agent(backend, store, workspace, policy, context_fraction=0.6)

    produced = await agent.answer("t1", user("new question"))

    assert body(produced[-1]) == "an answer"
    # The summary, then the answer. No refusal and no retry in between.
    assert len(backend.requests) == 2
    assert agent.store.summary("t1")[0] == "compressed older turns"
    assert "old turn 0" not in prompt_text(backend.requests[-1])


async def test_context_overflow_folds_then_retries_once(
    database: Path, workspace: Path
) -> None:
    """The backstop under the estimate: the server refuses anyway.

    The budget is deliberately far larger than this conversation, so the
    pre-request fold does not fire and the overflow arrives unannounced — which
    is exactly the case an estimate cannot rule out and this path still has to
    survive.
    """

    store = SqliteStore(database)
    seed_old_turns(store)
    backend = ScriptedBackend(
        ContextOverflowError("too large"),
        says("compressed older turns"),
        says("recovered answer", input_tokens=40),
        limit=100_000,
    )
    policy = ContextPolicy(keep_turns=1)
    agent = Agent(backend, store, workspace, policy, context_fraction=0.6)

    produced = await agent.answer("t1", user("new question"))

    assert body(produced[-1]) == "recovered answer"
    assert len(backend.requests) == 3
    assert agent.store.summary("t1")[0] == "compressed older turns"
    assert "old turn 0" not in prompt_text(backend.requests[-1])


async def test_a_second_context_overflow_returns_a_clear_refusal(
    database: Path, workspace: Path
) -> None:
    store = SqliteStore(database)
    seed_old_turns(store)
    backend = ScriptedBackend(
        ContextOverflowError("first overflow"),
        says("compressed older turns"),
        ContextOverflowError("still too large"),
        limit=100_000,
    )
    policy = ContextPolicy(keep_turns=1)
    agent = Agent(backend, store, workspace, policy, context_fraction=0.6)

    produced = await agent.answer("t1", user("huge new question"))

    assert "too large for the model's context window" in body(produced[-1])
    assert len(backend.requests) == 3
    assert body(agent.history("t1")[-1]) == body(produced[-1])


async def test_an_input_that_cannot_be_folded_is_refused_without_retrying(
    database: Path, workspace: Path
) -> None:
    backend = ScriptedBackend(ContextOverflowError("too large"), limit=100)
    agent = Agent(backend, SqliteStore(database), workspace, context_fraction=0.6)

    produced = await agent.answer("t1", user("one enormous new input"))

    assert "too large for the model's context window" in body(produced[-1])
    assert len(backend.requests) == 1
    assert [body(message) for message in agent.history("t1")] == [
        "one enormous new input",
        body(produced[-1]),
    ]


async def test_overflow_while_summarizing_stops_with_a_refusal(
    database: Path, workspace: Path
) -> None:
    store = SqliteStore(database)
    seed_old_turns(store)
    backend = ScriptedBackend(
        ContextOverflowError("original request overflow"),
        ContextOverflowError("summary also overflowed"),
        limit=100_000,
    )
    policy = ContextPolicy(keep_turns=1)
    agent = Agent(backend, store, workspace, policy, context_fraction=0.6)

    produced = await agent.answer("t1", user("new question"))

    assert "too large for the model's context window" in body(produced[-1])
    assert len(backend.requests) == 2
    assert agent.store.summary("t1") == (None, 0)


async def test_the_workspace_is_the_only_readable_root(
    database: Path, workspace: Path, tmp_path: Path
) -> None:
    (tmp_path / "outside.txt").write_text("must not be read", encoding="utf-8")
    backend = ScriptedBackend(calls("read_file", path="../outside.txt"), says("I cannot."))
    agent = open_agent(database, workspace, backend)

    produced = await agent.answer("t1", user("Read ../outside.txt"))

    assert body(produced[1]).startswith("error: path")
    assert "must not be read" not in prompt_text(backend.requests[1])


# --- a fold that fails does not fail the turn ----------------------------------------


class _Overflowing(ScriptedBackend):
    """Answers as scripted; the summarizer's request does not fit."""

    async def invoke(self, messages, tools=None, response_format=None):
        if "running summary" in body(messages[0]):
            self.requests.append(list(messages))
            raise ContextOverflowError("HTTP 400: maximum context length")
        return await super().invoke(messages, tools, response_format)


async def test_a_summarizer_that_does_not_fit_does_not_fail_a_delivered_turn(
    database: Path, workspace: Path
) -> None:
    """ISS-0029: the answer was streamed, then the fold in `persist` raised
    and the person read "That request failed" under a complete answer."""

    policy = ContextPolicy(keep_turns=2)
    backend = _Overflowing(default=says("ok", input_tokens=9_000), limit=10_000)
    agent = Agent(backend, SqliteStore(database), workspace, policy, context_fraction=0.6)

    answers = [await agent.answer("t1", user(f"turn {turn}")) for turn in range(6)]

    assert all(body(reply[-1]) == "ok" for reply in answers)
    assert agent.store.summary("t1") == (None, 0), "nothing folded, nothing lost"
    assert agent.store.message_count("t1") == 12
    assert any("running summary" in body(request[0]) for request in backend.requests)


async def test_a_fold_before_a_step_that_does_not_fit_still_answers(
    database: Path, workspace: Path
) -> None:
    """The same guard in `fitted`: the request goes unfolded and the overflow
    path answers if it must; here it fits, so the answer is the answer."""

    policy = ContextPolicy(keep_turns=1)
    backend = _Overflowing(default=says("ok"), limit=100)
    agent = open_agent(database, workspace, backend, policy)

    for turn in range(3):
        await agent.answer("t1", user(f"turn {turn}"))

    assert agent.store.message_count("t1") == 6
    assert any("running summary" in body(request[0]) for request in backend.requests)
