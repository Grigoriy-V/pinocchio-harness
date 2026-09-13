"""Chainlit in front of the agent.

This file adapts between Chainlit's world and the project's own: attachments
become `ContentPart`s, agent messages become Chainlit messages and steps. It
holds no logic about tools, memory or context — that lives in `app/`, so a
second consumer can be added without moving any of it.

    .venv\\Scripts\\python.exe -m chainlit run ui/chainlit_app.py -w
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.attachments import AttachmentBytes, AttachmentError, admit_uploads


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
from chainlit.server import app as server
from fastapi.responses import JSONResponse

from app.agent.commands import (
    CONTEXT_COMMANDS,
    MODE_COMMANDS,
    PLAN_COMMANDS,
    WORKSPACE_COMMANDS,
    compact_reply,
    context_reply,
    mode_reply,
    plan_reply,
    short,
    workspace_reply,
)
from app.agent.folder import folder_of, last_folder, set_folder
from app.agent.runtime import Agent, create_agent, user_workspace
from app.agent.status import CreditsWatch, status_of
from app.agent.stop import MemoryStopRequests
from app.capabilities import Delivery
from app.config import AgentSettings
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message
from ui.chainlit_history import LOCAL_USER_IDENTIFIER, MemoryStoreDataLayer

IMAGE = "image"
AUDIO = "audio"
CONFIRM_TIMEOUT = 600
DELIVERY = Delivery(media=(IMAGE, AUDIO), place="the Chainlit web app")

# Offered in the composer's menu; typed with a slash they mean the same. The
# words of every reply are `app.agent.commands`, shared with Telegram.
COMMANDS = [
    {"id": "compact", "description": "Fold the older part of this conversation now", "icon": "fold-vertical"},
    {"id": "plan", "description": "on | off: a task list for longer work", "icon": "list-checks"},
    {"id": "mode", "description": "full | careful: whether changes ask first", "icon": "shield"},
    {"id": "context", "description": "small | normal | large, or what the next request is made of", "icon": "layers"},
    {"id": "workspace", "description": "<absolute path> | off: the folder this conversation works in", "icon": "folder-open"},
]
COMMAND_NAMES = ", ".join(f"/{command['id']}" for command in COMMANDS)


@cl.header_auth_callback
async def local_auth(_headers: Any) -> cl.User:
    """A single transparent identity for this loopback-only application."""

    return cl.User(identifier=LOCAL_USER_IDENTIFIER, display_name="Local user")


@cl.data_layer
def history_layer() -> MemoryStoreDataLayer:
    settings = AgentSettings()
    return MemoryStoreDataLayer(
        SqliteStore(settings.database),
        checkpoints=settings.checkpoints,
        workspace=user_workspace(settings.workspace, LOCAL_USER_ID),
    )


def uploads_of(incoming: cl.Message) -> list[AttachmentBytes]:
    """Read what Chainlit saved, without deciding what the agent accepts."""

    uploads = []
    for element in incoming.elements or ():
        path = getattr(element, "path", None)
        if not path:
            continue
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise AttachmentError(f"{Path(path).name}: uploaded file is unavailable") from exc
        uploads.append(
            AttachmentBytes(
                name=getattr(element, "name", None) or Path(path).name,
                media_type=getattr(element, "mime", None) or None,
                data=data,
            )
        )
    return uploads


def to_message(incoming: cl.Message, workspace: Path) -> Message:
    """One message as the turn's input.

    A picture or a sound goes to the model; any other file is saved under
    `inbox/` in the workspace and named in the turn. The admission is
    `app.attachments`, the same one Telegram has.
    """

    parts: list[ContentPart] = []
    if incoming.content:
        parts.append(ContentPart(kind="text", text=incoming.content))
    parts.extend(admit_uploads(uploads_of(incoming), workspace))
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


class Turn:
    """One collapsed step per turn, holding every tool call it made.

    Chainlit shows each step as its own row, so a turn of twelve calls was
    twelve rows. The calls are the turn's children now: a finished turn is
    one line that opens on a click, the way the references show it. The
    answers stay top-level, which is why the parent is named on each child
    rather than entered as a context: a message sent inside a step's
    context is filed under it.
    """

    def __init__(self) -> None:
        self.step: cl.Step | None = None
        self.calls = 0

    async def child(self, name: str) -> cl.Step:
        if self.step is None:
            self.step = cl.Step(name="working", type="run")
            await self.step.send()
        self.calls += 1
        step = cl.Step(name=name, type="tool", parent_id=self.step.id)
        await step.send()
        return step

    async def close(self) -> None:
        if self.step is None:
            return
        plural = "" if self.calls == 1 else "s"
        self.step.name = f"{self.calls} tool call{plural}"
        await self.step.update()


async def render(produced: AsyncIterator[Message], turn: Turn) -> None:
    """Show messages as their nodes finish: a tool call as a step under the
    turn's, an answer as a message."""

    steps: dict[str, cl.Step] = {}
    async for message in produced:
        if message.role == "tool":
            step = steps.pop(message.tool_call_id or "", None)
            if step is None:
                # The call was announced before a restart, so there is no open
                # step to fill in; show the result on its own instead of losing it.
                step = await turn.child("tool result")
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
            step = await turn.child(call.name)
            step.input = call.arguments
            await step.update()
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
        create_agent(agent_settings=settings, delivery=DELIVERY, stops=stops, open=True),
        stops,
    )


# The status card (`public/status.js`) asks this route while it is open. A
# loopback app with one person: the session that opened last is the one
# in front of them, so the route needs no id from the page.
_current: dict[str, Any] = {}
_credits = CreditsWatch()


