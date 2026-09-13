"""Chainlit in front of the agent.

This file adapts between Chainlit's world and the project's own: attachments
become `ContentPart`s, agent messages become Chainlit messages and steps. It
holds no logic about tools, memory or context — that lives in `app/`, so a
second consumer can be added without moving any of it.

    .venv\\Scripts\\python.exe -m chainlit run ui/chainlit_app.py -w
"""

from __future__ import annotations

import itertools
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.attachments import AttachmentError, AttachmentSource, load_attachments


def _auth_secret() -> str:
    """Return a stable local signing key without putting it in the repository."""

    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    secret_path = local_data / "local-multimodal-agent" / "chainlit-auth-secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return secret_path.read_text(encoding="utf-8").strip()
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(secret)
    return secret


# Chainlit requires authentication before it exposes native chat history. The
# transparent single-user login is only a UI identity; the signing key lives in
# the user's local application data, not in source control or conversation data.
os.environ.setdefault("CHAINLIT_AUTH_SECRET", _auth_secret())

import chainlit as cl

from app.agent.runtime import Agent, create_agent
from app.agent.stop import MemoryStopRequests
from app.capabilities import Delivery
from app.config import AgentSettings
from app.memory import SqliteStore
from app.models import ContentPart, Message
from ui.chainlit_history import LOCAL_USER_IDENTIFIER, MemoryStoreDataLayer

IMAGE = "image"
AUDIO = "audio"
CONFIRM_TIMEOUT = 600
DELIVERY = Delivery(media=(IMAGE, AUDIO), place="the Chainlit web app")


@cl.header_auth_callback
async def local_auth(_headers: Any) -> cl.User:
    """A single transparent identity for this loopback-only application."""

    return cl.User(identifier=LOCAL_USER_IDENTIFIER, display_name="Local user")


@cl.data_layer
def history_layer() -> MemoryStoreDataLayer:
    settings = AgentSettings()
    return MemoryStoreDataLayer(
        SqliteStore(settings.database), checkpoints=settings.checkpoints
    )


def attachment_sources(incoming: cl.Message) -> list[AttachmentSource]:
    """Translate Chainlit metadata without deciding what the agent accepts."""

    sources = []
    for element in incoming.elements or ():
        path = getattr(element, "path", None)
        if path:
            sources.append(
                AttachmentSource(
                    path=Path(path),
                    media_type=getattr(element, "mime", None),
                    name=getattr(element, "name", None) or Path(path).name,
                )
            )
    return sources


def to_message(incoming: cl.Message) -> Message:
    parts: list[ContentPart] = []
    if incoming.content:
        parts.append(ContentPart(kind="text", text=incoming.content))
    parts.extend(load_attachments(attachment_sources(incoming)))
    if not parts:
        raise AttachmentError("the message has no text or usable attachments")
    return Message(role="user", content=parts)


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content).strip()


def media_parts(message: Message, *, outbound_only: bool = False) -> list[ContentPart]:
    """The parts that have to be shown rather than said, in the order they came."""

    return [
        part
        for part in message.content
        if part.kind != "text" and (part.outbound or not outbound_only)
    ]


def canonical_thread_id(session: Any) -> str:
    """Use Chainlit's persistent conversation id, never its websocket id."""

    return str(session.thread_id)


def attachments(message: Message, *, outbound_only: bool = False) -> list[Any]:
    """Show the pictures and the sound, not just the words about them.

    Without this a reopened conversation is a transcript with holes in it: the
    image the whole exchange is about was stored, and then not shown.
    """

    shown = []
    for index, part in enumerate(media_parts(message, outbound_only=outbound_only)):
        element = {IMAGE: cl.Image, AUDIO: cl.Audio}.get(part.kind, cl.File)
        shown.append(
            element(
                name=part.name or f"{part.kind}-{index}",
                content=part.data,
                mime=part.media_type,
                display="inline",
            )
        )
    return shown


async def render(produced: AsyncIterator[Message]) -> None:
    """Show messages as their nodes finish: a tool call as a step, an answer as a message."""

    steps: dict[str, cl.Step] = {}
    async for message in produced:
        if message.role == "tool":
            step = steps.pop(message.tool_call_id or "", None)
            if step is None:
                # The call was announced before a restart, so there is no open
                # step to fill in; show the result on its own instead of losing it.
                step = cl.Step(name="tool result", type="tool")
                await step.send()
            step.output = spoken(message) if message.failure is not None else "Completed."
            await step.update()
            shown = attachments(message, outbound_only=True)
            if shown:
                await cl.Message(content="", elements=shown).send()
            continue

        body = spoken(message)
        shown = attachments(message)
        if body or shown:
            await cl.Message(content=body, elements=shown).send()
        for call in message.tool_calls:
            step = cl.Step(name=call.name, type="tool")
            step.input = call.arguments
            await step.send()
            steps[call.id] = step
        if not body and not shown and not message.tool_calls:
            await cl.Message(content="(no answer)").send()


