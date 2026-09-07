"""The webhook/control-plane boundary, entirely offline."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config import TelegramSettings
from tests.fakes import QueuedInbox
from ui.telegram.inbox import InboxJob
from ui.telegram.webhook import MAX_ATTEMPTS, TelegramUpdateWorker, TelegramWebhook
from ui.telegram.wire import (
    MODEL_FREE_COMMANDS,
    SETTLED_APPROVED,
    SETTLED_REJECTED,
    canonical_user_id,
)


def update(update_id: int = 7, user_id: int = 42) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": 99},
            "from": {"id": user_id},
            "text": "hello",
        },
    }


FakeInbox = QueuedInbox


def settings() -> TelegramSettings:
    return TelegramSettings(
        token="bot-token",
        webhook_secret="webhook-secret",
        allowed_users="42",
        _env_file=None,
    )


def body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


async def test_valid_update_is_persisted_before_spawn() -> None:
    inbox = FakeInbox()
    order: list[str] = []

    original_enqueue = inbox.enqueue

    async def enqueue(
        update_id: int,
        payload: dict[str, Any],
        run_id: str = "",
        conversation_key: str = "",
        control: bool = False,
    ) -> EnqueueResult:
        order.append("persist")
        return await original_enqueue(
            update_id, payload, run_id, conversation_key, control
        )

    inbox.enqueue = enqueue  # type: ignore[method-assign]

    async def spawn(update_id: int) -> None:
        assert update_id in inbox.payloads
        order.append("spawn")

    response = await TelegramWebhook(settings(), inbox, spawn).accept(
        {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}, body(update())
    )

    assert response.status == 200
    assert order == ["persist", "spawn"]


async def test_bad_secret_and_unauthorized_user_never_persist_or_spawn() -> None:
    inbox = FakeInbox()
    spawned: list[int] = []

    async def spawn(update_id: int) -> None:
        spawned.append(update_id)

    webhook = TelegramWebhook(settings(), inbox, spawn)
    bad_secret = await webhook.accept({}, body(update()))
    unauthorized = await webhook.accept(
        {"x-telegram-bot-api-secret-token": "webhook-secret"},
        body(update(user_id=100)),
    )

    assert bad_secret.status == 401
    assert unauthorized.status == 200
    assert inbox.payloads == {}
    assert spawned == []


async def test_duplicate_update_already_claimed_does_not_request_a_second_worker() -> None:
    inbox = FakeInbox()
    spawned: list[int] = []

    async def spawn(update_id: int) -> None:
        spawned.append(update_id)

    webhook = TelegramWebhook(settings(), inbox, spawn)
    headers = {"x-telegram-bot-api-secret-token": "webhook-secret"}

    first = await webhook.accept(headers, body(update()))
    assert await inbox.claim(7) is not None
    duplicate = await webhook.accept(headers, body(update()))

    assert first.detail == "accepted"
    assert duplicate.detail == "already accepted"
    assert spawned == [7]


async def test_spawn_failure_keeps_the_persisted_update_for_retry() -> None:
    inbox = FakeInbox()
    attempts = 0

    async def fail(_update_id: int) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("platform unavailable")

    webhook = TelegramWebhook(settings(), inbox, fail)
    headers = {"x-telegram-bot-api-secret-token": "webhook-secret"}
    response = await webhook.accept(headers, body(update()))
    retry = await webhook.accept(headers, body(update()))

    assert response.status == 503
    assert retry.status == 503
    assert attempts == 2
    assert 7 in inbox.payloads


class Handler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: list[dict[str, Any]] = []

    async def handle_update(self, payload: dict[str, Any], trace: Any = None) -> None:
        self.seen.append(payload)
        if self.error:
            raise self.error


async def test_worker_claims_handles_and_completes_one_update() -> None:
    inbox = FakeInbox()
    await inbox.enqueue(7, update())
    handler = Handler()

    ran = await TelegramUpdateWorker(inbox, handler).run(7)
    duplicate = await TelegramUpdateWorker(inbox, handler).run(7)

    assert ran is True
    assert duplicate is False
    assert handler.seen == [update()]
    assert inbox.completed == [7]


async def test_worker_releases_failed_update_for_a_later_retry() -> None:
    inbox = FakeInbox()
    await inbox.enqueue(7, update())
    worker = TelegramUpdateWorker(inbox, Handler(RuntimeError("failed turn")))

    with pytest.raises(RuntimeError, match="failed turn"):
        await worker.run(7)

    assert inbox.retried == [(7, "RuntimeError: failed turn")]


# --- a live worker is known by its heartbeat ----------------------------------
#
# ISS-0061..0063, 2026-09-07: a ten-minute turn was killed at the container's
# 600 s timeout; its fixed 590 s lease then held the conversation with nobody
# alive, and the two messages behind it were queued without a worker because
# the conversation looked busy.


class SlowHandler(Handler):
    """A turn that takes several heartbeats."""

    def __init__(self, beats: int) -> None:
        super().__init__()
        self.beats = beats

    async def handle_update(self, payload: dict[str, Any], trace: Any = None) -> None:
        await super().handle_update(payload, trace)
        for _ in range(self.beats):
            await asyncio.sleep(0)


async def test_a_worker_extends_its_lease_while_the_turn_runs() -> None:
    """The lease says "alive", so a turn that outlives it must keep saying so."""

    inbox = FakeInbox()
    await queued(inbox, 7)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await asyncio.sleep(0)

    await TelegramUpdateWorker(
        inbox, SlowHandler(beats=6), heartbeat_seconds=20, lease_seconds=60, sleep=sleep
    ).run(7)

    assert inbox.extended and all(update_id == 7 for update_id in inbox.extended)
    assert all(seconds == 20 for seconds in slept)
    assert inbox.completed == [7]


async def test_the_heartbeat_ends_with_the_turn() -> None:
    inbox = FakeInbox()
    await queued(inbox, 7)

    await TelegramUpdateWorker(inbox, Handler(), heartbeat_seconds=20, sleep=asyncio.sleep).run(7)
    beats = len(inbox.extended)
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(inbox.extended) == beats


async def test_a_worker_started_for_a_held_conversation_waits_for_the_holder() -> None:
    """The holder is alive and drains the queue, this update included; the
    waiting worker sees it finished and leaves without claiming anything."""

    inbox = FakeInbox()
    await queued(inbox, 7, 8)
    holder = await inbox.claim(7)
    assert holder is not None
    handler = Handler()
    ticks = {"count": 0}

    async def sleep(seconds: float) -> None:
        ticks["count"] += 1
        if ticks["count"] == 2:
            # The live holder finishes its turn and takes the next update.
            await inbox.complete(holder)
            following = await inbox.claim_next("person-42")
            assert following is not None and following.update_id == 8
            await inbox.complete(following)

    ran = await TelegramUpdateWorker(
        inbox, handler, lease_seconds=60, claim_retry_seconds=5, sleep=sleep
    ).run(8)

    assert ran is False
    assert handler.seen == []
    assert ticks["count"] == 2


async def test_a_worker_started_for_a_held_conversation_takes_it_up_when_the_holder_dies() -> None:
    """The holder's heartbeat stops, its lease runs out, and the worker that
    waited claims the conversation's oldest unfinished update: the dead one."""

    inbox = FakeInbox()
    await queued(inbox, 7, 8)
    holder = await inbox.claim(7)
    assert holder is not None
    handler = Handler()

    async def sleep(seconds: float) -> None:
        inbox.expire(7)

    ran = await TelegramUpdateWorker(
        inbox, handler, lease_seconds=60, claim_retry_seconds=5, sleep=sleep
    ).run(8)

    assert ran is True
    assert update_ids(handler) == [7, 8]


