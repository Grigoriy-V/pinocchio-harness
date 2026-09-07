"""A scenario starts from nothing, in both profiles (2026-09-05).

Deployed, the probe user's threads and workspace outlive a run; the third G
sample of 2026-09-05 answered from the history of the two before it. No
model, no network.
"""

from __future__ import annotations

from pathlib import Path

from app.memory import SqliteStore
from app.models import ContentPart, Message
from scripts.loop_live import start_clean


class Probe:
    def __init__(self, store: SqliteStore, checkpoints: Path) -> None:
        self.store = store
        self.checkpoints = checkpoints
        self.user_id = "probe"


async def test_the_selected_threads_and_the_workspace_are_emptied(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "Task Board").mkdir(parents=True)
    (root / "Task Board" / "index.html").write_text("<h1>old</h1>", encoding="utf-8")
    (root / "notes.txt").write_text("old", encoding="utf-8")
    with SqliteStore(str(tmp_path / "c.db")) as store:
        for thread in ("chat-g", "chat-b"):
            store.append(thread, [Message(role="user", content=[ContentPart(kind="text", text="hi")])], "probe")
        store.remember("likes Antonovka", "probe", "chat-h")
        store.remember("likes plums", "someone-else", "t")

        await start_clean(Probe(store, tmp_path / "missing.sqlite3"), frozenset("G"), root)

        assert store.messages("chat-g") == []
        assert len(store.messages("chat-b")) == 1  # not selected: untouched
        # A bare run has no memory either (deployed H, 2026-09-07), and only
        # the probe user's.
        assert store.facts("probe") == []
        assert store.facts("someone-else") == ["likes plums"]
    assert list(root.iterdir()) == []
