"""Long polling: the local transport in front of the adapter.

This is the part the deployed profile replaces. A webhook there will validate
the request, hand the update to a spawned worker and answer Telegram at once;
the worker calls the same `TelegramAdapter.handle_update`. Keeping the loop in
its own module is what makes that a substitution rather than a rewrite.

Updates from different chats run concurrently, because one person's five-minute
turn must not silence the assistant for everyone else. Updates from the same
chat are serialized, so an approval cannot overtake the question that produced
it — except for the control updates, which are answered beside the lock rather
than behind it. `/stop` waiting for the turn it exists to stop was this
profile's behaviour for as long as the lock has existed.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from app.config import AgentSettings, TelegramSettings
from app.telemetry import NO_TRACE, TurnRun, TurnTrace, open_telemetry
from ui.telegram.adapter import TelegramAdapter
from ui.telegram.api import TelegramClient, TelegramError
from ui.telegram.wire import (
    is_cancellation,
    needs_model,
    read_update,
    travels_out_of_band,
)

# How long to wait after a transport failure before polling again, so a bot
# pointed at an unreachable network does not spin.
RETRY_SECONDS = 5.0


class PollingBot:
    """Drive the adapter from `getUpdates` until cancelled."""

    def __init__(self, adapter: TelegramAdapter) -> None:
        self.adapter = adapter
        self.client = adapter.client
        self._offset: int | None = None
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: set[asyncio.Task[None]] = set()

    def _lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    @staticmethod
    def _chat_of(update: dict[str, Any]) -> int:
        message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        return int((message.get("chat") or {}).get("id", 0))

    def _trace(self, update: dict[str, Any]) -> TurnTrace:
        """Start measuring here, because locally there is no webhook to do it.

        The deployed profile generates the turn's identity at its front door and
        keeps it with the durable queue row. Polling has neither, so the moment
        an update comes off `getUpdates` is the earliest honest start.
        """

        incoming = read_update(update)
        if incoming is None or not (needs_model(incoming) or is_cancellation(incoming)):
            return NO_TRACE
        return self.adapter.telemetry.start(
            TurnRun(
                run_id=uuid.uuid4().hex,
                source="telegram-polling",
                source_update_id=str(update.get("update_id", "")),
            )
        )

    async def _guarded(self, update: dict[str, Any]) -> None:
        incoming = read_update(update)
        if incoming is not None and travels_out_of_band(incoming):
            # Past the lock, deliberately. These are answered from wiring or
            # storage in milliseconds and change no history, and one of them is
            # `/stop`, which is useless anywhere but here.
            await self._handle(update)
            return
        # Offered to the turn that may be running before waiting for it, so
        # the person's comment reaches the model at that turn's next step.
        # Once the conversation is free, a message the turn read is done
        # with; the rest are answered as their own turn, as before.
        offered = await self.adapter.offer(update)
        async with self._lock(self._chat_of(update)):
            if offered is not None and await self.adapter.taken(*offered):
                return
            await self._handle(update)

    async def _handle(self, update: dict[str, Any]) -> None:
        trace = self._trace(update)
        try:
            await self.adapter.handle_update(update, trace)
        except TelegramError as error:
            trace.finish("failed", error_type="TelegramError")
            print(f"telegram delivery failed: {error}", flush=True)
        finally:
            trace.finish("failed", error_type="incomplete")
            self.adapter.telemetry.release(trace.run.run_id)

    def dispatch(self, update: dict[str, Any]) -> None:
        task = asyncio.create_task(self._guarded(update))
        # Held so the loop does not drop the only reference and let the task be
        # collected mid-turn.
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def poll_once(self) -> int:
        """Fetch and dispatch one batch, returning how many updates arrived."""

        updates = await self.client.get_updates(self._offset)
        for update in updates:
            self._offset = int(update["update_id"]) + 1
            self.dispatch(update)
        return len(updates)

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except TelegramError as error:
                print(f"telegram poll failed: {error}", flush=True)
                await asyncio.sleep(RETRY_SECONDS)

    async def aclose(self) -> None:
        for task in list(self._running):
            task.cancel()
        for task in list(self._running):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.adapter.aclose()
        await self.client.aclose()
        self.adapter.telemetry.close()


async def main() -> None:
    settings = TelegramSettings()
    if not settings.token:
        raise SystemExit("TELEGRAM_TOKEN is not set; see env.example")
    if not settings.allowed and not settings.open_access:
        raise SystemExit(
            "TELEGRAM_ALLOWED_USERS is empty and TELEGRAM_OPEN_ACCESS is off, so "
            "nobody may use this bot. Set the list to your numeric Telegram user "
            "id, or set TELEGRAM_OPEN_ACCESS=true to admit everyone."
        )
    client = TelegramClient(settings)
    agent_settings = AgentSettings()
    bot = PollingBot(
        TelegramAdapter(
            client,
            settings,
            agent_settings,
            telemetry=open_telemetry(agent_settings),
        )
    )
    if settings.open_access:
        # Loud at the point of use, not only in documentation: this is the one
        # setting whose cost is paid by the owner and not by whoever turned it on.
        print(
            "OPEN ACCESS: every Telegram account that finds this bot may use it.\n"
            "  Conversations and memory stay separate per account.\n"
            "  The workspace and the GPU are shared.\n"
            "  Unset TELEGRAM_OPEN_ACCESS to go back to the allow list."
        )
    else:
        print(f"Polling Telegram for {len(settings.allowed)} allowed account(s).")
    print("Ctrl+C to stop.")
    try:
        await bot.run()
    finally:
        await bot.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