async def test_a_waiting_worker_gives_up_after_one_lease() -> None:
    inbox = FakeInbox()
    await queued(inbox, 7, 8)
    assert await inbox.claim(7) is not None
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    ran = await TelegramUpdateWorker(
        inbox, Handler(), lease_seconds=60, claim_retry_seconds=5, sleep=sleep
    ).run(8)

    assert ran is False
    assert sum(slept) == 60


def test_the_deployed_worker_timeout_is_the_one_the_drain_window_is_derived_from() -> None:
    """`control_app.py` is not imported by tests (it builds Modal images), so
    the number it hands the platform is read from its source."""

    from ui.telegram.webhook import WORKER_TIMEOUT_SECONDS

    source = (
        Path(__file__).resolve().parents[1] / "deploy" / "modal" / "control_app.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    declared = [
        eval(compile(ast.Expression(node.value), "control_app.py", "eval"))  # noqa: S307
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "WORKER_TIMEOUT_SECONDS"
            for target in node.targets
        )
    ]

    assert declared == [WORKER_TIMEOUT_SECONDS]


# --- one conversation at a time, in order ------------------------------------
#
# The live defect these are about: a screenshot and the question after it were
# sent seconds apart, ran in two containers, and were answered out of order.


def update_ids(handler: Handler) -> list[int]:
    return [payload["update_id"] for payload in handler.seen]