async def status_route() -> JSONResponse:
    agent: Agent | None = _current.get("agent")
    thread_id: str | None = _current.get("thread_id")
    if agent is None or thread_id is None:
        return JSONResponse({"error": "no session"}, status_code=404)
    credits = await _credits.read()
    # `context_report` lists the toolbox, and the first listing of a process
    # waits on the MCP servers with a blocking call (ISS-0070): in a thread,
    # so the app is not held while it does.
    status = await asyncio.to_thread(
        status_of, agent, thread_id, credits, _credits.session_spend()
    )
    return JSONResponse(status.as_dict())


# Ahead of Chainlit's catch-all, which serves its page for any path.
server.add_api_route("/status", status_route, methods=["GET"])
server.router.routes.insert(0, server.router.routes.pop())


def command_of(incoming: cl.Message) -> tuple[str, str] | None:
    """The command and its argument, from the composer's menu or a slash."""

    text = (incoming.content or "").strip()
    chosen = getattr(incoming, "command", None)
    if chosen:
        return f"/{chosen.lower()}", text.lower()
    if text.startswith("/"):
        head, _, argument = text.partition(" ")
        return head.lower(), argument.strip().lower()
    return None


async def say(agent: Agent, thread_id: str, text: str, asked: str | None = None) -> None:
    """A line of the harness's own, shown now and kept in the thread's notes
    so a reopened conversation shows it where it was said; the model never
    reads it. `asked` is the command it answers, kept the same way."""

    if asked:
        agent.store.add_note(thread_id, "user", asked, agent.user_id)
    agent.store.add_note(thread_id, "assistant", text, agent.user_id)
    await cl.Message(content=text).send()


async def handle_command(agent: Agent, thread_id: str, incoming: cl.Message) -> bool:
    """Answer a command without a turn. True when the message was one."""

    command = command_of(incoming)
    if command is None:
        return False
    head, argument = command
    if head in PLAN_COMMANDS:
        reply = plan_reply(agent, argument)
    elif head in MODE_COMMANDS:
        reply = mode_reply(agent, argument)
    elif head in CONTEXT_COMMANDS:
        reply = context_reply(agent, thread_id, argument)
    elif head in WORKSPACE_COMMANDS:
        # The path as typed: lower-casing it would name another folder on
        # a case-sensitive disk.
        typed = (incoming.content or "").strip()
        raw = typed if getattr(incoming, "command", None) else typed.partition(" ")[2].strip()
        reply = workspace_reply(agent, thread_id, raw if raw.lower() != "off" else "off")
    elif head == "/compact":
        reply = await compact_reply(agent, thread_id)
    else:
        reply = f"No such command: {head}. Commands: {COMMAND_NAMES}."
    await say(agent, thread_id, reply, asked=incoming.content or head)
    return True


async def drive(
    agent: Agent, thread_id: str, produced: AsyncIterator[Message] | None = None
) -> None:
    """Run a turn to its end, answering every question it stops on.

    Without a stream it only finishes what is already waiting, which is how a
    turn interrupted before a restart is picked up.
    """

    turn = Turn()
    _, covered = agent.store.summary(thread_id)
    history_before = agent.context_report(thread_id).layers["history"]
    if produced is not None:
        await render(produced, turn)
    while (question := await agent.pending(thread_id)) is not None:
        await render(agent.resume(thread_id, await confirm(question)), turn)
    await turn.close()
    _, now_covered = agent.store.summary(thread_id)
    if now_covered > covered:
        # The turn folded older conversation on its own; said the way the
        # references say it, and kept.
        freed = max(0, history_before - agent.context_report(thread_id).layers["history"])
        await say(agent, thread_id, f"Compacted conversation · saved {short(freed)} tokens")


async def open_session(agent: Agent, stops: MemoryStopRequests, thread_id: str) -> None:
    cl.user_session.set("agent", agent)
    cl.user_session.set("stops", stops)
    cl.user_session.set("thread_id", thread_id)
    await cl.context.emitter.set_commands(COMMANDS)
    _current.update(agent=agent, thread_id=thread_id)


@cl.on_chat_start
async def start() -> None:
    agent, stops = create_runtime_with_stops()
    # The websocket session id is ephemeral and differs from the canonical
    # thread id that Chainlit puts in its sidebar and data layer.
    thread_id = canonical_thread_id(cl.context.session)
    # A new conversation starts in the folder the last one worked in.
    inherited = last_folder(agent.workspace)
    if inherited is not None and folder_of(agent.workspace, thread_id) is None:
        set_folder(agent.workspace, thread_id, inherited)
    await open_session(agent, stops, thread_id)


@cl.on_chat_resume
async def resume(thread: dict[str, Any]) -> None:
    agent, stops = create_runtime_with_stops()
    thread_id = thread["id"]
    await open_session(agent, stops, thread_id)
    if await agent.pending(thread_id) is not None:
        await cl.Message(content="This conversation stopped waiting for an answer.").send()
        await drive(agent, thread_id)


@cl.on_message
async def on_message(incoming: cl.Message) -> None:
    agent: Agent = cl.user_session.get("agent")
    thread_id: str = cl.user_session.get("thread_id")

    if await handle_command(agent, thread_id, incoming):
        return
    try:
        message = to_message(incoming, agent.workspace)
    except AttachmentError as exc:
        await cl.Message(content=f"Upload refused: {exc}.").send()
        return

    await drive(agent, thread_id, agent.steps(thread_id, message, next(_sequence)))


@cl.on_chat_end
async def end() -> None:
    agent: Agent | None = cl.user_session.get("agent")
    if agent is not None:
        if _current.get("agent") is agent:
            _current.clear()
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
