"""The SQLite implementation of the persistence contract.

One file holds threads, their messages, a rolling summary per thread, and
long-term facts. Facts are not scoped to a thread — the point of a long-term
fact is that a later conversation finds it — but they are scoped to a user,
because the point stops holding across people.

The store is synchronous. A local agent spends its time waiting on the model,
not on SQLite, and an async wrapper would buy nothing but a dependency. The
deployed profile does not share that property and gets its own implementation of
`ConversationStore` rather than an async disguise over this one.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.memory.base import Compaction, LOCAL_USER_ID, ConversationStore, Hit, Thread
from app.memory.records import (
    dump_failure,
    dump_content,
    dump_tool_calls,
    message_text,
    now,
    opening_text,
    row_to_message,
    stored_text,
)
from app.models import Message

# Bumped whenever the schema changes. `PRAGMA user_version` is SQLite's own
# integer on the file, so the database states its shape rather than the code
# guessing it from which columns happen to exist.
SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL DEFAULT 'local-user',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    summary            TEXT,
    summarized_through INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL REFERENCES threads(id),
    position     INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_calls   TEXT,
    tool_call_id TEXT,
    failure      TEXT,
    -- The message's words, for full-text search: `records.message_text`.
    -- Not an expression over `content`, which carries base64 media. Schema 4.
    text         TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (thread_id, position)
);

-- One row per fold: which position the summary came to cover, how many
-- messages that newly took in, and why. What 4.6b reads to recover exactly
-- what a summary stands for. Schema 3.
CREATE TABLE IF NOT EXISTS compactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id     TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    through       INTEGER NOT NULL,
    folded        INTEGER NOT NULL,
    trigger       TEXT NOT NULL,
    summary_chars INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS compactions_by_thread ON compactions(thread_id, id);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT 'local-user',
    thread_id  TEXT,
    created_at TEXT NOT NULL
);

-- Which conversation each person is in. Separate from `threads` because it is
-- a fact about the person, not about any one conversation, and because a
-- column on `threads` would need every other row cleared to move somebody.
--
-- `ON DELETE SET NULL` is what keeps a choice from outliving its conversation:
-- deleting a thread leaves the chooser with no choice, which the reader below
-- turns back into "not chosen yet".
CREATE TABLE IF NOT EXISTS user_state (
    user_id          TEXT PRIMARY KEY,
    active_thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS threads_by_user ON threads(user_id);
CREATE INDEX IF NOT EXISTS facts_by_user ON facts(user_id);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(text, content='facts', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
    USING fts5(text, content='messages', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

TOKEN = re.compile(r"\w+", re.UNICODE)


def match_query(query: str) -> str:
    """Turn free text into an FTS5 MATCH expression.

    The query comes from a model, so it may contain anything. Every token is
    quoted and the rest is dropped, because an unescaped `-` or `*` is an FTS5
    syntax error rather than a poor search.
    """

    tokens = TOKEN.findall(query)
    return " OR ".join(f'"{token}"' for token in tokens)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def migrate(db: sqlite3.Connection) -> int:
    """Bring a database up to `SCHEMA_VERSION`, returning the version found.

    Version 0 is either an empty file or a database written before conversations
    and facts had owners. In the second case every existing row belongs to the
    person who has been using the local profile all along, so the backfill hands
    them to `LOCAL_USER_ID` rather than inventing an owner or refusing to open.

    Version 1 is that database with no record of which conversation anyone is
    in. Nothing existing changes shape, so re-running the schema is the whole
    migration: the new table appears and every conversation is still there. A
    person who was in the middle of one is given it back by the interface, which
    adopts their most recent thread the first time it finds no choice recorded.

    Version 2 has no `failure` column on messages and no `compactions` table.
    The column is added empty — every stored row predates the typed outcome
    and reads as a message without one — and the table appears from the
    schema. No row is rewritten.

    Version 3 has no `text` column and no index over it. The column is added
    and filled from what each row already holds, then the FTS table is built
    from it. `text` is derived, so this is the one migration that writes to
    every message row, and what it writes is nothing the row did not say.
    """

    found = int(db.execute("PRAGMA user_version").fetchone()[0])
    if found >= SCHEMA_VERSION:
        return found

    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table in ("threads", "facts"):
        if table in tables and "user_id" not in _columns(db, table):
            db.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL"
                f" DEFAULT '{LOCAL_USER_ID}'"
            )
    if "messages" in tables and "failure" not in _columns(db, "messages"):
        db.execute("ALTER TABLE messages ADD COLUMN failure TEXT")
    if "messages" in tables and "text" not in _columns(db, "messages"):
        db.execute("ALTER TABLE messages ADD COLUMN text TEXT")
        rows = db.execute(
            "SELECT id, content, tool_calls, failure FROM messages WHERE text IS NULL"
        ).fetchall()
        db.executemany(
            "UPDATE messages SET text = ? WHERE id = ?",
            [(stored_text(r["content"], r["tool_calls"], r["failure"]), r["id"]) for r in rows],
        )
    db.executescript(SCHEMA)
    db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()
    return found


class SqliteStore(ConversationStore):
    """The project's SQLite file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        migrate(self._db)

    # --- threads -------------------------------------------------------------

    def ensure_thread(self, thread_id: str, user_id: str) -> None:
        stamp = now()
        self._db.execute(
            "INSERT INTO threads (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (thread_id, user_id, stamp, stamp),
        )
        self._db.commit()

    def threads(self, user_id: str) -> list[Thread]:
        """This user's conversations, most recently touched first.

        Carries what a chooser needs — how long it is and how it began — because
        a thread identifier is a session UUID and says nothing to anyone.
        """

        rows = self._db.execute(
            "SELECT t.id, t.user_id, t.created_at, t.updated_at,"
            "  (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS messages,"
            "  (SELECT m.content FROM messages m WHERE m.thread_id = t.id AND m.role = 'user'"
            "   ORDER BY m.position LIMIT 1) AS opening"
            " FROM threads t WHERE t.user_id = ?"
            " ORDER BY t.updated_at DESC, t.rowid DESC",
            (user_id,),
        ).fetchall()
        return [
            Thread(
                id=row["id"],
                user_id=row["user_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                messages=row["messages"],
                opening=opening_text(row["opening"]),
            )
            for row in rows
        ]

    def thread_owner(self, thread_id: str) -> str | None:
        row = self._db.execute(
            "SELECT user_id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return None if row is None else row["user_id"]

    def delete_thread(self, thread_id: str) -> bool:
        """Delete one conversation while preserving separately approved facts."""

        with self._db:
            exists = self._db.execute(
                "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if exists is None:
                return False
            # Facts are the user's memory, not the conversation's. Removing their
            # deleted-conversation provenance avoids a dangling reference without
            # forgetting the fact or changing who it belongs to.
            self._db.execute(
                "UPDATE facts SET thread_id = NULL WHERE thread_id = ?", (thread_id,)
            )
            self._db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            # `user_state` clears itself through its foreign key.
            self._db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return True

    # --- the chosen conversation ---------------------------------------------

    def active_thread(self, user_id: str) -> str | None:
        """The conversation this user chose, if it is still theirs and still there."""

        row = self._db.execute(
            "SELECT s.active_thread_id AS id FROM user_state s"
            " JOIN threads t ON t.id = s.active_thread_id AND t.user_id = s.user_id"
            " WHERE s.user_id = ?",
            (user_id,),
        ).fetchone()
        return None if row is None else row["id"]

    def set_active_thread(self, user_id: str, thread_id: str) -> None:
        """Record the chosen conversation, refusing one that is not this user's.

        The ownership test is the `WHERE EXISTS` of the same statement rather
        than a read before it: a check that is a separate statement is a check
        another writer can invalidate between the two.
        """

        with self._db:
            cursor = self._db.execute(
                "INSERT INTO user_state (user_id, active_thread_id, updated_at)"
                " SELECT ?, ?, ? WHERE EXISTS"
                " (SELECT 1 FROM threads WHERE id = ? AND user_id = ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                "  active_thread_id = excluded.active_thread_id,"
                "  updated_at = excluded.updated_at",
                (user_id, thread_id, now(), thread_id, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no such thread for this user: {thread_id!r}")

    # --- messages ------------------------------------------------------------

    def append(self, thread_id: str, messages: Iterable[Message], user_id: str) -> int:
        """Append messages to a thread and return the new message count."""

        self.ensure_thread(thread_id, user_id)
        position = self._db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()["next"]
        stamp = now()
        for message in messages:
            self._db.execute(
                "INSERT INTO messages"
                " (thread_id, position, role, content, tool_calls, tool_call_id, failure,"
                "  text, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    position,
                    message.role,
                    dump_content(message.content),
                    dump_tool_calls(message.tool_calls),
                    message.tool_call_id,
                    dump_failure(message.failure),
                    message_text(message),
                    stamp,
                ),
            )
            position += 1
        self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (stamp, thread_id))
        self._db.commit()
        return position

    def messages(
        self, thread_id: str, after: int = -1, limit: int | None = None
    ) -> list[Message]:
        """Messages of a thread in order, optionally only those past a position."""

        sql = "SELECT * FROM messages WHERE thread_id = ? AND position > ? ORDER BY position"
        parameters: list[Any] = [thread_id, after]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        return [row_to_message(row) for row in self._db.execute(sql, parameters)]

    def message_count(self, thread_id: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row["n"]

    def search_messages(
        self, query: str, user_id: str, *, thread_id: str | None = None, limit: int = 8
    ) -> list[Hit]:
        match = match_query(query)
        if not match:
            return []
        sql = (
            "SELECT m.thread_id, m.position, m.role, m.created_at, m.text"
            " FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
            " JOIN threads t ON t.id = m.thread_id"
            " WHERE messages_fts MATCH ? AND t.user_id = ?"
        )
        parameters: list[Any] = [match, user_id]
        if thread_id is not None:
            sql += " AND m.thread_id = ?"
            parameters.append(thread_id)
        sql += " ORDER BY bm25(messages_fts), m.id DESC LIMIT ?"
        parameters.append(limit)
        rows = self._db.execute(sql, parameters).fetchall()
        return [
            Hit(
                thread_id=row["thread_id"],
                position=row["position"],
                role=row["role"],
                created_at=row["created_at"],
                text=row["text"] or "",
            )
            for row in rows
        ]

    # --- rolling summary -----------------------------------------------------

    def summary(self, thread_id: str) -> tuple[str | None, int]:
        """The thread's summary and the position it covers through."""

        row = self._db.execute(
            "SELECT summary, summarized_through FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None, 0
        return row["summary"], row["summarized_through"]

    def set_summary(self, thread_id: str, text: str, through: int) -> None:
        cursor = self._db.execute(
            "UPDATE threads SET summary = ?, summarized_through = ?, updated_at = ? WHERE id = ?",
            (text, through, now(), thread_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"no such thread: {thread_id!r}")
        self._db.commit()

    def record_compaction(
        self, thread_id: str, *, through: int, folded: int, trigger: str, summary_chars: int
    ) -> None:
        if self.thread_owner(thread_id) is None:
            raise KeyError(f"no such thread: {thread_id!r}")
        self._db.execute(
            "INSERT INTO compactions"
            " (thread_id, through, folded, trigger, summary_chars, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, through, folded, trigger, summary_chars, now()),
        )
        self._db.commit()

    def compactions(self, thread_id: str) -> list[Compaction]:
        rows = self._db.execute(
            "SELECT thread_id, through, folded, trigger, summary_chars, created_at"
            " FROM compactions WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        ).fetchall()
        return [Compaction(**dict(row)) for row in rows]

    # --- facts ---------------------------------------------------------------

    def remember(self, text: str, user_id: str, thread_id: str | None = None) -> int:
        text = text.strip()
        if not text:
            raise ValueError("a fact cannot be empty")
        cursor = self._db.execute(
            "INSERT INTO facts (text, user_id, thread_id, created_at) VALUES (?, ?, ?, ?)",
            (text, user_id, thread_id, now()),
        )
        self._db.commit()
        return int(cursor.lastrowid or 0)

    def search(self, query: str, user_id: str, limit: int = 5) -> list[str]:
        """Full-text search over this user's facts, best match first."""

        match = match_query(query)
        if not match:
            return []
        rows = self._db.execute(
            "SELECT f.text FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid"
            " WHERE facts_fts MATCH ? AND f.user_id = ?"
            " ORDER BY bm25(facts_fts) LIMIT ?",
            (match, user_id, limit),
        ).fetchall()
        return [row["text"] for row in rows]

    def facts(self, user_id: str, limit: int = 50) -> list[str]:
        rows = self._db.execute(
            "SELECT text FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [row["text"] for row in rows]

    def forget(self, user_id: str) -> int:
        with self._db:
            gone = self._db.execute("DELETE FROM facts WHERE user_id = ?", (user_id,)).rowcount
        return int(gone or 0)

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._db.close()