async def queued(inbox: FakeInbox, *update_ids: int, user_id: int = 42) -> None:
    for update_id in update_ids:
        await inbox.enqueue(
            update_id,
            update(update_id, user_id),
            run_id=f"run-{update_id}",
            conversation_key=f"person-{user_id}",
        )


async def test_a_second_message_waits_for_the_first_instead_of_racing_it() -> None:
    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    held = Handler()
    first = TelegramUpdateWorker(inbox, held)
    second = TelegramUpdateWorker(inbox, Handler())

    # The first worker has claimed the conversation and has not finished; the
    # worker spawned for the other update finds nothing it may take.
    claimed = await inbox.claim(5)
    assert claimed is not None
    assert await second.run(7) is False

    await inbox.complete(claimed)
    assert await first.run(7) is True


async def test_a_worker_answers_the_oldest_message_first_whichever_woke_it() -> None:
    """Order is the queue's, not the race between two spawns."""

    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    handler = Handler()

    ran = await TelegramUpdateWorker(inbox, handler).run(7)

    assert ran is True
    assert update_ids(handler) == [5, 7]
    assert inbox.completed == [5, 7]


async def test_two_people_do_not_wait_for_each_other() -> None:
    inbox = FakeInbox()
    await queued(inbox, 5, user_id=42)
    await queued(inbox, 6, user_id=99)
    mine = await inbox.claim(5)
    assert mine is not None
    theirs = Handler()

    assert await TelegramUpdateWorker(inbox, theirs).run(6) is True
    assert update_ids(theirs) == [6]


async def test_a_long_burst_is_handed_to_a_fresh_worker_rather_than_cut_off() -> None:
    """A container is killed at a timeout; the conversation is not lost to it."""

    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    handler = Handler()
    spawned: list[int] = []
    ticks = iter([0.0, 999.0])

    async def spawn(update_id: int) -> None:
        spawned.append(update_id)

    worker = TelegramUpdateWorker(
        inbox, handler, spawn=spawn, drain_seconds=10.0, clock=lambda: next(ticks)
    )

    assert await worker.run(5) is True
    assert update_ids(handler) == [5]
    assert spawned == [5]
    # Still owed, and still claimable by whoever answers the hand-off.
    assert inbox.state[7] == "pending"


async def test_a_hand_off_nobody_can_answer_leaves_the_update_queued() -> None:
    """The local profile has no second container to spawn."""

    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    handler = Handler()
    ticks = iter([0.0, 999.0])

    worker = TelegramUpdateWorker(
        inbox, handler, drain_seconds=10.0, clock=lambda: next(ticks)
    )

    assert await worker.run(5) is True
    assert inbox.state[7] == "pending"


async def test_a_update_queued_before_conversations_existed_is_still_answered() -> None:
    """The column is additive, so a row from the previous deployment has none."""

    inbox = FakeInbox()
    await inbox.enqueue(7, update())
    handler = Handler()

    assert await TelegramUpdateWorker(inbox, handler).run(7) is True
    assert update_ids(handler) == [7]


