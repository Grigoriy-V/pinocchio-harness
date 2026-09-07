"""The deployed lane for a message sent mid-turn: the inbox itself.

Deployed, the front door queues every update in `telegram_updates` and a
message that arrives while the person's turn is running is left `pending`
behind the lease (`inbox.py`). Nothing new is written for it. The running
turn, at its tools boundary, takes those rows in one statement — `pending`
to `done` with `RETURNING` — which is what makes a taken message this turn's
and nobody else's: the drain loop never claims a done row, and a row a later
worker already claimed is `running` and is not taken.

Turning a row into the turn's input is the adapter's `to_message`, files and
all, which is why this lives beside the adapter and not in `app/`. A row whose
file cannot be fetched is put back to `pending`: it gets its own turn and the
usual refusal, rather than being lost with its download.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from app.models import Message
from ui.telegram.wire import read_update

if TYPE_CHECKING:
    from ui.telegram.adapter import TelegramAdapter


class TakingInbox(Protocol):
    """What the lane needs of the inbox: the two statements in `inbox.py`."""

    async def take_pending(self, conversation_key: str, after: int) -> list[dict[str, Any]]: ...

    async def release(self, update_id: int) -> None: ...


class InboxInterjections:
    def __init__(self, inbox: TakingInbox, adapter: TelegramAdapter) -> None:
        self.inbox = inbox
        self.adapter = adapter

    async def take(self, key: str, since: int) -> list[Message]:
        """The person's pending messages after `since`, oldest first.

        `key` is the agent's user id, which is also the inbox's conversation
        key (`wire.conversation_key`): both are the canonical id of the account.
        """

        rows = await self.inbox.take_pending(key, since)
        taken: list[Message] = []
        for row in sorted(rows, key=lambda row: int(row["update_id"])):
            incoming = read_update(row["payload"])
            if incoming is None or incoming.callback_data is not None:
                # Not a message; answered on its own, as it would have been.
                await self.inbox.release(int(row["update_id"]))
                continue
            try:
                workspace = self.adapter.agent(key).capability_grant.root
                taken.append(await self.adapter.to_message(incoming, workspace))
            except Exception:  # noqa: BLE001 - the row, not the turn, carries the failure
                await self.inbox.release(int(row["update_id"]))
        return taken