async def confirm(question: list[dict[str, Any]]) -> dict[str, bool]:
    """Ask about each call the agent stopped for. No answer means no."""

    answers: dict[str, bool] = {}
    for call in question:
        arguments = json.dumps(call["arguments"], indent=2, ensure_ascii=False)
        response = await cl.AskActionMessage(
            content=f"Run `{call['name']}`?\n```json\n{arguments}\n```",
            actions=[
                cl.Action(name="approve", payload={"approved": True}, label="Run it"),
                cl.Action(name="decline", payload={"approved": False}, label="Don't"),
            ],
            timeout=CONFIRM_TIMEOUT,
        ).send()
        answers[call["id"]] = bool((response or {}).get("payload", {}).get("approved"))
    return answers


# Every event of this session in order, so a stop can be told from the turn it
# is meant to stop. Telegram gets the same number from its own update ids; here
# there is nothing to take it from, so the session counts its own.
_sequence = itertools.count(1)


def create_runtime_with_stops() -> tuple[Agent, MemoryStopRequests]:
    """One agent and the place a stop for it is recorded.

    `attachments` above renders both kinds of media inline, which is what the
    model is told it may produce. Chainlit runs the turn and the stop in one process, so memory is the whole
    truth here — unlike the deployed profile, where they are two containers.
    """

    stops = MemoryStopRequests()
    # The local profile: the store and the checkpoints are the SQLite files
    # beside this process, whatever `.env` says. The developer's `.env` names
    # the deployed database for the deploy scripts, and this app's history
    # panel above reads the SQLite store; an agent writing to the other
    # database would split one conversation in two (and async psycopg does
    # not run on Windows's default loop, so the turn died before the model).
    settings = AgentSettings(database_url="")
    return (
        create_agent(agent_settings=settings, delivery=DELIVERY, stops=stops),
        stops,
    )


async def report_fill(agent: Agent) -> None:
    """State how large the last request was, counted by the model itself."""

    fill = await agent.fill()
    if fill is None:
        return
    share = fill.fraction
    body = (
        f"context {fill.used} / {fill.budget} tokens ({share:.0%})"
        if share is not None
        else f"context {fill.used} tokens"
    )
    await cl.Message(content=body, author="context").send()


async def drive(
    agent: Agent, thread_id: str, produced: AsyncIterator[Message] | None = None
) -> None:
    """Run a turn to its end, answering every question it stops on.

    Without a stream it only finishes what is already waiting, which is how a
    turn interrupted before a restart is picked up.
    """

    if produced is not None:
        await render(produced)
    while (question := await agent.pending(thread_id)) is not None:
        await render(agent.resume(thread_id, await confirm(question)))
    await report_fill(agent)


@cl.on_chat_start
async def start() -> None:
    agent, stops = create_runtime_with_stops()
    # The websocket session id is ephemeral and differs from the canonical
    # thread id that Chainlit puts in its sidebar and data layer.
    thread_id = canonical_thread_id(cl.context.session)
    cl.user_session.set("agent", agent)
    cl.user_session.set("stops", stops)
    cl.user_session.set("thread_id", thread_id)


@cl.on_chat_resume
async def resume(thread: dict[str, Any]) -> None:
    agent, stops = create_runtime_with_stops()
    thread_id = thread["id"]
    cl.user_session.set("agent", agent)
    cl.user_session.set("stops", stops)
    cl.user_session.set("thread_id", thread_id)
    if await agent.pending(thread_id) is not None:
        await cl.Message(content="This conversation stopped waiting for an answer.").send()
        await drive(agent, thread_id)


@cl.on_message
async def on_message(incoming: cl.Message) -> None:
    agent: Agent = cl.user_session.get("agent")
    thread_id: str = cl.user_session.get("thread_id")

    try:
        message = to_message(incoming)
    except AttachmentError as exc:
        await cl.Message(
            content=f"Upload refused: {exc}."
        ).send()
        return

    await drive(agent, thread_id, agent.steps(thread_id, message, next(_sequence)))


@cl.on_chat_end
async def end() -> None:
    agent: Agent | None = cl.user_session.get("agent")
    if agent is not None:
        await agent.aclose()


@cl.on_stop
async def stop() -> None:
    """Chainlit's stop button, as the loop's own stop.

    It records a request rather than cancelling a coroutine: the turn is what
    decides where it can safely be interrupted, and it looks between steps.
    """

    agent: Agent | None = cl.user_session.get("agent")
    stops: MemoryStopRequests | None = cl.user_session.get("stops")
    if agent is None or stops is None:
        return
    await stops.request(agent.user_id, next(_sequence))
