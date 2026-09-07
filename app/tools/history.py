"""The two tools that reach what a summary or a stub stands for.

Stored history is canonical and never rewritten (`DECISIONS.md`
2026-08-30); a summary is safe to be wrong only because the exact words are
still there. Until 2026-09-03 nothing in the product could reach them: the
model's one way back to an old result was to run the tool again. These two
tools are the way back. `search_history` finds the message; `read_history`
returns it as it was said, in pages, and is what a shortened result's stub
points at by position.

Hits are returned as text, not condensed by another model call: the point is
not another summary. Scope is the same as facts — this person's own
conversations, never anyone else's — and the current conversation unless
asked otherwise.
"""

from __future__ import annotations

import re

from app.memory import ConversationStore
from app.memory.records import message_text
from app.models import Message
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError
from app.tools.paging import page

# What one hit shows around its first match: enough to tell whether it is
# the one, not the whole message.
SNIPPET_CHARS = 300
# What one `read_history` call returns before it asks to be called again.
PAGE_CHARS = 12_000
NOT_FOUND = "history.not_found"

TOKEN = re.compile(r"\w+", re.UNICODE)


def snippet(text: str, query: str, width: int = SNIPPET_CHARS) -> str:
    """`width` characters of `text` around the first word of `query` found."""

    flat = " ".join(text.split())
    lowered = flat.casefold()
    at = -1
    for token in TOKEN.findall(query):
        at = lowered.find(token.casefold())
        if at >= 0:
            break
    if at < 0:
        at = 0
    start = max(0, at - width // 3)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    piece = flat[start:end]
    if start > 0:
        piece = "…" + piece
    if end < len(flat):
        piece = piece + "…"
    return piece


def describe(message: Message) -> str:
    """A stored message as words: its text, its failure, its calls."""

    return message_text(message)


def with_results(
    store: ConversationStore, thread_id: str, position: int, messages: list[Message]
) -> list[Message]:
    """The messages read, plus the results of the calls the last one made.

    A call and what came back are one thing to a reader: live on 2026-09-03
    (run `live-80`) the model read the call it had found, saw no error in
    it, and said none had happened — the failure was the next message.
    """

    last = messages[-1]
    if not last.tool_calls:
        return messages
    wanted = {call.id for call in last.tool_calls}
    after = position + len(messages) - 1
    for message in store.messages(thread_id, after=after, limit=len(wanted)):
        if message.role != "tool" or message.tool_call_id not in wanted:
            break
        messages.append(message)
    return messages


def _search(
    store: ConversationStore,
    user_id: str,
    thread_id: str,
    query: str,
    all_conversations: bool,
    limit: int,
) -> str:
    hits = store.search_messages(
        query, user_id, thread_id=None if all_conversations else thread_id, limit=limit
    )
    if not hits:
        where = "any of your conversations" if all_conversations else "this conversation"
        return f"no message in {where} matches {query!r}"
    lines = []
    for hit in hits:
        place = f" in conversation {hit.thread_id}" if all_conversations else ""
        lines.append(
            f"#{hit.position} {hit.role} {hit.created_at[:16]}{place}\n"
            f"{snippet(hit.text, query)}{outcome(store, hit)}"
        )
    return "\n\n".join(lines)


def outcome(store: ConversationStore, hit) -> str:
    """For a hit that is a call, what the tool said back, on the next line."""

    if hit.role != "assistant":
        return ""
    following = store.messages(hit.thread_id, after=hit.position, limit=1)
    if not following or following[0].role != "tool":
        return ""
    result = following[0]
    said = message_text(result)
    if not said:
        return ""
    word = "failed" if result.failure is not None else "returned"
    return f"\n  → #{hit.position + 1} {word}: {snippet(said, '', 160)}"


def _read(
    store: ConversationStore,
    user_id: str,
    thread_id: str,
    position: int,
    count: int,
    offset: int,
    conversation: str | None,
) -> str:
    if position < 0:
        raise ToolError("position must be zero or more", code=BAD_ARGUMENTS)
    if count < 1:
        raise ToolError("count must be at least one", code=BAD_ARGUMENTS)
    target = conversation or thread_id
    if store.thread_owner(target) != user_id:
        # Another person's conversation and a conversation that does not exist
        # get the same answer: neither is this person's to read.
        raise ToolError(f"no conversation {target!r} of yours", code=NOT_FOUND)
    messages = store.messages(target, after=position - 1, limit=count)
    if not messages:
        raise ToolError(
            f"nothing at position {position}: the conversation has "
            f"{store.message_count(target)} messages",
            code=NOT_FOUND,
        )
    messages = with_results(store, target, position, messages)
    text = "\n\n".join(
        f"#{position + index} {message.role}\n{describe(message)}"
        for index, message in enumerate(messages)
    )
    return page(
        text,
        offset,
        PAGE_CHARS,
        f"read_history again with position={position}, count={count}, offset={{offset}}",
    )


def history_tools(
    store: ConversationStore,
    user_id: str,
    thread_id: str,
    limit: int = 8,
) -> list[Tool]:
    """Build `search_history` and `read_history` over one store, for one
    person, in one conversation."""

    return [
        Tool(
            name="search_history",
            replay_safe=True,
            description=(
                "Find what was actually said earlier in this conversation, including "
                "before the summary: the exact wording, a filename, a number, an error "
                "message, a tool's result. Set `all_conversations` to search this "
                "person's other conversations too."
            ),
            returns=(
                "the best-matching messages, each with its position and a snippet; "
                "read_history returns one whole."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words to look for."},
                    "all_conversations": {
                        "type": "boolean",
                        "description": "Search every conversation of this person, not only this one.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            run=lambda query, all_conversations=False: _search(
                store, user_id, thread_id, query, bool(all_conversations), limit
            ),
        ),
        Tool(
            name="read_history",
            replay_safe=True,
            description=(
                "Read stored messages of this conversation as they were said, by position "
                "(from search_history, or from a shortened result's note). Pass "
                "`conversation` to read one of this person's other conversations."
            ),
            returns=(
                "the messages whole, in pages: the end of a page says which offset to "
                "ask for next."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "position": {"type": "integer", "minimum": 0, "description": "The message's position."},
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "How many messages from that position. Defaults to 1.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Character offset to continue a long read from. Defaults to 0.",
                    },
                    "conversation": {
                        "type": "string",
                        "description": "A conversation id from search_history; defaults to this one.",
                    },
                },
                "required": ["position"],
                "additionalProperties": False,
            },
            run=lambda position, count=1, offset=0, conversation=None: _read(
                store, user_id, thread_id, int(position), int(count), int(offset), conversation
            ),
        ),
    ]
