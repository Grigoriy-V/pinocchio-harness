"""The person's commands, answered in words once for every interface.

`/plan`, `/mode`, `/context` and `/compact` change or report the switches a
workspace keeps and what the next request is made of. Telegram and Chainlit
both offer them; the words are written here so the two interfaces cannot
drift into two answers to the same question. Each function returns the text
to show; sending it is the adapter's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent.mode import MODES, current_mode, set_mode
from app.agent.todo import PLAN_SWITCH, planning_enabled, set_planning
from app.context.choice import CONTEXT_CHOICE, SIZES, set_context_choice

if TYPE_CHECKING:
    from app.agent.runtime import Agent

PLAN_COMMANDS = {"/plan", "/planning"}
MODE_COMMANDS = {"/mode"}
CONTEXT_COMMANDS = {"/context", "/ctx"}


def exchanges(keep_turns: int) -> str:
    """The verbatim floor in a person's words: exchanges, not messages."""

    return "the last exchange" if keep_turns == 1 else f"the last {keep_turns} exchanges"


def plan_reply(agent: Agent, argument: str) -> str:
    """Show or flip whether the assistant keeps a task list.

    A marker file in the person's workspace, read when the next turn's
    toolbox is built, so `off` takes effect from the next message and is
    the same in every interface. Nothing else is touched: with the tool
    absent the brief has nothing to say about planning.
    """

    workspace = agent.workspace
    if argument in {"on", "off"}:
        set_planning(workspace, argument == "on")
        agent.rewire()
        return (
            "Planning is on from your next message: I may keep a task list for "
            f"longer work. Kept as {PLAN_SWITCH.as_posix()} in your workspace; "
            "/plan off turns it off again."
            if argument == "on"
            else "Planning is off from your next message: no task list, no "
            "planning tool. That is the default; /plan on turns it on."
        )
    state = "on" if planning_enabled(workspace) else "off (the default)"
    return (
        f"Planning is {state}. /plan on gives me a task list and the planning "
        "tool from the next message on; /plan off takes them away."
    )


def mode_reply(agent: Agent, argument: str) -> str:
    """Show or set whether changes to the workspace ask first.

    The same marker mechanism as `/plan`: read when the next toolbox is
    built, so it takes effect from the next message in every interface.
    """

    workspace = agent.workspace
    if argument in MODES:
        set_mode(workspace, argument)
        agent.rewire()
        return (
            "Careful mode from your next message: writing or changing a file and "
            "running a command wait for your yes, with the same buttons as before. "
            "/mode full turns it off."
            if argument == "careful"
            else "Full mode from your next message: everything inside your workspace "
            "runs without asking, and only effects beyond it ask. That is the "
            "default; /mode careful makes changes ask first."
        )
    mode = current_mode(workspace)
    return (
        f"Mode: {mode}{' (the default)' if mode == 'full' else ''}. In full mode "
        "everything inside your workspace runs without asking; in careful mode a "
        "change to a file or a command waits for your yes. /mode full or /mode "
        "careful sets it from the next message."
    )


def context_reply(agent: Agent, thread_id: str, argument: str) -> str:
    """Say what the next request is made of, or set how large it may be.

    Read from the store and estimated, never sent: the one number this
    cannot always give is the model's ceiling, which is read when the model
    next answers rather than by waking it for a report.
    """

    if argument in SIZES:
        set_context_choice(agent.workspace, argument)
        agent.rewire()
        report = agent.context_report(thread_id)
        size = f"up to {report.budget:,} tokens" + (
            f" of the model's {report.ceiling:,}" if report.ceiling else ""
        )
        return f"Context size is {argument} from your next message: {size}" + (
            f". Kept as {CONTEXT_CHOICE.as_posix()} in your workspace."
            if argument != "normal"
            else " (the default)."
        )
    if argument:
        return "Sizes are small, normal and large: /context small."
    report = agent.context_report(thread_id)
    layers = report.layers
    lines = [
        "What my next request in this chat is made of, estimated:",
        f"  core and capabilities  ~{layers['prelude']:,}",
        f"  tool schemas           ~{layers['schemas']:,}",
        f"  conversation           ~{layers['history']:,} "
        f"({report.messages} messages verbatim"
        + (f", {report.stubbed} older tool results shortened" if report.stubbed else "")
        + (f", {report.placeholders} pictures as placeholders" if report.placeholders else "")
        + ")",
    ]
    if report.summarized_through:
        lines.append(f"  summary covers the {report.summarized_through} messages before that")
    if layers["facts"]:
        lines.append(f"  facts for this turn    ~{layers['facts']:,}")
    if report.last_used is not None:
        cached = (
            f", {report.last_cached:,} of them from the cache"
            if report.last_cached is not None
            else ""
        )
        lines.append(f"Last request: {report.last_used:,} tokens{cached}.")
    lines.append(
        f"Size {report.size}: up to {report.budget:,} tokens"
        + (f" of the model's {report.ceiling:,}" if report.ceiling else "")
        + "; older conversation folds into the summary past that."
    )
    lines.append("/context small|normal|large sets the size; /compact folds the older part now.")
    return "\n".join(lines)


async def compact_reply(agent: Agent, thread_id: str) -> str:
    """Fold the older part of this conversation now. One summarizer call.

    Said in tokens, the way the references say it: what the next request
    no longer carries, from the same estimate the fold decides by.
    """

    before = agent.context_report(thread_id).layers["history"]
    folded = await agent.compact(thread_id)
    keep = exchanges(agent.policy.keep_turns)
    if not folded:
        return (
            f"Nothing to fold: {keep} always stay verbatim, and that is all there "
            "is past the summary."
        )
    after = agent.context_report(thread_id).layers["history"]
    freed = max(0, before - after)
    return (
        f"Compacted: about {freed:,} tokens freed, {folded} older messages folded "
        f"into the summary; {keep} stay verbatim, and the exact words stay "
        "reachable with search_history."
    )