async def test_the_front_door_names_the_conversation_it_queues() -> None:
    inbox = FakeInbox()

    async def spawn(_update_id: int) -> None:
        return None

    await TelegramWebhook(settings(), inbox, spawn).accept(SECRET, body(update()))

    assert inbox.keys[7] == canonical_user_id(42)


# --- an update that cannot be answered ---------------------------------------


class Broken:
    """A handler that fails the way a stale callback did: every single time."""

    def __init__(self) -> None:
        self.tries = 0

    async def handle_update(self, update, trace=None) -> None:
        self.tries += 1
        raise RuntimeError("telegram refused answerCallbackQuery: query is too old")


async def test_a_failing_update_returns_to_the_queue_a_bounded_number_of_times() -> None:
    inbox = FakeInbox()
    await queued(inbox, 5)
    handler = Broken()
    worker = TelegramUpdateWorker(inbox, handler)

    for _ in range(MAX_ATTEMPTS - 1):
        with pytest.raises(RuntimeError):
            await worker.run(5)
        assert inbox.state[5] == "pending"

    # The last attempt gives up instead of queueing it again, and does not
    # raise: the point of giving up is that the conversation carries on.
    assert await worker.run(5) is True
    assert inbox.state[5] == "done"
    assert [update_id for update_id, _ in inbox.abandoned] == [5]
    assert handler.tries == MAX_ATTEMPTS


async def test_one_update_nobody_can_answer_does_not_block_the_conversation() -> None:
    """Live, this was a bot that would have stopped answering that person.

    A lease belongs to a conversation, so an update that fails every time it is
    claimed is claimed ahead of every later message of that conversation — for
    ever, since nothing bounded the retries. What made it fail was Telegram
    expiring a callback query while the person thought about the question.
    """

    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    inbox.attempts[5] = MAX_ATTEMPTS - 1  # it has already failed twice

    class Selective:
        def __init__(self) -> None:
            self.seen: list[int] = []

        async def handle_update(self, update, trace=None) -> None:
            self.seen.append(update["update_id"])
            if update["update_id"] == 5:
                raise RuntimeError("this one can never be answered")

    handler = Selective()

    assert await TelegramUpdateWorker(inbox, handler).run(7) is True

    assert handler.seen == [5, 7], "the later message is answered, not stranded"
    assert inbox.state[5] == "done" and inbox.state[7] == "done"


# --- out of band -------------------------------------------------------------
#
# Serializing a conversation is right for the messages in it and wrong for the
# updates about it. `/stop` behind the turn it exists to stop is not a slow
# stop; it is no stop at all.


def command(text: str, update_id: int = 8, user_id: int = 42) -> dict[str, Any]:
    payload = update(update_id, user_id)
    payload["message"]["text"] = text
    return payload


async def accepted(inbox: FakeInbox, payload: dict[str, Any]) -> None:
    async def spawn(_update_id: int) -> None:
        return None

    await TelegramWebhook(settings(), inbox, spawn).accept(SECRET, body(payload))


async def test_the_front_door_marks_a_control_update_and_nothing_else() -> None:
    inbox = FakeInbox()

    await accepted(inbox, update(7))
    await accepted(inbox, command("/stop", 8))
    await accepted(inbox, command("/chats", 9))

    assert inbox.control == {8, 9}


async def test_a_stop_is_answered_while_the_turn_it_stops_is_running() -> None:
    """The whole point. Queued behind the turn, it arrives after the end."""

    inbox = FakeInbox()
    await queued(inbox, 5)
    await accepted(inbox, command("/stop", 8))
    running = await inbox.claim(5)
    assert running is not None, "the turn is in flight"
    handler = Handler()

    assert await TelegramUpdateWorker(inbox, handler).run(8) is True
    assert update_ids(handler) == [8]


async def test_a_control_worker_does_not_take_the_conversation_over() -> None:
    """It answers beside the conversation; it does not join the queue for it."""

    inbox = FakeInbox()
    await accepted(inbox, command("/chats", 8))
    await queued(inbox, 9)
    handler = Handler()

    assert await TelegramUpdateWorker(inbox, handler).run(8) is True
    assert update_ids(handler) == [8]
    assert inbox.state[9] == "pending"


