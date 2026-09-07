"""Transport-neutral Telegram webhook acceptance and worker execution.

An HTTP/platform adapter only has to pass headers and bytes to
``TelegramWebhook.accept`` and translate the returned status. The acceptor
validates, durably queues and requests a worker; it never runs the agent loop.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import TelegramSettings
from app.telemetry.base import TraceEvent, TurnRun
from app.telemetry.trace import NO_TRACE, Telemetry, TurnTrace, log_event
from ui.telegram.inbox import InboxJob, UpdateInbox
from ui.telegram.wire import (
    Incoming,
    conversation_key,
    is_cancellation,
    needs_model,
    read_update,
    travels_out_of_band,
)


MAX_UPDATE_BYTES = 1024 * 1024
SECRET_HEADER = "x-telegram-bot-api-secret-token"

# The deployed worker container's life, the platform's own clock: it counts
# from the input's start and nothing inside the turn resets it. A turn is
# bounded by its health check, not by this, so this is set far above any turn
# the check would let run — a guard against a container that never ends, not a
# ceiling (ISS-0061: at 600 s a ten-minute turn was killed while persisting).
# `deploy/modal/control_app.py` carries the same number, and a test keeps them
# equal.
WORKER_TIMEOUT_SECONDS = 4 * 3600

# How long a worker keeps taking the next update of its conversation before
# handing the rest to a fresh one: the container's life less an hour, so the
# check happens with a whole long turn's room left before the platform's kill.
DRAIN_SECONDS = float(WORKER_TIMEOUT_SECONDS - 3600)

# How many times one update may be claimed before the queue gives up on it. A
# failed turn returns to the queue because most failures are transient, and a
# lease belongs to a conversation — so an update that fails every time it is
# claimed is now claimed ahead of every later message of that conversation, for
# ever. Found live: a consent button pressed after Telegram had expired its
# callback query failed the turn, and would have blocked every message after it.
MAX_ATTEMPTS = 3

# How long a claim holds a conversation between two heartbeats. A live worker
# extends it every `HEARTBEAT_SECONDS` for as long as it is answering, so the
# lease says "a worker is alive", not "a turn may take this long"; a worker
# that dies stops extending and the conversation is free within a minute
# (ISS-0063: a fixed 590 s lease silenced a conversation for ten minutes after
# a kill). Three heartbeats fit in one lease, so one slow store write does not
# free a conversation a live worker still holds.
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20

# How often a worker that found its conversation held asks again. It waits
# out at most one lease: either the holder is alive and drains the queue,
# including the update this worker was started for, or it is dead and the
# lease runs out.
CLAIM_RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class WebhookResponse:
    status: int
    detail: str


class UpdateHandler(Protocol):
    async def handle_update(
        self, update: dict[str, Any], trace: TurnTrace = NO_TRACE
    ) -> None: ...


class TelegramWebhook:
    """Validate and persist one update before asking for a worker."""

    def __init__(
        self,
        settings: TelegramSettings,
        inbox: UpdateInbox,
        spawn: Callable[[int], Awaitable[None]],
        *,
        warm: Callable[[], Awaitable[object]] | None = None,
        max_update_bytes: int = MAX_UPDATE_BYTES,
    ) -> None:
        self.settings = settings
        self.inbox = inbox
        self.spawn = spawn
        self.warm = warm
        self.max_update_bytes = max_update_bytes

    async def accept(self, headers: Mapping[str, str], body: bytes) -> WebhookResponse:
        supplied = {key.lower(): value for key, value in headers.items()}.get(SECRET_HEADER, "")
        expected = self.settings.webhook_secret
        if not expected:
            return WebhookResponse(503, "webhook secret is not configured")
        if not hmac.compare_digest(supplied, expected):
            return WebhookResponse(401, "invalid webhook secret")
        if len(body) > self.max_update_bytes:
            return WebhookResponse(413, "update is too large")
        try:
            update = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WebhookResponse(400, "invalid JSON")
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return WebhookResponse(400, "update_id is required")

        incoming = read_update(update)
        if incoming is None:
            return WebhookResponse(200, "ignored update")
        if not (
            self.settings.open_access
            or incoming.telegram_user_id in self.settings.allowed
        ):
            # Acknowledge without persisting or spawning. Returning an error
            # would make Telegram retry an update that will never be admitted.
            return WebhookResponse(200, "ignored unauthorized account")

        # After admission, never before: a stranger's message must not be able
        # to spend GPU. Alongside the write rather than in front of it, because
        # waking the model is not something the durable hand-off has to wait for
        # — awaiting it first added about a second to every message, which is
        # what the deployed execution times showed.
        if self.warm is None or not needs_model(incoming):
            return await self._admit(update, incoming)
        _, response = await asyncio.gather(self._wake(), self._admit(update, incoming))
        return response

    async def _wake(self) -> None:
        """Ask the model to start. Never raises: a slow turn, not a lost one."""

        assert self.warm is not None
        try:
            await self.warm()
        except Exception:  # noqa: BLE001 - an optimization cannot lose a message
            pass

    async def _admit(self, update: dict[str, Any], incoming: Incoming) -> WebhookResponse:
        """Persist the update, then ask for a worker. Order is the durability.

        The turn's identity is generated here, where the person's message
        actually arrives, and written by the insert that already happens — so
        measurement costs this path no round trip, and the wait before a worker
        exists is inside the turn rather than before it. It is derived from
        nothing: not from the text, the account or the update id.

        The conversation the update belongs to is written by the same insert,
        and this is the only place that decides it. It is what a worker's lease
        is taken against, so a second message arriving while the first is being
        answered waits for it instead of racing it.

        Whether it waits at all is decided here too. A control update is queued
        durably like any other — losing a `/stop` is not an improvement on a
        slow one — and marked so that no conversation's lease holds it.
        """

        queued = await self.inbox.enqueue(
            update["update_id"],
            update,
            run_id=uuid.uuid4().hex,
            conversation_key=conversation_key(incoming),
            control=travels_out_of_band(incoming),
        )
        # One line, so the webhook's own logs join the rest of the turn. The
        # durable record is the worker's; this is correlation, and free.
        log_event(
            TraceEvent(
                run_id=queued.run_id,
                seq=0,
                type="update_enqueued",
                data={"update_id": queued.update_id, "spawning": queued.should_spawn},
            )
        )
        if not queued.should_spawn:
            return WebhookResponse(200, "already accepted")
        try:
            await self.spawn(queued.update_id)
        except Exception:  # noqa: BLE001 - persistence makes a retry safe
            return WebhookResponse(503, "queued; worker was not started")
        return WebhookResponse(200, "accepted")


class TelegramUpdateWorker:
    """Claim a conversation's oldest unanswered update and work through them.

    This is also where a turn begins to be measured, because this is where the
    update stops waiting and starts being worked on. What the person eventually
    received is decided deeper, in the adapter, so the outcome is set there and
    this only closes a turn nobody else closed — which is exactly what a crash
    looks like.

    A worker keeps its conversation until the conversation has nothing left. A
    burst is therefore answered in order by one warm container, and the workers
    spawned for the other updates of that burst find the lease taken and exit —
    which is the whole point, since they are what answered out of order before.
    """

    def __init__(
        self,
        inbox: UpdateInbox,
        handler: UpdateHandler,
        telemetry: Telemetry | None = None,
        *,
        spawn: Callable[[int], Awaitable[None]] | None = None,
        drain_seconds: float = DRAIN_SECONDS,
        lease_seconds: int = LEASE_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        claim_retry_seconds: float = CLAIM_RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.inbox = inbox
        self.handler = handler
        self.telemetry = telemetry or Telemetry(None)
        self.spawn = spawn
        self.drain_seconds = drain_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.claim_retry_seconds = claim_retry_seconds
        self.clock = clock
        self.sleep = sleep

    def _open_trace(self, job: InboxJob) -> TurnTrace:
        """Start measuring, unless this update is answered without a model.

        `/new`, `/chats` and the conversation buttons cost nothing, take
        milliseconds and would outnumber the turns worth measuring. `/stop` is
        the exception: it spends nothing itself but ends something that does.
        """

        incoming = read_update(job.payload)
        if incoming is None or not (needs_model(incoming) or is_cancellation(incoming)):
            return NO_TRACE
        run = TurnRun(
            run_id=job.run_id,
            source="telegram",
            source_update_id=str(job.update_id),
        )
        return self.telemetry.start(run, offset_ms=job.queued_ms)

    async def run(self, update_id: int) -> bool:
        job = await self._claim(update_id)
        if job is None:
            return False
        deadline = self.clock() + self.drain_seconds
        while True:
            if job.attempts > MAX_ATTEMPTS:
                # Claimed three times and never finished: a worker that dies
                # cannot return it to the queue itself, so the count is read
                # here. Given up on, and said so, rather than claimed for ever.
                await self._give_up(job)
            else:
                await self._answer(job)
            if job.control:
                # A control worker exists to answer beside a conversation, not
                # to take it over. Draining from here would put this container
                # in the lane it was created to skip.
                return True
            if not job.conversation_key:
                return True
            if self.clock() >= deadline:
                # Long enough. The container has a timeout of its own and a
                # turn can take minutes, so the rest of the conversation is
                # handed to a fresh worker rather than gambling on being killed
                # mid-turn. Any id of this conversation will do: the next claim
                # reads the key from the row and takes the oldest unfinished one.
                await self._hand_off(job)
                return True
            following = await self.inbox.claim_next(job.conversation_key, self.lease_seconds)
            if following is None:
                return True
            job = following

    async def _claim(self, update_id: int) -> InboxJob | None:
        """Claim the update, waiting out one lease if its conversation is held.

        Every queued update asks for a worker (`enqueue` no longer suppresses
        the spawn while a conversation is held), so a worker that finds the
        conversation taken is the ordinary case in a burst. It does not exit:
        the holder may be dead, and a dead holder's lease is the only thing
        that would ever free this conversation, so somebody has to be there
        when it does. It asks again every `claim_retry_seconds` until the
        update is finished — the live holder drained it — or a lease has
        passed, which is when a dead holder's claim expires and this one takes
        the conversation up.
        """

        waited = 0.0
        while True:
            job = await self.inbox.claim(update_id, self.lease_seconds)
            if job is not None:
                return job
            if waited >= self.lease_seconds or await self.inbox.finished(update_id):
                return None
            await self.sleep(self.claim_retry_seconds)
            waited += self.claim_retry_seconds

    async def _heartbeat(self, job: InboxJob) -> None:
        """Extend the lease while the turn runs. Ends with the turn."""

        while True:
            await self.sleep(self.heartbeat_seconds)
            try:
                await self.inbox.extend(job, self.lease_seconds)
            except Exception:  # noqa: BLE001 - one missed beat is not a dead worker
                pass

    async def _give_up(self, job: InboxJob) -> None:
        log_event(
            TraceEvent(
                run_id=job.run_id,
                seq=0,
                type="update_abandoned",
                data={"update_id": job.update_id, "attempts": job.attempts},
            )
        )
        await self.inbox.abandon(job, f"interrupted {job.attempts - 1} times")
        give_up = getattr(self.handler, "give_up", None)
        if give_up is not None:
            try:
                await give_up(job.payload, job.attempts - 1)
            except Exception:  # noqa: BLE001 - the queue has moved on either way
                pass

    async def _answer(self, job: InboxJob) -> None:
        trace = self._open_trace(job)
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            await self.handler.handle_update(job.payload, trace)
        except Exception as error:
            trace.finish("failed", error_type=type(error).__name__)
            detail = f"{type(error).__name__}: {error}"
            if job.attempts >= MAX_ATTEMPTS:
                # Deliberately not re-raised: the point of giving up is that the
                # conversation carries on, and raising here would leave the
                # messages behind this one waiting for the next thing to spawn
                # a worker.
                log_event(
                    TraceEvent(
                        run_id=job.run_id,
                        seq=0,
                        type="update_abandoned",
                        data={"update_id": job.update_id, "attempts": job.attempts},
                    )
                )
                await self.inbox.abandon(job, detail)
                return
            await self.inbox.retry(job, detail)
            raise
        finally:
            heartbeat.cancel()
            # A turn the adapter did not close is one that ended some other way.
            trace.finish("failed", error_type="incomplete")
            self.telemetry.release(job.run_id)
        await self.inbox.complete(job)

    async def _hand_off(self, job: InboxJob) -> None:
        """Ask for another worker. Never raises: the queue keeps the update."""

        if self.spawn is None:
            return
        try:
            await self.spawn(job.update_id)
        except Exception:  # noqa: BLE001 - the row stays pending either way
            log_event(
                TraceEvent(
                    run_id=job.run_id,
                    seq=0,
                    type="drain_handoff_failed",
                    data={"update_id": job.update_id},
                )
            )

