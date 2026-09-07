"""The self-test, tested — including that it reports failure rather than raising.

A diagnostic is only worth having if it is honest when the thing it checks is
broken, so most of what is asserted here is behaviour under failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime import Agent
from app.memory import SqliteStore
from app.preflight import Check, Probe, attempt, report, run, tool_probes
from app.tools import BROWSER_INSPECT, FILESYSTEM_READ, CapabilityRegistry
from tests.fakes import ScriptedBackend, says


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


def open_agent(tmp_path: Path, workspace: Path, backend: ScriptedBackend) -> Agent:
    return Agent(backend, SqliteStore(tmp_path / "m.sqlite3"), workspace)


# --- the honesty of the report ------------------------------------------------


async def test_a_broken_probe_is_a_failed_check_not_an_exception() -> None:
    """One broken capability must not hide the state of the others."""

    async def explode() -> str:
        raise RuntimeError("no browser here")

    async def fine() -> str:
        return "worked"

    checks = await run(
        [Probe("broken", "free", explode), Probe("working", "free", fine)]
    )

    assert [check.ok for check in checks] == [False, True]
    assert "no browser here" in checks[0].detail
    assert "RuntimeError" in checks[0].detail


async def test_a_probe_that_returns_nothing_still_counts_as_run() -> None:
    async def quiet() -> str:
        return ""

    assert (await attempt(Probe("quiet", "free", quiet))).ok is True


def test_the_report_says_how_many_passed() -> None:
    passed = [Check("a", True, "fine"), Check("b", True, "fine")]
    mixed = [Check("a", True, "fine"), Check("b", False, "broken")]

    assert "all 2 passed" in report(passed)
    assert "1/2 passed" in report(mixed)
    assert report([]) == "nothing was checked"


# --- cost is a gate, not a label ----------------------------------------------


async def test_the_model_probe_does_not_run_unless_it_is_asked_for() -> None:
    """Nothing that wakes a GPU may run because a diagnostic was convenient."""

    async def never() -> str:
        raise AssertionError("a gpu probe ran without being included")

    assert await run([Probe("model", "gpu", never)]) == []
    assert await run([Probe("model", "gpu", never)], ("free",)) == []


async def test_selftest_leaves_the_model_alone(tmp_path: Path, workspace: Path) -> None:
    backend = ScriptedBackend()  # any call raises: nothing is scripted
    agent = open_agent(tmp_path, workspace, backend)
    try:
        result = await agent.selftest("thread")
    finally:
        await agent.aclose()

    assert backend.requests == []
    assert not any(line.endswith(" model") for line in result.splitlines())
    assert "PASS model:" not in result and "FAIL model:" not in result


# --- it exercises the real thing ----------------------------------------------


async def test_it_runs_the_store_in_the_order_a_turn_does(
    tmp_path: Path, workspace: Path
) -> None:
    """Listing threads before reading context is the sequence that broke live."""

    agent = open_agent(tmp_path, workspace, ScriptedBackend())
    try:
        result = await agent.selftest("thread")
    finally:
        await agent.aclose()

    assert "PASS store.turn" in result
    assert "PASS store.memory" in result
    assert "PASS filesystem" in result


async def test_it_leaves_no_conversation_behind(
    tmp_path: Path, workspace: Path
) -> None:
    agent = open_agent(tmp_path, workspace, ScriptedBackend())
    try:
        await agent.selftest("thread")
        remaining = agent.store.threads(agent.user_id)
    finally:
        await agent.aclose()

    assert remaining == []


async def test_it_leaves_no_files_behind(tmp_path: Path, workspace: Path) -> None:
    agent = open_agent(tmp_path, workspace, ScriptedBackend())
    try:
        await agent.selftest("thread")
    finally:
        await agent.aclose()

    # The browser tool creates `.agent/browser/` for its screenshots; what must
    # not survive is anything the probes put in it.
    leftovers = [path for path in workspace.rglob("*") if path.is_file()]

    assert leftovers == []


async def test_a_capability_that_is_not_granted_is_not_probed(workspace: Path) -> None:
    """The probes follow the toolbox, so they cannot test what was not given."""

    registry = CapabilityRegistry(workspace)
    reading_only = registry.toolbox(registry.grant(capabilities=(FILESYSTEM_READ,)))

    assert [probe.name for probe in tool_probes(reading_only, workspace)] == []

    with_browser = registry.toolbox(registry.grant(capabilities=(BROWSER_INSPECT,)))

    assert [probe.name for probe in tool_probes(with_browser, workspace)] == [
        "browser.page"
    ]


async def test_the_probe_list_matches_what_the_agent_claims(
    tmp_path: Path, workspace: Path
) -> None:
    """Every tool the report advertises has something that tries it.

    Not every tool one-to-one — `remember_fact` is covered by `store.memory`
    and the three file tools by one probe — but no advertised area may be
    unprobed, which is what would let a claim go untested.
    """

    agent = open_agent(tmp_path, workspace, ScriptedBackend(says("x")))
    try:
        claimed = agent.capabilities("thread")
        probed = {probe.name for probe in agent.probes("thread")}
    finally:
        await agent.aclose()

    assert "use_page" in claimed and "browser.page" in probed
    assert "write_file" in claimed and "filesystem" in probed
    assert "remember_fact" in claimed and "store.memory" in probed
    assert "fetch_page" in claimed and "web.fetch" in probed
    assert "view_web_page" in claimed and "web.view" in probed


async def test_an_async_tool_is_probed_through_the_path_it_actually_takes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by running `/check`, not by reading the code.

    `Toolbox.run` refuses an async tool instead of running it, so the two web
    probes reported the capability broken while the tools worked. A probe that
    lies about a working capability is the same failure as one that hides a
    broken one.
    """

    from app.config import WebSettings
    from app.tools import Toolbox, web_fetch_tools
    from app.web import Fetched

    async def fetched(url, settings=None, client=None, resolve=None):
        return Fetched(url, 200, "text/html", "Example", "Example Domain", False)

    monkeypatch.setattr("app.tools.web.fetch_page", fetched)
    tools = Toolbox(web_fetch_tools(workspace, WebSettings(_env_file=None)))

    checks = await run(tool_probes(tools, workspace))

    assert [(check.name, check.ok) for check in checks] == [("web.fetch", True)]
