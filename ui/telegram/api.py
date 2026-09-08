"""The Telegram Bot API wire format, and nothing else.

Only this module knows what Telegram's JSON looks like. It is written directly
over `httpx` for the same reason the model backend is: the surface actually
needed here is six methods, while a bot framework would bring its own event
loop, handler registry and lifecycle to a project where LangGraph already owns
orchestration.

Model text is never handed to Telegram's own Markdown parsers. Those reject
unbalanced punctuation, and model output is exactly where unbalanced
punctuation comes from, so an assistant that passed its answers straight
through would intermittently fail to send them. What happens instead is that
`markdown.py` renders the answer into Telegram HTML here, under three rules
that together make formatting unable to lose a message:

*Everything from outside is escaped.* Headings in `Formatted.build` are this
project's own words; the bodies are not, and are escaped every time.

*A message is only ever split between whole blocks.* `Formatted` keeps its
blocks rather than one joined string, so a piece can never begin inside a
`<b>`. A block too long to send on its own degrades the whole message to plain
text instead.

*A refusal is answered with the plain reading.* Every block carries the plain
text it means, so a message Telegram will not parse is resent unformatted
rather than dropped.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Self

import httpx

from app.config import TelegramSettings
from app.telemetry.trace import spent
from ui.telegram import markdown
from ui.telegram.wire import SETTLED_APPROVED, SETTLED_REJECTED

# Telegram refuses a longer message outright rather than truncating it.
MAX_MESSAGE_CHARS = 4096

# How long a rate limit may hold one call before it is treated as a failure, and
# how many times a single call may be held. Telegram answers a flood with
# `429 Too Many Requests` and `parameters.retry_after`, which is a "later", not a
# "no" — and it says exactly how much later.
#
# Found live on 2026-08-30: seven long answers in four and a half minutes, each
# written into the chat by repeated edits, and Telegram refused the eighth with
# `retry after 32`. The turn had already spent 22 s of GPU producing a complete
# 770-token answer, which was then thrown away, because a refused delivery fails
# the turn — and a failed turn is re-run from the beginning, model calls
# included, rather than having its answer re-sent.
#
# Sixty seconds because the waits Telegram asks for are seconds to a minute,
# while the deployed worker is killed at 600 s and a turn may already have spent
# 300 of them. Two holds, because a limit that survives being waited out twice
# is a flood this turn cannot talk its way out of.
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_RATE_LIMIT_HOLDS = 2
# Documents Telegram will accept from a bot.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
# Telegram's own cap for `sendPhoto`, which is much smaller than a document's.
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class TelegramError(RuntimeError):
    """Telegram was reached but refused, or could not be reached at all."""


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Cut text into sendable pieces, preferring a line break to a hard cut.

    An answer that is one character too long is not an error the user should
    ever see, and a piece cut mid-word is not one they should have to read.
    """

    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


@dataclass(frozen=True)
class Formatted:
    """Marked-up text held as blocks, each with the plain text it means.

    Blocks rather than one string because that is what makes splitting safe:
    the only cut points are between them, and a cut between two blocks cannot
    land inside a tag. The plain half of each block is not decoration either --
    it is what gets sent if Telegram refuses to parse the marked-up half.
    """

    blocks: tuple[tuple[str, str], ...]

    @property
    def html(self) -> str:
        return "\n\n".join(block for block, _ in self.blocks if block)

    @property
    def plain(self) -> str:
        return "\n\n".join(block for _, block in self.blocks if block)

    @classmethod
    def build(cls, blocks: Iterable[tuple[str, str]]) -> Formatted:
        """Compose from (heading, body) pairs; either side may be empty.

        Headings are this project's own words and bodies are anything, model
        output included, so only the body is ever escaped -- and it is escaped
        every time.
        """

        built: list[tuple[str, str]] = []
        for heading, body in blocks:
            if not heading and not body:
                continue
            marked = "\n".join(
                piece
                for piece in (f"<b>{escape(heading)}</b>" if heading else "", escape(body))
                if piece
            )
            built.append((marked, "\n".join(piece for piece in (heading, body) if piece)))
        return cls(tuple(built))

    @classmethod
    def from_markdown(cls, text: str) -> Formatted:
        """Render an ordinary Markdown answer, degrading rather than failing.

        The assistant's canonical text is ordinary Markdown and stays that way
        in the store; this is only how Telegram shows it. Any renderer failure,
        and any block whose markup does not come out balanced, becomes that
        block's plain reading -- a formatting bug must cost formatting, never
        the answer.
        """

        try:
            blocks = markdown.render(text)
        except Exception:  # noqa: BLE001 - presentation must not lose a reply
            blocks = [(escape(text or "", quote=False), text or "")]
        return cls(
            tuple(
                (marked, plain)
                if markdown.balanced(marked)
                else (escape(plain, quote=False), plain)
                for marked, plain in blocks
            )
        )