async def test_a_control_update_never_holds_a_conversation_up() -> None:
    inbox = FakeInbox()
    await accepted(inbox, command("/stop", 8))
    await queued(inbox, 9)
    control = await inbox.claim(8)
    assert control is not None and control.control is True
    handler = Handler()

    assert await TelegramUpdateWorker(inbox, handler).run(9) is True
    assert update_ids(handler) == [9]


def test_accepting_an_update_does_not_load_the_agent_stack() -> None:
    """The webhook's import cost is a product decision, so it is a test.

    A cold deployed webhook took 4.69 s to validate an update and write one
    row. Two thirds of its import time was LangGraph and the harness, reached
    through a single `read_update` import from the adapter — code the webhook
    never executes. Nothing warns when an import like that comes back, so this
    is the warning.

    A subprocess because the assertion is about a fresh interpreter; by the
    time the rest of this suite has run, everything is in `sys.modules`.
    """

    probe = (
        "import sys;"
        "import ui.telegram.webhook, ui.telegram.inbox;"
        "heavy = sorted("
        "  name for name in ('langgraph', 'langchain_core', 'langsmith', 'app.agent',"
        "                    'ui.telegram.adapter')"
        "  if any(loaded == name or loaded.startswith(name + '.') for loaded in sys.modules)"
        ");"
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_the_wire_format_module_imports_nothing_from_the_application() -> None:
    """`wire.py` is only worth having while it stays free of dependencies."""

    source = (
        Path(__file__).resolve().parents[1] / "ui" / "telegram" / "wire.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    # `uuid` earns its place: the canonical identity of a Telegram account is
    # derived here so the webhook can name the conversation it is queueing
    # without importing the adapter to ask.
    assert imported <= {"__future__", "dataclasses", "typing", "uuid"}


# --- waking the model before the worker exists --------------------------------

SECRET = {"x-telegram-bot-api-secret-token": "webhook-secret"}


class Warming:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def __call__(self) -> bool:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return True


def warming_webhook(warm: Warming) -> tuple[TelegramWebhook, FakeInbox, list[int]]:
    inbox, spawned = FakeInbox(), []

    async def spawn(update_id: int) -> None:
        spawned.append(update_id)

    return TelegramWebhook(settings(), inbox, spawn, warm=warm), inbox, spawned


async def test_an_ordinary_message_starts_the_model_waking() -> None:
    """The model wakes in about 5.5 s and the worker takes about 4.9 s to be
    scheduled and initialized. Started together they cost 5.5 s; started one
    after the other they cost both."""

    warm = Warming()
    webhook, _, spawned = warming_webhook(warm)

    response = await webhook.accept(SECRET, body(update()))

    assert response.status == 200
    assert warm.calls == 1
    assert spawned == [7]


async def test_a_model_free_command_does_not_spend_a_gpu_wake() -> None:
    for command in sorted(MODEL_FREE_COMMANDS):
        warm = Warming()
        webhook, _, spawned = warming_webhook(warm)
        raw = update()
        raw["message"]["text"] = command

        response = await webhook.accept(SECRET, body(raw))

        assert response.status == 200
        assert warm.calls == 0, f"{command} spent a GPU wake"
        # Still processed. Not waking is an optimization, not a refusal.
        assert spawned == [7]


async def test_an_approval_button_wakes_because_resuming_calls_the_model() -> None:
    warm = Warming()
    webhook, _, _ = warming_webhook(warm)
    raw = {
        "update_id": 11,
        "callback_query": {
            "id": "c1",
            "data": "approve",
            "from": {"id": 42},
            "message": {"chat": {"id": 99}},
        },
    }

    assert (await webhook.accept(SECRET, body(raw))).status == 200
    assert warm.calls == 1


async def test_neither_a_stranger_nor_a_bad_secret_can_spend_a_gpu_wake() -> None:
    """Why the admission checks come first rather than merely somewhere."""

    warm = Warming()
    webhook, _, spawned = warming_webhook(warm)

    unauthorized = await webhook.accept(SECRET, body(update(user_id=9999)))
    bad_secret = await webhook.accept({"x-telegram-bot-api-secret-token": "no"}, body(update()))

    assert (unauthorized.status, bad_secret.status) == (200, 401)
    assert warm.calls == 0
    assert spawned == []


async def test_a_failed_wake_still_delivers_the_message() -> None:
    """A slower turn is acceptable; a lost one is not."""

    warm = Warming(RuntimeError("the model endpoint is unreachable"))
    webhook, inbox, spawned = warming_webhook(warm)

    response = await webhook.accept(SECRET, body(update()))

    assert response.status == 200
    assert warm.calls == 1
    assert spawned == [7]
    assert 7 in inbox.payloads


async def test_waking_does_not_delay_the_durable_hand_off() -> None:
    """Awaiting the wake first cost about a second on every deployed message.

    The wake and the write are independent — one asks a GPU to start, the other
    records the update — so they run together. This asserts the overlap rather
    than the wall clock: the write starts before the wake has finished.
    """

    order: list[str] = []
    started = asyncio.Event()

    async def warm() -> bool:
        order.append("wake started")
        started.set()
        await asyncio.sleep(0.05)
        order.append("wake finished")
        return True

    inbox = FakeInbox()
    original = inbox.enqueue

    async def enqueue(
        update_id: int,
        payload: dict[str, Any],
        run_id: str = "",
        conversation_key: str = "",
        control: bool = False,
    ) -> EnqueueResult:
        await started.wait()
        order.append("write")
        return await original(update_id, payload, run_id, conversation_key, control)

    inbox.enqueue = enqueue  # type: ignore[method-assign]

    async def spawn(update_id: int) -> None:
        order.append("spawn")

    response = await TelegramWebhook(settings(), inbox, spawn, warm=warm).accept(
        SECRET, body(update())
    )

    assert response.status == 200
    assert order.index("write") < order.index("wake finished")
    assert order[-1] == "wake finished" or "spawn" in order


async def test_a_settled_status_button_does_not_spend_a_gpu_wake() -> None:
    """The one callback that is not a decision.

    A button reading `✓ Approved` describes something that already happened.
    Pressing it out of curiosity must cost nothing, and the front door is where
    that is decided — by the time an adapter could tell, the wake has been paid
    for.
    """

    for data in (SETTLED_APPROVED, SETTLED_REJECTED):
        warm = Warming()
        webhook, _, spawned = warming_webhook(warm)
        raw = {
            "update_id": 12,
            "callback_query": {
                "id": "c2",
                "data": data,
                "from": {"id": 42},
                "message": {"chat": {"id": 99}, "message_id": 500},
            },
        }

        response = await webhook.accept(SECRET, body(raw))

        assert response.status == 200
        assert warm.calls == 0, f"{data} spent a GPU wake"
        # Still delivered, so the adapter can acknowledge the tap.
        assert spawned == [12]


async def test_an_update_claimed_three_times_and_never_finished_is_given_up_on() -> None:
    """A worker that dies cannot return its update to the queue, so the count
    of claims is read at the next claim: the fourth is not another attempt,
    it is the moment to stop and say so (roadmap 4.7; OpenClaw's budget of
    three)."""

    inbox = FakeInbox()
    await queued(inbox, 5, 7)
    inbox.attempts[5] = MAX_ATTEMPTS  # claimed three times; three workers died

    class Remembering(Handler):
        def __init__(self) -> None:
            super().__init__()
            self.given_up: list[tuple[int, int]] = []

        async def give_up(self, update, attempts: int) -> None:
            self.given_up.append((update["update_id"], attempts))

    handler = Remembering()
    assert await TelegramUpdateWorker(inbox, handler).run(5) is True

    assert update_ids(handler) == [7], "the dead update is not tried a fourth time"
    assert handler.given_up == [(5, 3)]
    assert [update_id for update_id, _ in inbox.abandoned] == [5]
    assert inbox.state[5] == "done" and inbox.state[7] == "done"
