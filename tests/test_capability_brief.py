"""The assistant's account of itself, checked against what is wired up.

These tests exist because of two live failures, not a hypothesis. Asked for a
screenshot, the assistant said its "output supports only text" while the adapter
was sending screenshots; in the same run it reported a `browser.inspect` tool as
unavailable, a capability name it had turned into a tool that never existed.

So the properties under test are: the description is generated, it is complete,
it contains nothing invented, and the hand-written part of the prompt cannot
quietly outlive the tools it names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent.runtime import Agent
from app.attachments import ACCEPTED_MEDIA_TYPES
from app.capabilities import (
    CHAT_DELIVERY,
    TEXT_ONLY,
    Delivery,
    capability_brief,
    capability_report,
    needs_approval,
    system_message,
    tool_inventory,
)
from app.context.window import DEFAULT_SYSTEM_PROMPT
from app.memory import SqliteStore
from app.tools import (
    BROWSER_INSPECT,
    FILESYSTEM_READ,
    CapabilityRegistry,
    Toolbox,
    memory_tools,
)
from tests.fakes import ScriptedBackend, prompt_text, says, user


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


@pytest.fixture
def registry(workspace: Path) -> CapabilityRegistry:
    return CapabilityRegistry(workspace)


def everything(registry: CapabilityRegistry) -> Toolbox:
    return registry.toolbox(registry.grant())


# --- the inventory is generated, not typed -----------------------------------


def test_the_brief_names_every_tool_the_agent_holds(registry: CapabilityRegistry) -> None:
    tools = everything(registry)

    brief = capability_brief(tools)

    assert tools.names
    for name in tools.names:
        assert name in brief


def test_a_narrower_grant_produces_a_narrower_brief(registry: CapabilityRegistry) -> None:
    """The tool the model invented a name for is absent when it is not granted."""

    reading_only = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)))

    brief = capability_brief(reading_only)

    assert "read_file" in brief
    assert "use_page" not in brief
    assert "write_file" not in brief


def test_the_brief_grows_with_the_tools_it_is_given(registry: CapabilityRegistry) -> None:
    """A tool added outside the registry is described too, or the list lies."""

    store = SqliteStore(":memory:")
    try:
        with_memory = registry.toolbox(
            registry.grant(capabilities=(BROWSER_INSPECT,)),
            memory_tools(store, "someone", "thread", 5),
        )
    finally:
        store.close()

    brief = capability_brief(with_memory)

    assert "remember_fact" in brief
    assert "search_memory" in brief


def test_current_same_user_tools_do_not_advertise_an_approval_step(
    registry: CapabilityRegistry,
) -> None:
    tools = everything(registry)

    assert needs_approval(tools) == ()
    assert "approves them" not in capability_brief(tools)


def test_the_brief_covers_every_media_type_the_policy_admits(
    registry: CapabilityRegistry,
) -> None:
    """The admission policy is the only source for what can arrive."""

    brief = capability_brief(everything(registry))

    for media_type in ACCEPTED_MEDIA_TYPES:
        assert media_type in brief


# --- what leaves is the interface's fact, not the model's guess ---------------


def test_an_interface_that_shows_media_says_media_arrives(
    registry: CapabilityRegistry,
) -> None:
    brief = capability_brief(everything(registry), CHAT_DELIVERY)

    assert "image" in brief
    assert "send_file is the one way" in brief
    assert "one call per item" in brief
    assert "nothing is sent by itself" in brief


def test_the_brief_says_where_the_person_is_and_that_a_path_delivers_nothing(
    registry: CapabilityRegistry,
) -> None:
    """Live 2026-09-03: asked for a screenshot in the chat, the model answered
    with `![Screenshot](.agent/browser/….png)`. Nothing had told it the person
    is on Telegram and cannot see the workspace."""

    brief = capability_brief(everything(registry), Delivery(place="Telegram"))

    assert "talking to you through Telegram" in brief
    assert "cannot open, browse or see your workspace" in brief
    assert "markdown image of a workspace file reaches them as plain text" in brief
    assert "one call per item" in brief


def test_an_interface_that_cannot_show_media_says_that_instead(
    registry: CapabilityRegistry,
) -> None:
    """A future caller with no rendering must not be told pictures arrive."""

    brief = capability_brief(everything(registry), TEXT_ONLY)

    assert "no explicit file-delivery action" in brief
    assert "send_file is the one way" not in brief


def test_a_text_only_agent_does_not_receive_a_send_tool(
    tmp_path: Path, workspace: Path
) -> None:
    agent = Agent(
        ScriptedBackend(),
        SqliteStore(tmp_path / "text-only.sqlite3"),
        workspace,
        delivery=TEXT_ONLY,
    )
    try:
        assert "send_file" not in agent.toolbox("thread").names
    finally:
        agent.store.close()


def test_a_declared_kind_reaches_the_model(registry: CapabilityRegistry) -> None:
    brief = capability_brief(everything(registry), Delivery(media=("image",)))

    assert "can deliver image" in brief
    assert "audio" in brief  # still accepted as input
    assert "nothing is sent by itself" in brief


# --- what the wiring says, that no fixed prompt could ------------------------


def test_the_brief_says_there_is_a_workspace_and_never_where(
    registry: CapabilityRegistry, workspace: Path
) -> None:
    """An agent that does not know it has somewhere to put a file writes none.
    An agent told the exact path starts building paths — and in the deployed
    profile that path is the volume's internal one, which cost two refused
    calls on 2026-08-30. It needs the first fact and not the second."""

    brief = capability_brief(everything(registry))

    assert "one workspace directory and it is yours" in brief
    assert "without asking first" in brief
    assert "by its plain name" in brief
    assert str(workspace) not in brief


def test_the_brief_says_to_choose_a_name_rather_than_write_nothing(
    registry: CapabilityRegistry,
) -> None:
    """The measured failure: asked for a page and given no filename, the model
    wrote it into the chat and told the person to save it themselves."""

    brief = capability_brief(everything(registry))

    assert "choose a sensible name" in brief
    assert "instead of explaining what you could do" in brief


def test_a_reading_grant_is_not_told_to_create_files(
    registry: CapabilityRegistry,
) -> None:
    reading_only = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)))

    brief = capability_brief(reading_only)

    assert "one workspace directory" in brief
    assert "choose a sensible name" not in brief


def test_observation_guidance_appears_only_with_the_tool(
    registry: CapabilityRegistry,
) -> None:
    inspecting = registry.toolbox(registry.grant(capabilities=(BROWSER_INSPECT,)))
    reading_only = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)))

    guided = capability_brief(inspecting)

    assert "look at it before you describe it" in guided
    assert "never ask them to open it for you" in guided
    assert "use_page" not in capability_brief(reading_only)


def test_planning_guidance_appears_only_with_the_tool(
    registry: CapabilityRegistry,
) -> None:
    """And it says the two things the schema cannot: the price of a list, and
    what reads it once there is one.

    It has to sit between two live failures on 2026-08-31. Read as an
    invitation, it cost 88-100 s against about 50 s and changed nothing the
    model did. Rewritten to read as a discouragement, it produced a four-file
    application with eight stated requirements, no list, and nothing checked.
    So what is asserted here is the handle and the price, and that neither a
    prohibition nor an encouragement is left in the wording.
    """

    from app.tools import todo_tools

    planning = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)), todo_tools())

    guided = capability_brief(planning)

    # The conditions are Codex's, literal (ISS-0016: "when you can hold it in
    # your head" made GLM never open a list), and the price is stated.
    assert "phases or dependencies where the order matters" in guided
    assert "simple or single-step request" in guided
    assert "resends the whole list" in guided
    assert "read when you try to finish" in guided
    assert "todo_write" not in capability_brief(everything(registry))


def test_standing_instructions_are_never_memory(registry: CapabilityRegistry) -> None:
    """The boundary is stated on `remember_fact` itself, because that is the
    tool a model would otherwise reach for to keep a preference."""

    store = SqliteStore(":memory:")
    try:
        with_memory = registry.toolbox(
            registry.grant(capabilities=(FILESYSTEM_READ,)),
            memory_tools(store, "someone", "thread", 5),
        )
    finally:
        store.close()

    described = {s["function"]["name"]: s["function"]["description"] for s in with_memory.schemas()}

    assert "standing instructions" in described["remember_fact"]


def test_an_agent_without_tools_is_not_told_to_reach_for_them() -> None:
    brief = capability_brief(Toolbox())

    assert "You have no tools here" in brief
    assert "Treat the request as an outcome" not in brief


def test_the_system_message_is_the_core_then_the_wiring(
    registry: CapabilityRegistry,
) -> None:
    tools = everything(registry)

    whole = system_message(tools)

    assert whole.startswith(DEFAULT_SYSTEM_PROMPT)
    assert capability_brief(tools) in whole


# --- the hand-written prompt cannot outlive its tools -------------------------


def test_the_core_prompt_names_no_tool_at_all(registry: CapabilityRegistry) -> None:
    """The stable layer cannot know a tool exists.

    Stronger than the rule it replaces, which allowed the core to name a tool
    as long as that tool still existed. A grant can withhold any of them, so a
    fixed sentence about one is either advertising a tool this agent does not
    have or duplicating a sentence the brief already generates. Both are ways
    for the prompt to outlive the wiring.
    """

    assert not re.findall(r"\b[a-z]+_[a-z_]+\b", DEFAULT_SYSTEM_PROMPT)


def test_generated_guidance_names_only_tools_that_exist(
    registry: CapabilityRegistry,
) -> None:
    """Guidance may name tools; it may not name ones that are gone."""

    store = SqliteStore(":memory:")
    try:
        tools = registry.toolbox(
            registry.grant(), memory_tools(store, "someone", "thread", 5)
        )
    finally:
        store.close()

    named = set(re.findall(r"\b[a-z]+_[a-z_]+\b", capability_brief(tools)))

    assert named <= set(tools.names)


def test_every_wired_tool_carries_its_own_description(
    registry: CapabilityRegistry,
) -> None:
    """What each tool is for is owned by its schema, not by prose about it.

    This is where the coverage the core prompt used to provide now lives: the
    brief says what a capability is for and what may not be assumed, and the
    schema the model receives beside it says what each call does. A tool with
    no description would be reachable and unexplained.
    """

    store = SqliteStore(":memory:")
    try:
        tools = registry.toolbox(
            registry.grant(), memory_tools(store, "someone", "thread", 5)
        )
    finally:
        store.close()

    for schema in tools.schemas():
        described = schema["function"]["description"]
        assert described and described.strip(), schema["function"]["name"]


def test_the_inventory_closes_the_list_wherever_tools_are_given() -> None:
    """Every model call that receives tools receives this sentence."""

    sentence = tool_inventory(Toolbox())

    assert "none" in sentence
    assert "no others" in sentence


# --- the person can check the model's answer ---------------------------------


def test_the_report_states_what_the_agent_can_see_hear_send_and_change(
    registry: CapabilityRegistry, workspace: Path
) -> None:
    report = capability_report(everything(registry), CHAT_DELIVERY, workspace)

    assert "image/png" in report
    assert "audio/ogg" in report
    assert "write_file" in report
    assert str(workspace) in report


def test_the_agents_own_report_includes_its_memory_tools_and_root(
    tmp_path: Path, workspace: Path
) -> None:
    agent = Agent(ScriptedBackend(), SqliteStore(tmp_path / "m.sqlite3"), workspace)
    try:
        report = agent.capabilities("thread")
    finally:
        agent.store.close()

    assert "remember_fact" in report
    assert "use_page" in report
    assert str(workspace.resolve()) in report


# --- and the model is actually told ------------------------------------------


async def test_the_model_is_sent_the_derived_brief_every_turn(
    tmp_path: Path, workspace: Path
) -> None:
    backend = ScriptedBackend(says("hello"))
    agent = Agent(backend, SqliteStore(tmp_path / "m.sqlite3"), workspace)
    try:
        await agent.answer("thread", user("hi"))
    finally:
        await agent.aclose()

    sent = prompt_text(backend.requests[0])
    assert "Your tools are exactly" in sent
    assert "use_page" in sent
    assert "remember_fact" in sent


def test_the_person_is_told_which_documents_can_be_read(
    registry: CapabilityRegistry,
) -> None:
    """`/can` has to name this, because nothing else does.

    A document does not arrive as something the model can see, so an assistant
    that lists only images and audio reads as one that refuses PDFs — which is
    what it did until the tool existed.
    """

    from app.capabilities import capability_report

    tools = registry.toolbox(registry.grant())
    report = capability_report(tools)

    assert "Read: csv, docx, md, pdf, txt" in report
    assert "read_document" in report
    assert "view_pages" in report


def test_the_brief_never_says_a_document_cannot_be_seen(
    registry: CapabilityRegistry,
) -> None:
    """It said exactly that, and the assistant repeated it to a person.

    Asked to show the PDF it had just summarized, the assistant answered that it
    is a text model with no way to display anything — while holding a tool that
    renders the page and an interface that delivers pictures. The sentence that
    caused it was in the generated brief, which is the one place that is supposed
    to be incapable of being wrong about this.
    """

    from app.capabilities import capability_brief

    brief = capability_brief(registry.toolbox(registry.grant()))

    assert "never shown to you" not in brief
    assert "view_pages" in brief


def test_the_brief_separates_observation_from_explicit_presentation(
    registry: CapabilityRegistry,
) -> None:
    """Looking at a page is not a hidden request for the adapter to send it."""

    from app.capabilities import capability_brief

    tools = registry.toolbox(registry.grant())
    brief = capability_brief(tools)
    described = {s["function"]["name"]: s["function"]["description"] for s in tools.schemas()}

    assert "view_pages" in brief
    assert "nothing reaches the person unless you send_file it" in described["view_pages"]
    assert "send_file is the one way" in brief