@dataclass(frozen=True)
class Piece:
    """One outgoing message: what to send, and what it means unformatted.

    `plain` is `None` when `text` is already plain, which is also how
    `send_message` knows whether to ask Telegram to parse it.
    """

    text: str
    plain: str | None = None


def pack(blocks: Iterable[tuple[str, str]], limit: int = MAX_MESSAGE_CHARS) -> list[Piece] | None:
    """Group whole blocks into sendable pieces, or refuse if one cannot fit.

    Refusing is the honest answer: a block longer than a message has no safe
    cut point inside it, and the caller's fallback -- the plain reading, which
    `split_message` may cut anywhere -- is better than half a tag.
    """

    pieces: list[Piece] = []
    marked: list[str] = []
    plain: list[str] = []
    for block, readable in blocks:
        if not block:
            continue
        if len(block) > limit:
            return None
        if marked and len("\n\n".join([*marked, block])) > limit:
            pieces.append(Piece("\n\n".join(marked), "\n\n".join(plain)))
            marked, plain = [], []
        marked.append(block)
        plain.append(readable)
    if marked:
        pieces.append(Piece("\n\n".join(marked), "\n\n".join(plain)))
    return pieces


def retry_after(body: dict[str, Any]) -> float | None:
    """How long Telegram asked us to wait, if that is what it refused for.

    Read from `parameters.retry_after`, which is where Telegram puts it, rather
    than parsed out of the description text — the number is structured and the
    sentence is not a contract. A rate limit with no usable number is treated as
    an ordinary refusal, because waiting for an unknown time is not a plan.
    """

    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        return None
    seconds = parameters.get("retry_after")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    return float(seconds) if seconds > 0 else None


def is_parse_refusal(error: Exception) -> bool:
    """Did Telegram reject the markup rather than the message itself?"""

    detail = str(error).lower()
    return "parse" in detail or "entities" in detail or "tag" in detail


def approval_keyboard(approve: str, decline: str) -> dict[str, Any]:
    """The two-button reply markup used for every consent question."""

    return {
        "inline_keyboard": [
            [
                {"text": "Run it", "callback_data": approve},
                {"text": "Don't", "callback_data": decline},
            ]
        ]
    }


def settled_keyboard(approved: bool, *, styled: bool = True) -> dict[str, Any]:
    """What an inline action becomes once it has actually been carried out.

    One button, because it is a status and no longer a choice. The word is the
    state and the colour is only an enhancement: `style` is a recent Bot API
    addition, so the caller retries without it if this version refuses it, and
    the result still reads correctly on a client that shows no colour at all.
    """

    button: dict[str, Any] = {
        "text": "\u2713 Approved" if approved else "\u2715 Rejected",
        "callback_data": SETTLED_APPROVED if approved else SETTLED_REJECTED,
    }
    if styled:
        button["style"] = "success" if approved else "danger"
    return {"inline_keyboard": [[button]]}


def conversations_keyboard(
    choices: Iterable[tuple[str, str]], close: str
) -> dict[str, Any]:
    """The conversation list: one button per conversation, and a way out.

    One per row rather than a grid, because the label is a sentence fragment
    from the conversation itself and two of them side by side are unreadable on
    a phone.
    """

    rows: list[list[dict[str, Any]]] = [
        [{"text": label, "callback_data": data}] for label, data in choices
    ]
    rows.append([{"text": "Close", "callback_data": close}])
    return {"inline_keyboard": rows}


