"""What the durable queue promises, asserted against PostgreSQL itself.

There is no offline half of this file, and that is deliberate. The rules below
live in one `UPDATE` statement and an advisory lock; a stand-in that passes them
has demonstrated something about the stand-in. `tests/fakes.py` holds the same
rules for tests whose subject is the worker rather than the queue, and this file
is what keeps the two honest — so `AGENT_TEST_DATABASE_URL` is what makes the
queue answerable, exactly as it is for the conversation store.

The live defect these are about: a screenshot and the question that followed it
were sent seconds apart, ran in two containers and were answered out of order.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest

from ui.telegram.inbox import PostgresUpdateInbox

POSTGRES_DSN = os.environ.get("AGENT_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="AGENT_TEST_DATABASE_URL is not set; the queue is only testable live",
)

ALICE = "person-alice"
BOB = "person-bob"


@pytest.fixture
async def inbox() -> AsyncIterator[PostgresUpdateInbox]:
    """A queue in a schema of its own, dropped whatever the test did to it."""

    import psycopg

    schema = f"inbox_{uuid.uuid4().hex[:12]}"
    queue = PostgresUpdateInbox(POSTGRES_DSN, schema)
    await queue.setup()
    try:
        yield queue
    finally:
        connection = await psycopg.AsyncConnection.connect(POSTGRES_DSN, autocommit=True)
        async with connection:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def payload(update_id: int) -> dict[str, object]:
    return {"update_id": update_id, "message": {"text": "hello"}}


async def queue(
    inbox: PostgresUpdateInbox, *update_ids: int, key: str = ALICE
) -> None:
    for update_id in update_ids:
        await inbox.enqueue(
            update_id, payload(update_id), run_id=f"run-{update_id}", conversation_key=key
        )


async def test_one_conversation_runs_one_update_at_a_time(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, 7)

    first = await inbox.claim(5)
    assert first is not None and first.update_id == 5
    assert await inbox.claim(7) is None
    assert await inbox.claim_next(ALICE) is None

    await inbox.complete(first)
    second = await inbox.claim(7)
    assert second is not None and second.update_id == 7


async def test_the_oldest_update_is_answered_first(inbox: PostgresUpdateInbox) -> None:
    """The worker is woken by whichever spawn won, not by the older message."""

    await queue(inbox, 5, 7)

    claimed = await inbox.claim(7)

    assert claimed is not None
    assert claimed.update_id == 5
    assert claimed.conversation_key == ALICE
    assert claimed.run_id == "run-5"


async def test_a_finished_update_still_names_its_conversation(
    inbox: PostgresUpdateInbox,
) -> None:
    """How a hand-off works: any id of the conversation finds the rest of it."""

    await queue(inbox, 5, 7)
    done = await inbox.claim(5)
    assert done is not None
    await inbox.complete(done)

    following = await inbox.claim(5)

    assert following is not None and following.update_id == 7


async def test_two_people_do_not_wait_for_each_other(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, key=ALICE)
    await queue(inbox, 6, key=BOB)

    mine = await inbox.claim(5)
    theirs = await inbox.claim(6)

    assert mine is not None and theirs is not None


async def test_a_dead_worker_does_not_hold_a_conversation_forever(
    inbox: PostgresUpdateInbox,
) -> None:
    """Nothing releases the lease of a container that disappeared."""

    await queue(inbox, 5)
    lost = await inbox.claim(5, lease_seconds=1)
    assert lost is not None

    await asyncio.sleep(1.2)
    recovered = await inbox.claim(5, lease_seconds=30)

    assert recovered is not None
    assert recovered.update_id == 5
    assert recovered.lease_token != lost.lease_token


async def test_a_failed_turn_returns_to_the_queue(inbox: PostgresUpdateInbox) -> None:
    await queue(inbox, 5)
    failed = await inbox.claim(5)
    assert failed is not None

    await inbox.retry(failed, "RuntimeError: failed turn")
    again = await inbox.claim(5)

    assert again is not None
    assert again.lease_token != failed.lease_token


async def test_an_update_queued_before_conversations_existed_is_answered_alone(
    inbox: PostgresUpdateInbox,
) -> None:
    """The column was added to a table that already held rows."""

    await inbox.enqueue(5, payload(5))
    await inbox.enqueue(7, payload(7))

    first = await inbox.claim(5)
    second = await inbox.claim(7)

    assert first is not None and first.update_id == 5
    assert second is not None and second.update_id == 7
    assert first.conversation_key == ""


async def test_a_redelivered_update_keeps_one_identity(
    inbox: PostgresUpdateInbox,
) -> None:
    first = await inbox.enqueue(5, payload(5), run_id="run-5", conversation_key=ALICE)
    second = await inbox.enqueue(5, payload(5), run_id="run-again", conversation_key=ALICE)

    assert first.run_id == "run-5"
    # The stored identity wins: one update seen twice is one turn, not two.
    assert second.run_id == "run-5"
    # And it is still owed an answer, so asking for a worker again is correct.
    assert second.should_spawn is True


async def test_a_claimed_update_is_not_spawned_a_second_time(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5)
    claimed = await inbox.claim(5, lease_seconds=300)
    assert claimed is not None

    again = await inbox.enqueue(5, payload(5), conversation_key=ALICE)

    assert again.should_spawn is False


async def test_a_claim_reports_how_many_times_it_has_been_tried(
    inbox: PostgresUpdateInbox,
) -> None:
    """What bounds retrying an update the worker can never answer."""

    await queue(inbox, 5)
    first = await inbox.claim(5)
    assert first is not None and first.attempts == 1

    await inbox.retry(first, "RuntimeError: again")
    second = await inbox.claim(5)

    assert second is not None and second.attempts == 2


async def test_an_abandoned_update_stops_being_claimed(
    inbox: PostgresUpdateInbox,
) -> None:
    """Giving up has to mean the conversation moves on, not that it waits."""

    await queue(inbox, 5, 7)
    doomed = await inbox.claim(5)
    assert doomed is not None

    await inbox.abandon(doomed, "RuntimeError: nobody can answer this")
    following = await inbox.claim(7)

    assert following is not None and following.update_id == 7
    assert await inbox.claim(5) is None


async def test_a_control_update_is_claimed_while_the_conversation_runs(
    inbox: PostgresUpdateInbox,
) -> None:
    """The out-of-band lane, which is the reason the column exists.

    A `/stop` that waits for the turn it exists to stop is not a slow stop.
    """

    await queue(inbox, 5)
    await inbox.enqueue(8, payload(8), conversation_key=ALICE, control=True)
    running = await inbox.claim(5)
    assert running is not None

    control = await inbox.claim(8)

    assert control is not None
    assert control.update_id == 8
    assert control.control is True


async def test_a_control_update_is_never_what_a_conversation_claims(
    inbox: PostgresUpdateInbox,
) -> None:
    """It is answered beside the conversation, so the drain must not take it."""

    await inbox.enqueue(5, payload(5), conversation_key=ALICE, control=True)
    await queue(inbox, 7)

    claimed = await inbox.claim(7)

    assert claimed is not None and claimed.update_id == 7
    assert await inbox.claim_next(ALICE) is None


async def test_a_running_control_update_does_not_hold_a_conversation_up(
    inbox: PostgresUpdateInbox,
) -> None:
    await inbox.enqueue(5, payload(5), conversation_key=ALICE, control=True)
    await queue(inbox, 7)
    control = await inbox.claim(5)
    assert control is not None

    assert await inbox.claim(7) is not None


async def test_an_update_queued_before_the_control_lane_existed_is_ordinary(
    inbox: PostgresUpdateInbox,
) -> None:
    """The migration itself, against a table that already holds rows.

    The column is taken away and the rows are written as the previous
    deployment wrote them; `setup()` is then what a deploy runs. Every one of
    them has to come back meaning exactly what it meant.
    """

    import psycopg
    from psycopg.types.json import Jsonb

    connection = await psycopg.AsyncConnection.connect(POSTGRES_DSN, autocommit=True)
    async with connection:
        await connection.execute(f"ALTER TABLE {inbox.table} DROP COLUMN control")
        for update_id in (5, 7):
            await connection.execute(
                f"INSERT INTO {inbox.table} (update_id, run_id, conversation_key, payload)"
                " VALUES (%s, %s, %s, %s)",
                (update_id, f"run-{update_id}", ALICE, Jsonb(payload(update_id))),
            )

    await inbox.setup()
    claimed = await inbox.claim(7)

    assert claimed is not None and claimed.update_id == 5
    assert claimed.control is False


async def test_a_conversation_already_running_does_not_ask_for_another_worker(
    inbox: PostgresUpdateInbox,
) -> None:
    """A burst must not ask for a container per message.

    Live on 2026-08-30: two Telegram albums of four documents arrived as eight
    updates 1.2 s apart and every one of them asked for a worker. Seven found
    the conversation held and exited without claiming anything, so the spawns
    were work that could not have happened.
    """

    await queue(inbox, 5)
    running = await inbox.claim(5)
    assert running is not None

    following = await inbox.enqueue(7, payload(7), conversation_key=ALICE)

    assert following.should_spawn is False
    # Queued all the same: what is suppressed is the container, never the row.
    assert await inbox.claim_next(ALICE) is None
    await inbox.complete(running)
    drained = await inbox.claim_next(ALICE)
    assert drained is not None and drained.update_id == 7


async def test_a_conversation_whose_worker_died_still_asks_for_one(
    inbox: PostgresUpdateInbox,
) -> None:
    """The lease has to be live, or a dead container silences the conversation.

    A running row with an expired lease is exactly what a killed worker leaves
    behind, and it is the case that most needs a new one started.
    """

    await queue(inbox, 5)
    lost = await inbox.claim(5, lease_seconds=1)
    assert lost is not None
    await asyncio.sleep(1.2)

    following = await inbox.enqueue(7, payload(7), conversation_key=ALICE)

    assert following.should_spawn is True


async def test_another_person_is_not_held_up_by_a_running_conversation(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, key=ALICE)
    running = await inbox.claim(5)
    assert running is not None

    theirs = await inbox.enqueue(7, payload(7), conversation_key=BOB)

    assert theirs.should_spawn is True


async def test_a_control_update_always_asks_for_a_worker(
    inbox: PostgresUpdateInbox,
) -> None:
    """`/stop` must not be the thing that waits for the turn it is about."""

    await queue(inbox, 5)
    running = await inbox.claim(5)
    assert running is not None

    control = await inbox.enqueue(8, payload(8), conversation_key=ALICE, control=True)

    assert control.should_spawn is True


async def test_an_update_with_no_conversation_still_asks_for_a_worker(
    inbox: PostgresUpdateInbox,
) -> None:
    """Rows queued before the key existed are answered one at a time, as before."""

    await inbox.enqueue(5, payload(5))
    running = await inbox.claim(5)
    assert running is not None

    following = await inbox.enqueue(7, payload(7))

    assert following.should_spawn is True


async def test_setting_the_queue_up_twice_changes_nothing(
    inbox: PostgresUpdateInbox,
) -> None:
    """Every deployment runs it, so it has to be safe on a populated table."""

    await queue(inbox, 5)
    await inbox.setup()

    claimed = await inbox.claim(5)

    assert claimed is not None and claimed.update_id == 5


# --- the running turn takes what arrived behind it (item 20) -----------------


async def test_a_running_turn_takes_the_messages_queued_behind_it(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, 7, 8)
    first = await inbox.claim(5)
    assert first is not None

    taken = await inbox.take_pending(ALICE, 5)

    assert sorted(row["update_id"] for row in taken) == [7, 8]
    assert all(row["payload"]["message"]["text"] == "hello" for row in taken)
    # Done: the drain after the turn finds nothing, and nothing runs them twice.
    await inbox.complete(first)
    assert await inbox.claim_next(ALICE) is None
    assert await inbox.take_pending(ALICE, 5) == []


async def test_a_control_row_and_another_persons_row_are_not_taken(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, 9)
    await queue(inbox, 6, key=BOB)
    await inbox.enqueue(7, payload(7), run_id="run-7", conversation_key=ALICE, control=True)
    first = await inbox.claim(5)
    assert first is not None

    assert [row["update_id"] for row in await inbox.take_pending(ALICE, 5)] == [9]
    assert await inbox.claim(7) is not None, "the control row is still there to claim"
    assert await inbox.claim(6) is not None


async def test_a_row_another_worker_holds_is_not_taken(inbox: PostgresUpdateInbox) -> None:
    """A lease that expired after a death: the later worker's claim wins."""

    await queue(inbox, 5, 7)
    await inbox.claim(5, lease_seconds=0)
    later = await inbox.claim(7)
    assert later is not None and later.update_id == 5, "the oldest, resumed by the later worker"

    assert [row["update_id"] for row in await inbox.take_pending(ALICE, 5)] == [7]


async def test_a_released_row_is_answered_as_its_own_turn(
    inbox: PostgresUpdateInbox,
) -> None:
    await queue(inbox, 5, 7)
    first = await inbox.claim(5)
    assert first is not None
    assert [row["update_id"] for row in await inbox.take_pending(ALICE, 5)] == [7]

    await inbox.release(7)
    await inbox.complete(first)
    following = await inbox.claim_next(ALICE)
    assert following is not None and following.update_id == 7
