"""Durable hand-off between the Telegram webhook and an agent worker.

The webhook acknowledges quickly after this inbox owns the update. A worker
then claims it with a lease, so duplicate Telegram deliveries or duplicate
spawn attempts cannot run the same update concurrently. PostgreSQL is opened
per operation because the deployed processes scale to zero between requests.

A lease is held against a conversation, not only against one update. Two
messages sent seconds apart used to be claimed by two containers and answered
out of order, because each claim only asked whether *that* update was free. A
claim now asks for the oldest unfinished update of the conversation the caller
names, and refuses while another one of its updates is still running.

Control updates are the exception, and are marked as such by the front door. A
`/stop` queued behind the turn it exists to stop is not a slow stop but no stop
at all, so a control row is claimed on its own, is never what a conversation's
claim takes, and never holds a conversation up.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# What `last_error` says for a row the running turn read mid-turn: not an
# error, the place the update went.
TAKEN = "taken by the running turn"


def lock_id(conversation_key: str) -> int:
    """One conversation's advisory-lock identifier, as a signed 64-bit integer.

    Mutual exclusion cannot come from the claim statement alone. Two workers
    holding different updates of one conversation both find nothing running and
    both proceed, because they lock different rows and never meet. A lock keyed
    by the conversation is what makes them meet, and PostgreSQL's advisory locks
    give one without a second table: it is held for the transaction and released
    when it ends, including when the connection dies.

    The number is computed here rather than by `hashtext()` so the value does
    not depend on a server function whose definition is not part of the SQL
    contract.
    """

    digest = hashlib.sha256(conversation_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class EnqueueResult:
    update_id: int
    should_spawn: bool
    # The identity this turn will be measured under. Born at ingress and kept
    # with the durable row, so a redelivered update or a second spawn attempt
    # continues the same turn instead of starting a second measured one.
    run_id: str = ""


@dataclass(frozen=True)
class InboxJob:
    update_id: int
    payload: dict[str, Any]
    lease_token: str
    run_id: str = ""
    # How long the update waited between arriving and being claimed: the queue
    # plus the worker's own cold start, which is the largest CPU number in the
    # chain and would otherwise fall outside every measurement.
    queued_ms: int = 0
    # The conversation this update belongs to, carried out of the claim so the
    # worker can ask for the next one without another lookup. Empty for a row
    # written before the column existed.
    conversation_key: str = ""
    # A control update: answered beside the conversation rather than in it, and
    # never part of the drain. A worker that claimed one does that one thing.
    control: bool = False
    # How many times this update has been claimed, this claim included. What
    # bounds retrying it: an update that cannot be answered must not hold its
    # conversation for the rest of time.
    attempts: int = 1


class UpdateInbox(Protocol):
    async def enqueue(
        self,
        update_id: int,
        payload: dict[str, Any],
        run_id: str = "",
        conversation_key: str = "",
        control: bool = False,
    ) -> EnqueueResult: ...

    async def claim(self, update_id: int, lease_seconds: int = 900) -> InboxJob | None: ...

    async def claim_next(
        self, conversation_key: str, lease_seconds: int = 900
    ) -> InboxJob | None: ...

    async def extend(self, job: InboxJob, lease_seconds: int) -> None: ...

    async def finished(self, update_id: int) -> bool: ...

    async def complete(self, job: InboxJob) -> None: ...

    async def retry(self, job: InboxJob, error: str) -> None: ...

    async def abandon(self, job: InboxJob, error: str) -> None: ...


class PostgresUpdateInbox:
    """A small leased queue in the control plane's PostgreSQL database."""

    def __init__(self, dsn: str, schema: str = "public") -> None:
        if not dsn:
            raise ValueError("a PostgreSQL database URL is required")
        if not SCHEMA_NAME.fullmatch(schema):
            raise ValueError(f"not a usable schema name: {schema!r}")
        self.dsn = dsn
        self.schema = schema
        self.table = f'"{schema}"."telegram_updates"'

    async def _connection(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        return await psycopg.AsyncConnection.connect(
            self.dsn, autocommit=True, row_factory=dict_row
        )

    async def setup(self) -> None:
        """Create the inbox table as an explicit deployment migration."""

        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                await cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table} (
                        update_id   BIGINT PRIMARY KEY,
                        run_id      TEXT,
                        payload     JSONB NOT NULL,
                        state       TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (state IN ('pending', 'running', 'done')),
                        lease_token TEXT,
                        lease_until TIMESTAMPTZ,
                        attempts    INTEGER NOT NULL DEFAULT 0,
                        last_error  TEXT,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                # Additive, for a queue table that already holds rows. Existing
                # updates get a NULL run id and are handled as "not measured",
                # never as an error.
                await cursor.execute(
                    f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS run_id TEXT"
                )
                # Also additive, and a row without it keeps the old behaviour of
                # being claimed on its own. Nothing is rewritten and nothing is
                # dropped: an update queued by the previous deployment is still
                # answered by the next worker that reaches it.
                await cursor.execute(
                    f"ALTER TABLE {self.table}"
                    " ADD COLUMN IF NOT EXISTS conversation_key TEXT"
                )
                # The out-of-band lane, as a column rather than as an absent
                # conversation key: a row with no key means "queued before
                # conversations existed", and a control update means the
                # opposite of that — it knows its conversation and deliberately
                # does not wait for it. A default rather than a nullable
                # column, so an existing row is an ordinary update and the
                # claim does not have to spell that out.
                await cursor.execute(
                    f"ALTER TABLE {self.table}"
                    " ADD COLUMN IF NOT EXISTS control BOOLEAN NOT NULL DEFAULT FALSE"
                )
                # The claim's whole query: one conversation's unfinished
                # updates, oldest first. Without it the lease scans the queue.
                await cursor.execute(
                    f'CREATE INDEX IF NOT EXISTS "telegram_updates_conversation"'
                    f" ON {self.table} (conversation_key, state, update_id)"
                )

    async def enqueue(
        self,
        update_id: int,
        payload: dict[str, Any],
        run_id: str = "",
        conversation_key: str = "",
        control: bool = False,
    ) -> EnqueueResult:
        from psycopg.types.json import Jsonb

        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"INSERT INTO {self.table}"
                    " (update_id, run_id, conversation_key, control, payload)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (update_id) DO NOTHING RETURNING state",
                    (
                        update_id,
                        run_id or None,
                        conversation_key or None,
                        control,
                        Jsonb(payload),
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is not None:
                    should_spawn, stored_run_id = True, run_id
                else:
                    await cursor.execute(
                        f"SELECT state, run_id, lease_until < CURRENT_TIMESTAMP AS expired"
                        f" FROM {self.table} WHERE update_id = %s",
                        (update_id,),
                    )
                    row = await cursor.fetchone()
                    should_spawn = bool(
                        row
                        and (
                            row["state"] == "pending"
                            or (row["state"] == "running" and row["expired"])
                        )
                    )
                    # The stored run id wins over the one just generated. A
                    # second delivery of one update is the same turn seen twice,
                    # not two turns.
                    stored_run_id = (row or {}).get("run_id") or ""
        # Every unfinished update asks for a worker, held conversation or not.
        # From 2026-08-30 to 2026-09-07 the spawn was suppressed while a live
        # lease held the conversation, to spare a burst a container per
        # message; the price was that a conversation whose worker died was
        # freed by its lease and then waited for the next message to start
        # anyone (ISS-0063). Now the worker started for a held conversation
        # waits out one lease (`TelegramUpdateWorker._claim`), and a burst
        # costs a few containers idling a minute.
        return EnqueueResult(update_id, should_spawn, stored_run_id)

    # What a claim may take: never a finished update, and never one another
    # container is still working on. An expired lease is fair game, because the
    # only thing that leaves one behind is a worker that died.
    UNFINISHED = (
        "(state = 'pending' OR (state = 'running' AND lease_until < CURRENT_TIMESTAMP))"
    )

    async def claim(self, update_id: int, lease_seconds: int = 900) -> InboxJob | None:
        """Claim work for the conversation this update belongs to.

        Not necessarily this update. A worker is started with the id that woke
        it, but what it owes the person is their oldest unanswered message, and
        a burst arrives as several ids whose workers race to be first. Reading
        the key here rather than trusting the caller keeps one writer of it: the
        front door that queued the row.
        """

        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"SELECT conversation_key, control FROM {self.table}"
                    " WHERE update_id = %s",
                    (update_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                key = row["conversation_key"] or ""
                if key and not row["control"]:
                    return await self._claim_conversation(connection, key, lease_seconds)
                # A control update, or one queued before the column existed.
                # One row, on its own, exactly as it was accepted — which for a
                # control update is the whole point: it must not wait behind the
                # turn it is about.
                return await self._lease(
                    connection,
                    f"WHERE update_id = %s AND {self.UNFINISHED}",
                    (update_id,),
                    lease_seconds,
                )

    async def claim_next(
        self, conversation_key: str, lease_seconds: int = 900
    ) -> InboxJob | None:
        """The next update this conversation is owed, if it is owed one.

        Called by a worker that has just finished one, so a burst is answered in
        order by the container that is already warm instead of by a race.
        """

        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if not conversation_key:
            return None
        connection = await self._connection()
        async with connection:
            return await self._claim_conversation(
                connection, conversation_key, lease_seconds
            )

    async def _claim_conversation(
        self, connection: Any, conversation_key: str, lease_seconds: int
    ) -> InboxJob | None:
        """Take one conversation's oldest unfinished update, or nothing.

        The advisory lock and the claim are one transaction: two workers holding
        different updates of the same conversation would otherwise both find
        nothing running and both start.
        """

        async with connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (lock_id(conversation_key),)
                )
            return await self._lease(
                connection,
                "WHERE update_id = (SELECT update_id"
                f" FROM {self.table} WHERE conversation_key = %s AND NOT control"
                f" AND {self.UNFINISHED}"
                " ORDER BY update_id LIMIT 1)"
                f" AND NOT EXISTS (SELECT 1 FROM {self.table} AS busy"
                " WHERE busy.conversation_key = %s AND busy.state = 'running'"
                " AND NOT busy.control"
                " AND busy.lease_until >= CURRENT_TIMESTAMP)",
                (conversation_key, conversation_key),
                lease_seconds,
            )

    async def _lease(
        self,
        connection: Any,
        where: str,
        values: tuple[Any, ...],
        lease_seconds: int,
    ) -> InboxJob | None:
        token = uuid.uuid4().hex
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {self.table} SET state = 'running', lease_token = %s,"
                " lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),"
                " attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP"
                f" {where}"
                " RETURNING update_id, payload, run_id, conversation_key, control,"
                " attempts,"
                " EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at)) AS waited",
                (token, lease_seconds, *values),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return InboxJob(
            int(row["update_id"]),
            dict(row["payload"]),
            token,
            run_id=row["run_id"] or "",
            queued_ms=max(0, int(float(row["waited"] or 0.0) * 1000)),
            conversation_key=row["conversation_key"] or "",
            control=bool(row["control"]),
            attempts=int(row["attempts"] or 1),
        )

    async def extend(self, job: InboxJob, lease_seconds: int) -> None:
        """A heartbeat: the worker holding this claim is still alive."""

        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {self.table} SET lease_until ="
                    " CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),"
                    " updated_at = CURRENT_TIMESTAMP"
                    " WHERE update_id = %s AND lease_token = %s AND state = 'running'",
                    (lease_seconds, job.update_id, job.lease_token),
                )

    async def finished(self, update_id: int) -> bool:
        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"SELECT state FROM {self.table} WHERE update_id = %s", (update_id,)
                )
                row = await cursor.fetchone()
        return row is not None and row["state"] == "done"

    async def complete(self, job: InboxJob) -> None:
        await self._finish(job, "done", None)

    async def take_pending(self, conversation_key: str, after: int) -> list[dict[str, Any]]:
        """Hand a conversation's waiting messages to the turn that is running.

        One statement, `pending` to `done`, so a taken row belongs to the
        caller and to nobody else: `claim_next` never takes a done row, and
        a row another worker holds is `running` and is not touched. Only
        messages, only after `after` (the running turn's own update id),
        never a control row. Marked in `last_error` so the table says
        where the update went.
        """

        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {self.table} SET state = 'done', last_error = %s,"
                    " updated_at = CURRENT_TIMESTAMP"
                    " WHERE conversation_key = %s AND state = 'pending' AND NOT control"
                    " AND update_id > %s AND payload ? 'message'"
                    " RETURNING update_id, payload, run_id",
                    (TAKEN, conversation_key, after),
                )
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def release(self, update_id: int) -> None:
        """Put a taken row back, for a message the turn could not read."""

        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {self.table} SET state = 'pending', last_error = NULL,"
                    " updated_at = CURRENT_TIMESTAMP"
                    " WHERE update_id = %s AND state = 'done' AND last_error = %s",
                    (update_id, TAKEN),
                )

    async def retry(self, job: InboxJob, error: str) -> None:
        await self._finish(job, "pending", error[:1000])

    async def abandon(self, job: InboxJob, error: str) -> None:
        """Give up on one update, keeping why, so it stops being claimed.

        Finished rather than failed, because `state` answers one question — may
        a worker take this? — and the answer here is no. The reason is in
        `last_error`, and the turn's own row already says it failed.
        """

        await self._finish(job, "done", error[:1000])

    async def _finish(self, job: InboxJob, state: str, error: str | None) -> None:
        connection = await self._connection()
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {self.table} SET state = %s, lease_token = NULL,"
                    " lease_until = NULL, last_error = %s, updated_at = CURRENT_TIMESTAMP"
                    " WHERE update_id = %s AND lease_token = %s",
                    (state, error, job.update_id, job.lease_token),
                )

