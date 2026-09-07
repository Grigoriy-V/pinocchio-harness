"""A bare run drops the probe user's facts, and nobody else's."""

from __future__ import annotations

from app.memory import SqliteStore


def test_forget_drops_one_users_facts_only() -> None:
    with SqliteStore() as store:
        store.remember("likes Antonovka", "alice", "t1")
        store.remember("likes pears", "alice", "t1")
        store.remember("likes plums", "bob", "t2")

        assert store.forget("alice") == 2

        assert store.facts("alice") == []
        assert store.search("Antonovka", "alice") == []
        assert store.facts("bob") == ["likes plums"]
        assert store.forget("alice") == 0