def no_keyboard() -> dict[str, Any]:
    """What a list that has been closed leaves behind: its text, and no buttons."""

    return {"inline_keyboard": []}


@dataclass(frozen=True)
class BotCommand:
    """One entry of Telegram's own command menu."""

    command: str
    description: str


# The native menu, which is the product's navigation surface. `/check` is
# deliberately absent: it tries every capability for real and is a diagnostic,
# not one of the four things to offer a person as the product. It stays fully
# available as a typed command, and `tests/test_telegram_adapter.py` holds both
# halves of that to be true.
PRODUCT_COMMANDS = (
    BotCommand("new", "Start a new conversation"),
    BotCommand("chats", "Switch conversation"),
    BotCommand("can", "What I can see, hear, send and change"),
    BotCommand("agents", "Your standing instructions for how I work"),
    BotCommand("plan", "Turn my task list on or off"),
    BotCommand("mode", "Work freely in the workspace, or ask before every change"),
    BotCommand("context", "How much of this chat I carry, and how much I may"),
    BotCommand("compact", "Fold the older part of this chat into a summary now"),
    BotCommand("stop", "Stop the task running in this chat"),
    BotCommand("help", "What this assistant is and how to use it"),
)

# Telegram shows this in an empty chat, before the first message. Its own limit
# is 512 characters; being far shorter than that is the point.
BOT_DESCRIPTION = (
    "A personal multimodal assistant. Talk to it normally, or send images, "
    "voice messages and documents. It can read your files, use the web and "
    "carry out longer tasks, and it asks before consequential actions."
)

# Shown beside the bot's name in profiles and shared links. Telegram's limit
# for this one is 120 characters.
BOT_SHORT_DESCRIPTION = (
    "A personal multimodal assistant that reads, searches and gets things done."
)


