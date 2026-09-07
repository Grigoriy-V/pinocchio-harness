"""The persistence contract, apart from any one database.

Conversations, summaries and long-term facts outlive the process and the model.
This module says what a store must do; `store.py` says how SQLite does it, and a
second implementation for the deployed profile answers the same questions.

Scope is part of the contract, not a convention on top of it. Every operation
that can reach across conversations — listing threads, saving a fact, searching
facts — takes the owning user explicitly and has no default. A store that let a
caller omit the owner would answer one person's question with another person's
memory, so the omission is made impossible rather than discouraged.

Operations keyed by a thread do not repeat the user: a thread belongs to exactly
one owner from the moment it is created, so the thread identifier already
carries the scope. `thread_owner` is how a caller checks that ownership before
acting on a thread it was handed.

`set_active_thread` is the exception that proves it. It names both, because it
is handed a thread identifier that came from outside — a pressed button — and
the pairing is exactly what it has to verify rather than assume.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from app.models import Message

# The single owner of everything created by the local profile. The deployed
# profile passes real identifiers instead; this constant is the answer to
# "whose is it" on a machine where the question has only one answer.
LOCAL_USER_ID = "local-user"


@dataclass(frozen=True)
class Thread:
    """Enough about a conversation to choose it without opening it."""

    id: str
    user_id: str
    created_at: str
    updated_at: str
    messages: int
    opening: str


@dataclass(frozen=True)
class Compaction:
    """One fold: which messages the summary came to stand for, and why.

    The record 4.6b reads to recover what a summary lost. `through` is the
    position the summary covers after this fold, `folded` how many messages
    this fold newly covered, `trigger` the reason — `count`, `size`, `forced`
    or `asked`.
    """

    thread_id: str
    through: int
    folded: int
    trigger: str
    summary_chars: int
    created_at: str


@dataclass(frozen=True)
class Hit:
    """One stored message found by its words: where it is, and what it said."""

    thread_id: str
    position: int
    role: str
    created_at: str
    text: str


@dataclass(frozen=True)
class TurnContextRecords:
    """Durable records needed to construct one model turn."""

    summary: str | None
    summarized_through: int
    messages: list[Message]
    facts: list[str]


class ConversationStore(ABC):
    """Durable conversations, summaries and facts for one deployment."""

    # --- threads -------------------------------------------------------------

    @abstractmethod
    def ensure_thread(self, thread_id: str, user_id: str) -> None:
        """Create the thread under this owner if it does not exist yet.

        An existing thread keeps its original owner; ownership is decided once,
        when the conversation first appears.
        """

    @abstractmethod
    def threads(self, user_id: str) -> list[Thread]:
        """This user's conversations, most recently touched first.

        The order describes activity and nothing else. Which conversation the
        person is in is `active_thread`, so writing to one thread cannot move
        somebody into it.
        """

    @abstractmethod
    def thread_owner(self, thread_id: str) -> str | None:
        """Who owns this thread, or `None` if there is no such thread."""

    @abstractmethod
    def delete_thread(self, thread_id: str) -> bool:
        """Delete one conversation, preserving separately approved facts.

        A user whose chosen conversation was the deleted one is left with no
        choice rather than a dangling one.
        """

    # --- the chosen conversation ---------------------------------------------

    @abstractmethod
    def active_thread(self, user_id: str) -> str | None:
        """The conversation this user chose, or `None` if they have not chosen.

        `None` is also the answer once the chosen conversation is gone, so a
        caller never has to handle an identifier that no longer resolves.
        """

    @abstractmethod
    def set_active_thread(self, user_id: str, thread_id: str) -> None:
        """Record which conversation this user is in.

        Raises `KeyError` when the thread does not exist *or* belongs to
        somebody else. One answer for both on purpose: distinguishing them
        would tell a caller that a thread it may not have exists.
        """

    # --- messages ------------------------------------------------------------

    @abstractmethod
    def append(self, thread_id: str, messages: Iterable[Message], user_id: str) -> int:
        """Append messages, creating the thread under `user_id` if it is new.

        Returns the new message count.
        """

    @abstractmethod
    def messages(
        self, thread_id: str, after: int = -1, limit: int | None = None
    ) -> list[Message]:
        """Messages of a thread in order, optionally only those past a position."""

    @abstractmethod
    def message_count(self, thread_id: str) -> int:
        ...

    @abstractmethod
    def search_messages(
        self, query: str, user_id: str, *, thread_id: str | None = None, limit: int = 8
    ) -> list[Hit]:
        """Full-text search over this user's stored messages, best match first.

        `thread_id` narrows it to one conversation, which must be this user's;
        without it every conversation of theirs is searched, and nobody
        else's ever is. What is found is what was said, not a summary of it.
        """

    # --- rolling summary -----------------------------------------------------

    @abstractmethod
    def summary(self, thread_id: str) -> tuple[str | None, int]:
        """The thread's summary and the position it covers through."""

    @abstractmethod
    def set_summary(self, thread_id: str, text: str, through: int) -> None:
        """Replace the summary of an existing thread.

        Raises `KeyError` for an unknown thread: a summary of a conversation
        that was never stored would have no owner and no messages to cover.
        """

    @abstractmethod
    def record_compaction(
        self, thread_id: str, *, through: int, folded: int, trigger: str, summary_chars: int
    ) -> None:
        """Record one fold of an existing thread. Raises `KeyError` for an unknown one."""

    @abstractmethod
    def compactions(self, thread_id: str) -> list[Compaction]:
        """Every fold of a thread, oldest first."""

    # --- facts ---------------------------------------------------------------

    @abstractmethod
    def remember(self, text: str, user_id: str, thread_id: str | None = None) -> int:
        """Save one durable fact for this user. `thread_id` is provenance only."""

    @abstractmethod
    def search(self, query: str, user_id: str, limit: int = 5) -> list[str]:
        """Full-text search over this user's facts, best match first."""

    @abstractmethod
    def facts(self, user_id: str, limit: int = 50) -> list[str]:
        """This user's most recently saved facts."""

    @abstractmethod
    def forget(self, user_id: str) -> int:
        """Drop every fact saved for this user; returns how many went.

        For a bare run (roadmap 15): a scenario that saves a fact must find
        none before it, or the model answers "already saved" and saves
        nothing (deployed H, 2026-09-07).
        """

    def turn_context(
        self,
        thread_id: str,
        user_id: str,
        query: str,
        retrieved_facts: int,
    ) -> TurnContextRecords:
        """Read every durable record needed for one turn.

        Local stores can answer this through their ordinary operations. A
        networked store overrides the boundary so one logical read need not
        become several network round-trips.
        """

        summary, through = self.summary(thread_id)
        messages = self.messages(thread_id, after=through - 1)
        facts = self.search(query, user_id, limit=retrieved_facts) if query else []
        return TurnContextRecords(summary, through, messages, facts)

    # --- lifecycle -----------------------------------------------------------

    @abstractmethod
    def close(self) -> None:
        ...

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()
