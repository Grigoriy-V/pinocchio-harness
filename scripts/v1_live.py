"""A live check of what version 1 added. Needs the model server.

    .venv\\Scripts\\python.exe -m scripts.v1_live

Five things the offline tests can only fake: the context length actually
reported by the running server, the token count it charges for a real request,
a fold triggered by that count, a conversation list built from a real database,
and what a dead endpoint costs before it gives up.

Writes only into a temporary directory.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.agent.runtime import Agent, text_message
from app.config import ModelSettings
from app.context import ContextPolicy
from app.memory import SqliteStore
from app.models.openai_compatible import BackendError, OpenAICompatibleBackend


@asynccontextmanager
async def open_agent(
    room: Path, fraction: float = 0.6, policy: ContextPolicy | None = None
) -> AsyncIterator[Agent]:
    """An agent that is closed even when the section fails.

    On Windows a live SQLite connection keeps the temporary directory undeletable,
    so a failed assertion would otherwise be buried under a PermissionError.
    """

    agent = Agent(
        backend=OpenAICompatibleBackend(ModelSettings()),
        store=SqliteStore(room / "memory.sqlite3"),
        workspace=room / "workspace",
        policy=policy,
        checkpoints=room / "checkpoints.sqlite3",
        context_fraction=fraction,
    )
    try:
        yield agent
    finally:
        await agent.aclose()


def show(title: str) -> None:
    print(f"\n=== {title} ===")


async def limit_and_fill(room: Path) -> None:
    show("the limit comes from the server, the size from the answer")
    async with open_agent(room) as agent:
        print(f"  context_limit: {await agent.backend.context_limit()}")
        print(f"  budget:        {await agent.budget()}")

        started = time.monotonic()
        async for message in agent.steps("chat", text_message("Say hello in five words.")):
            body = " ".join(part.text or "" for part in message.content).strip()
            print(f"  {message.role}: {body[:120]}")
        fill = await agent.fill()
        print(f"  fill: {fill.used} / {fill.budget} tokens ({fill.fraction:.0%})")
        print(f"  turn: {time.monotonic() - started:.1f} s")
        assert fill.used > 0, "the server reported no prompt tokens"
        assert fill.budget is not None, "the server did not state its context length"


async def folding_by_tokens(room: Path) -> None:
    show("a request over budget folds the conversation, though it is short")
    # A deliberately tiny share, so even these few turns overshoot it, and a
    # count trigger far out of reach: nothing here folds because it is long.
    policy = ContextPolicy(keep_turns=1)
    async with open_agent(room, fraction=0.02, policy=policy) as agent:
        print(f"  budget: {await agent.budget()} tokens")

        for turn in ("Name one colour.", "Name another one.", "And a third."):
            async for _ in agent.steps("short", text_message(turn)):
                pass
            fill = await agent.fill()
            _, through = agent.store.summary("short")
            print(f"  used {fill.used:>5} tokens, summarized through {through}")

        summary, through = agent.store.summary("short")
        print(f"  summary: {summary!r}")
        assert through > 0, "the request went over budget and nothing was folded"
        assert agent.store.message_count("short") > through, "folding deleted messages"


async def conversation_list(room: Path) -> None:
    show("the conversations, as the chooser will show them")
    async with open_agent(room) as agent:
        for thread in agent.threads():
            print(f"  {thread.id:<8} {thread.messages:>3} messages  {thread.opening[:48]!r}")
        assert len(agent.threads()) == 2, "both conversations should be listed"


async def a_dead_endpoint(room: Path) -> None:
    show("a server that is not there costs three attempts and says so")
    settings = ModelSettings(
        endpoint="http://127.0.0.1:9/v1", retries=2, retry_backoff=0.1, timeout=2.0
    )
    backend = OpenAICompatibleBackend(settings)
    print(f"  context_limit: {await backend.context_limit()}")

    started = time.monotonic()
    try:
        await backend.invoke([text_message("anyone there?")])
    except BackendError as error:
        print(f"  after {time.monotonic() - started:.1f} s: {error}")
    else:
        raise AssertionError("a dead endpoint answered")
    finally:
        await backend.aclose()


async def run(room: Path) -> None:
    (room / "workspace").mkdir()
    await limit_and_fill(room)
    await folding_by_tokens(room)
    await conversation_list(room)
    await a_dead_endpoint(room)


def main() -> None:
    with tempfile.TemporaryDirectory() as room:
        asyncio.run(run(Path(room)))


if __name__ == "__main__":
    main()