class TelegramClient:
    """The handful of Bot API calls this adapter makes."""

    def __init__(
        self,
        settings: TelegramSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or TelegramSettings()
        if not self.settings.token:
            raise TelegramError("TELEGRAM_TOKEN is not configured")
        base = self.settings.api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{base}/bot{self.settings.token}",
            timeout=self.settings.timeout,
            transport=transport,
        )
        self._files = httpx.AsyncClient(
            base_url=f"{base}/file/bot{self.settings.token}",
            timeout=self.settings.timeout,
            transport=transport,
        )

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """One Bot API call, waiting out a rate limit rather than failing on it.

        Nothing else is retried here. A rate limit is the one refusal that
        carries its own remedy — Telegram says how many seconds to wait — so
        waiting is following the instruction, not guessing that another attempt
        might work.
        """

        for hold in range(MAX_RATE_LIMIT_HOLDS + 1):
            with spent("telegram_call", method=method):
                body = await self._post(method, payload)
            if body.get("ok"):
                return body.get("result")
            pause = retry_after(body)
            if pause is None or pause > MAX_RETRY_AFTER_SECONDS or hold == MAX_RATE_LIMIT_HOLDS:
                raise TelegramError(
                    f"telegram refused {method}: {body.get('description', 'no description')}"
                )
            # A shade over what was asked for. Waiting exactly the stated time
            # and arriving on the boundary is how a flood wait gets extended.
            await asyncio.sleep(pause + 0.5)
        raise AssertionError("unreachable")

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"/{method}", json=payload)
        except httpx.HTTPError as error:
            detail = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
            raise TelegramError(f"telegram could not be reached ({detail})") from error
        try:
            body = response.json()
        except ValueError as error:
            raise TelegramError(f"telegram returned no JSON for {method}") from error
        if not isinstance(body, dict):
            raise TelegramError(f"telegram returned no object for {method}")
        return body

    async def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self.settings.poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def send_message(
        self,
        chat_id: int,
        text: str | Formatted,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send text, splitting it when Telegram would refuse the length.

        Returns the last message sent, which is the one a caller may later edit.
        """

        pieces = self._render(text)
        sent = None
        for index, piece in enumerate(pieces):
            if not piece.text:
                continue
            payload: dict[str, Any] = {"chat_id": chat_id, "text": piece.text}
            if piece.plain is not None:
                payload["parse_mode"] = "HTML"
            # Buttons belong to the final piece; a keyboard attached to the first
            # would sit above the rest of the answer.
            if reply_markup is not None and index == len(pieces) - 1:
                payload["reply_markup"] = reply_markup
            sent = await self._send_piece(payload, piece)
        return sent

    async def _send_piece(self, payload: dict[str, Any], piece: Piece) -> Any:
        """Send one piece, falling back to its plain reading if refused.

        The renderer is careful and the balance check is stricter still, but
        Telegram's parser is the only authority on what Telegram accepts. When
        it says no, the answer is sent unformatted rather than lost — the plain
        reading of a block is never longer than its markup, so it still fits.
        """

        try:
            return await self._call("sendMessage", payload)
        except TelegramError as error:
            if piece.plain is None or not is_parse_refusal(error):
                raise
        plain = dict(payload, text=piece.plain)
        plain.pop("parse_mode", None)
        return await self._call("sendMessage", plain)

    @staticmethod
    def _render(text: str | Formatted) -> list[Piece]:
        """Choose between the formatted rendering and the plain one.

        Formatting survives only while every piece can be cut between whole
        blocks. When one block is too long to send by itself there is no safe
        cut inside it, so the whole message degrades to plain text: a piece
        beginning inside a `<b>` is refused by Telegram rather than shown
        unstyled.
        """

        if isinstance(text, Formatted):
            packed = pack(text.blocks)
            if packed is None:
                return [Piece(piece) for piece in split_message(text.plain) or [""]]
            return packed
        return [Piece(piece) for piece in split_message(text) or [""]]

    async def replace_message(
        self, chat_id: int, message_id: int, text: str | Formatted
    ) -> None:
        """Turn a message already in the chat into a finished, rendered one.

        This is how a preview a person watched grow becomes the answer itself
        instead of a second bubble appearing underneath it. Everything the
        answer needs beyond one message is sent after it, in order, so the
        length rules are the same ones `send_message` follows.

        Raises rather than degrading silently: the caller has a whole answer to
        fall back on, and would otherwise have no way to know it is needed.
        """

        pieces = [piece for piece in self._render(text) if piece.text]
        if not pieces:
            return
        await self._edit_piece(chat_id, message_id, pieces[0])
        for piece in pieces[1:]:
            payload: dict[str, Any] = {"chat_id": chat_id, "text": piece.text}
            if piece.plain is not None:
                payload["parse_mode"] = "HTML"
            await self._send_piece(payload, piece)

    async def _edit_piece(self, chat_id: int, message_id: int, piece: Piece) -> None:
        """Edit one piece into place, with the same fallbacks a send has."""

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": piece.text,
        }
        if piece.plain is not None:
            payload["parse_mode"] = "HTML"
        try:
            await self._call("editMessageText", payload)
            return
        except TelegramError as error:
            # An answer whose rendering matches what was already shown is
            # finished, not failed: Telegram simply has nothing to change.
            if "not modified" in str(error).lower():
                return
            if piece.plain is None or not is_parse_refusal(error):
                raise
        plain = dict(payload, text=piece.plain)
        plain.pop("parse_mode", None)
        try:
            await self._call("editMessageText", plain)
        except TelegramError as error:
            if "not modified" not in str(error).lower():
                raise

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text, ignoring Telegram's "nothing changed" refusal."""

        try:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": split_message(text)[-1] if text else "…",
                },
            )
        except TelegramError as error:
            if "not modified" not in str(error).lower():
                raise

    async def edit_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict[str, Any]
    ) -> None:
        """Replace a message's buttons, leaving its text alone.

        This is how an inline action settles: the choices the person was
        offered become the one state they chose, on the same message, so the
        chat does not accumulate a second message saying what a button did.
        """

        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """Remove a message. Never raises.

        Used for transient status only. Telegram refuses to delete messages
        older than 48 hours and says so; a status that outlived its turn is
        untidy, not a failure worth ending the turn for.
        """

        try:
            await self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except TelegramError:
            pass

    async def set_my_commands(self, commands: Iterable[BotCommand]) -> None:
        """Publish the native command menu."""

        await self._call(
            "setMyCommands",
            {
                "commands": [
                    {"command": entry.command, "description": entry.description}
                    for entry in commands
                ]
            },
        )

    async def set_my_description(self, description: str) -> None:
        """Publish what an empty chat with this bot says."""

        await self._call("setMyDescription", {"description": description})

    async def set_my_short_description(self, description: str) -> None:
        await self._call("setMyShortDescription", {"short_description": description})

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Show Telegram's own "typing…" for a few seconds.

        Never raises. This exists so a person can tell the difference between
        thinking and dead, and failing to say that must not fail the turn it was
        describing.
        """

        try:
            await self._call("sendChatAction", {"chat_id": chat_id, "action": action})
        except TelegramError:
            pass

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Take the spinner off a pressed button. Never raises.

        This is an acknowledgement, not the work. Telegram expires a callback
        query after a few minutes, and it answers a late press with `query is
        too old` — so a person who took their time deciding would otherwise
        have their approval thrown away by the failure of the animation that
        told them it had been received. Found live: a consent button pressed
        four and a half minutes after the question failed the whole turn.
        """

        try:
            await self._call(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
            )
        except TelegramError:
            return

    async def _upload(
        self, method: str, field: str, chat_id: int, name: str, data: bytes, limit: int
    ) -> None:
        if len(data) > limit:
            await self.send_message(
                chat_id, f"{name} is too large to send ({len(data)} bytes)."
            )
            return
        try:
            response = await self._client.post(
                f"/{method}",
                data={"chat_id": str(chat_id)},
                files={field: (name, data)},
            )
        except httpx.HTTPError as error:
            raise TelegramError(f"telegram could not be reached ({error})") from error
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"telegram refused {method}: {body.get('description')}")

    async def send_media_group(
        self, chat_id: int, kind: str, items: Sequence[tuple[str, bytes]]
    ) -> None:
        """Several photos, or several documents, as one album.

        `kind` is "photo" or "document"; Telegram does not mix them in one
        group. Between two and ten items; the caller splits and singles out.
        """

        media = [
            {"type": kind, "media": f"attach://file{index}"} for index in range(len(items))
        ]
        try:
            response = await self._client.post(
                "/sendMediaGroup",
                data={"chat_id": str(chat_id), "media": json.dumps(media)},
                files={f"file{index}": (name, data) for index, (name, data) in enumerate(items)},
            )
        except httpx.HTTPError as error:
            raise TelegramError(f"telegram could not be reached ({error})") from error
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"telegram refused sendMediaGroup: {body.get('description')}")

    async def send_document(self, chat_id: int, name: str, data: bytes) -> None:
        await self._upload(
            "sendDocument", "document", chat_id, name, data, MAX_DOCUMENT_BYTES
        )

    async def send_photo(self, chat_id: int, name: str, data: bytes) -> None:
        """Send an image so it appears in the chat rather than as a file.

        Telegram's photo limit is far lower than its document limit, and a
        screenshot that is merely large is still worth seeing, so anything over
        it falls back to the document path instead of being dropped.
        """

        if len(data) > MAX_PHOTO_BYTES:
            await self.send_document(chat_id, name, data)
            return
        await self._upload("sendPhoto", "photo", chat_id, name, data, MAX_PHOTO_BYTES)

    async def download(self, file_id: str) -> bytes:
        """Fetch an uploaded file's bytes through the two-step file API."""

        described = await self._call("getFile", {"file_id": file_id})
        path = (described or {}).get("file_path")
        if not path:
            raise TelegramError("telegram did not return a file path")
        try:
            response = await self._files.get(f"/{path}")
        except httpx.HTTPError as error:
            raise TelegramError(f"telegram file could not be fetched ({error})") from error
        if response.status_code >= 400:
            raise TelegramError(f"telegram file download failed: HTTP {response.status_code}")
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._files.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exception: object) -> None:
        await self.aclose()
