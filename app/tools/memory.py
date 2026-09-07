"""The two tools that make memory the model's decision.

Saving is an explicit act, never a side effect of the model saying something.
That is why `remember_fact` is a tool the model has to call rather than a
listener that harvests statements out of the conversation.
"""

from __future__ import annotations

from app.memory import ConversationStore
from app.tools.base import Tool, ToolError

MAX_FACT_CHARS = 500

# The one way saving fails: the fact is not one this keeps.
INVALID = "memory.invalid"


def _remember(
    store: ConversationStore, user_id: str, thread_id: str | None, text: str
) -> str:
    text = text.strip()
    if not text:
        raise ToolError("a fact cannot be empty", code=INVALID)
    if len(text) > MAX_FACT_CHARS:
        raise ToolError(f"a fact must be shorter than {MAX_FACT_CHARS} characters", code=INVALID)
    store.remember(text, user_id, thread_id=thread_id)
    return f"saved: {text}"


def _search(store: ConversationStore, user_id: str, query: str, limit: int) -> str:
    found = store.search(query, user_id, limit=limit)
    if not found:
        return f"no memory matches {query!r}"
    return "\n".join(f"- {fact}" for fact in found)


def memory_tools(
    store: ConversationStore,
    user_id: str,
    thread_id: str | None = None,
    limit: int = 5,
) -> list[Tool]:
    """Build `remember_fact` and `search_memory` over one store, for one user.

    `thread_id` is provenance only. A fact saved in one conversation is
    searchable from every other one of that user's conversations, which is the
    whole point of saving it; `user_id` is where that stops.
    """

    return [
        Tool(
            name="remember_fact",
            description=(
                "Save one durable fact the person stated about themselves or their "
                "project, for later conversations. Only when they state it; never how "
                "they want you to work, which is their standing instructions file."
            ),
            returns="that the fact was saved.",
            leaves="the fact in this person's memory, retrieved in every later conversation.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact, stated in one self-contained sentence.",
                    }
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            run=lambda text: _remember(store, user_id, thread_id, text),
        ),
        Tool(
            name="search_memory",
            replay_safe=True,
            description="Search the facts saved in earlier conversations, by keyword.",
            returns="the matching facts, one per line.",
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words to look for."}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            run=lambda query: _search(store, user_id, query, limit),
        ),
    ]
