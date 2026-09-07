"""The page tool: one page, one call per action, evidence and effect for the model.

Most of this runs against a fake session, so it needs no browser: what is
under test is what the tool does with what the session gives it — the page
kept between calls, a ref checked, the report after an action, a screenshot
that is a file and a picture. The real-browser test runs where Chrome or Edge
is installed and is skipped elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.agent.runtime import Agent
from app.memory import SqliteStore
from app.models import ContentPart, ToolCall
from app.tools import (
    BROWSER_INSPECT,
    Capability,
    CapabilityRegistry,
    Pages,
    Tool,
    Toolbox,
    browser_tools,
    find_chromium_browser,
)
from app.tools.browser import container_flags, use_page
from app.tools.chromium import STALE_REF, BrowserError, Snapshot
from tests.fakes import ScriptedBackend, calls, says, user

# A 1x1 PNG, base64, so a fake screenshot is a real picture.
PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeSession:
    """Just enough of `BrowserSession`: a counter page with one button."""

    def __init__(self) -> None:
        self.count = 0
        self.errors: list[str] = []
        self.refused: list[str] = []
        self.done: list[str] = []
        self.browser_name = "fake"

    async def snapshot(self, max_chars: int, query: str | None = None) -> Snapshot:
        lines = [f'heading "{self.count} clicks" level=1', 'button "Count" [ref=e1]']
        if query:
            lines = [line for line in lines if query in line]
        return Snapshot(text="\n".join(lines), refs=("e1",), total_lines=2, shown_lines=len(lines))

    async def visible_text(self, max_chars: int) -> str:
        return f"{self.count} clicks\nCount"

    async def title(self) -> str:
        return "Counter"

    async def location(self) -> str:
        return "http://artifact.local/counter.html"

    async def evaluate(self, expression: str) -> Any:
        if expression == "0":
            return 0
        if expression == "boom":
            raise BrowserError("the expression failed in the page", detail="ReferenceError")
        return {"count": self.count}

    def console(self) -> list[str]:
        return list(self.errors)

    async def click(self, ref: str) -> None:
        if ref != "e1":
            raise BrowserError(f"{ref!r} is not a ref from the last snapshot", code=STALE_REF)
        self.count += 1
        self.done.append(f"click {ref}")

    async def type(self, ref: str, text: str, clear: bool = True) -> None:
        self.done.append(f"type {ref} {text}")

    async def press(self, key: str) -> None:
        self.done.append(f"press {key}")
        if key == "Enter":
            self.errors.append("TypeError: submit is not a function")

    async def select(self, ref: str, value: str) -> None:
        self.done.append(f"select {ref} {value}")

    async def screenshot(self, full_page: bool = False) -> str:
        return PNG


class FakePages(Pages):
    """`Pages` with the browser replaced: opening yields the fake session."""

    def __init__(self) -> None:
        super().__init__(idle_seconds=60)
        self.opened: list[str] = []
        self.closed = 0

    async def open(self, root: Path, *, file: Path | None = None, url: str | None = None):
        await self.close(root)
        self.opened.append(file.name if file is not None else str(url))
        from app.tools.browser import _Open

        class Context:
            async def __aexit__(self_, *_: Any) -> None:
                self.closed += 1

        held = _Open(session=FakeSession(), context=Context())
        self._open[root] = held
        self.touch(root)
        return held


def text_of(parts: list[ContentPart]) -> str:
    return "".join(part.text or "" for part in parts if part.kind == "text")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "counter.html").write_text("<button>Count</button>", encoding="utf-8")
    return tmp_path


async def test_open_reports_the_page_and_a_click_reports_its_effect(workspace: Path) -> None:
    pages = FakePages()

    opened = text_of(await use_page(workspace, pages, "open", path="counter.html"))
    clicked = text_of(await use_page(workspace, pages, "click", ref="e1"))
    again = text_of(await use_page(workspace, pages, "click", ref="e1"))

    assert opened.startswith("opened counter.html")
    assert 'button "Count" [ref=e1]' in opened and "0 clicks" in opened
    assert clicked.startswith("clicked e1") and "1 clicks" in clicked
    assert "2 clicks" in again
    assert pages.opened == ["counter.html"], "one page, kept between the calls"


async def test_an_action_before_open_and_a_stale_ref_are_refused(workspace: Path) -> None:
    pages = FakePages()
    box = Toolbox(browser_tools(workspace, pages=pages))

    early = await box.run_async(ToolCall("1", "use_page", {"action": "click", "ref": "e1"}))
    await box.run_async(ToolCall("2", "use_page", {"action": "open", "path": "counter.html"}))
    stale = await box.run_async(ToolCall("3", "use_page", {"action": "click", "ref": "e9"}))

    assert early.failure is not None and "action open first" in early.content[0].text
    assert stale.failure is not None and stale.failure.code == STALE_REF


async def test_console_errors_are_reported_once_since_the_last_call(workspace: Path) -> None:
    pages = FakePages()
    await use_page(workspace, pages, "open", path="counter.html")

    pressed = text_of(await use_page(workspace, pages, "press", key="Enter"))
    later = text_of(await use_page(workspace, pages, "snapshot"))

    assert "TypeError: submit is not a function" in pressed
    assert "TypeError" not in later, "an error is reported at the call that produced it"


async def test_evaluate_returns_json_and_a_failing_expression_is_a_typed_failure(
    workspace: Path,
) -> None:
    pages = FakePages()
    box = Toolbox(browser_tools(workspace, pages=pages))
    await box.run_async(ToolCall("1", "use_page", {"action": "open", "path": "counter.html"}))

    value = await box.run_async(
        ToolCall("2", "use_page", {"action": "evaluate", "expression": "state"})
    )
    failed = await box.run_async(
        ToolCall("3", "use_page", {"action": "evaluate", "expression": "boom"})
    )

    assert value.content[0].text == 'result: {"count": 0}'
    assert failed.failure is not None and "ReferenceError" in (failed.failure.detail or "")


async def test_a_screenshot_is_a_picture_for_the_model_and_a_file_in_the_workspace(
    workspace: Path,
) -> None:
    pages = FakePages()
    await use_page(workspace, pages, "open", path="counter.html")

    parts = await use_page(workspace, pages, "screenshot")

    assert [part.kind for part in parts] == ["text", "image"]
    assert "the person has not" in (parts[0].text or "")
    assert "send_file(path=" in (parts[0].text or "")
    shots = list((workspace / ".agent" / "browser").glob("counter-*.png"))
    assert len(shots) == 1 and shots[0].read_bytes() == parts[1].data


async def test_opening_another_page_closes_the_first(workspace: Path) -> None:
    pages = FakePages()
    (workspace / "other.html").write_text("<p>other</p>", encoding="utf-8")

    await use_page(workspace, pages, "open", path="counter.html")
    await use_page(workspace, pages, "open", path="other.html")

    assert pages.opened == ["counter.html", "other.html"]
    assert pages.closed == 1
    await pages.close_all()
    assert pages.closed == 2


async def test_open_takes_exactly_one_of_path_and_url(workspace: Path) -> None:
    box = Toolbox(browser_tools(workspace, pages=FakePages()))

    neither = await box.run_async(ToolCall("1", "use_page", {"action": "open"}))
    both = await box.run_async(
        ToolCall("2", "use_page", {"action": "open", "path": "a.html", "url": "https://x"})
    )

    assert neither.failure is not None and both.failure is not None


async def test_a_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")

    result = await Toolbox(browser_tools(root, pages=FakePages())).run_async(
        ToolCall("browser", "use_page", {"action": "open", "path": str(outside)})
    )

    assert result.failure is not None
    assert "outside the allowed root" in (result.content[0].text or "")


async def test_the_page_stays_open_across_a_turns_calls_through_the_registry(
    tmp_path: Path,
) -> None:
    """The registry holds the page, so two toolboxes of one agent share it."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "page.html").write_text("<button>Count</button>", encoding="utf-8")
    registry = CapabilityRegistry(workspace)
    registry.pages = FakePages()
    first = registry.toolbox(registry.grant(capabilities=(BROWSER_INSPECT,)))
    second = registry.toolbox(registry.grant(capabilities=(BROWSER_INSPECT,)))

    await first.run_async(ToolCall("1", "use_page", {"action": "open", "path": "page.html"}))
    clicked = await second.run_async(ToolCall("2", "use_page", {"action": "click", "ref": "e1"}))

    assert clicked.failure is None and "1 clicks" in (clicked.content[0].text or "")


async def test_the_screenshot_reaches_the_next_model_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def page(action: str, **_: Any):
        assert action == "screenshot"
        return [
            ContentPart(kind="text", text="screenshot: shot.png"),
            ContentPart(kind="image", data=b"screenshot", media_type="image/png"),
        ]

    registry = CapabilityRegistry(
        workspace,
        (Capability(BROWSER_INSPECT, lambda _root: [Tool(
            name="use_page",
            description="page",
            parameters={
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
                "additionalProperties": False,
            },
            run=page,
        )]),),
    )
    backend = ScriptedBackend(calls("use_page", action="screenshot"), says("Looks good."))
    agent = Agent(
        backend,
        SqliteStore(tmp_path / "memory.sqlite3"),
        workspace,
        capability_registry=registry,
        capability_grant=registry.grant(capabilities=(BROWSER_INSPECT,)),
    )

    await agent.answer("thread", user("Take a screenshot."))

    tool_result = backend.requests[1][-1]
    assert tool_result.role == "tool"
    assert [part.kind for part in tool_result.content] == ["text", "image"]
    assert tool_result.content[1].data == b"screenshot"
    await agent.aclose()


@pytest.mark.skipif(find_chromium_browser() is None, reason="Chrome/Edge is unavailable")
async def test_a_real_page_is_opened_clicked_and_read(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text(
        "<title>Capability check</title><main><h1 id=n>0</h1>"
        "<button onclick=\"document.getElementById('n').textContent++\">Continue</button></main>",
        encoding="utf-8",
    )
    pages = Pages()
    try:
        opened = text_of(await use_page(tmp_path, pages, "open", path="page.html"))
        clicked = text_of(await use_page(tmp_path, pages, "click", ref="e1"))
        shot = await use_page(tmp_path, pages, "screenshot")
    finally:
        await pages.close_all()

    assert "title: Capability check" in opened
    assert 'button "Continue" [ref=e1]' in opened
    assert "console errors since the last call:\nnone" in opened
    assert 'heading "1" level=1' in clicked
    assert shot[1].data is not None and len(shot[1].data) > 1_000
    assert list((tmp_path / ".agent" / "browser").glob("page-*.png"))


def test_a_desktop_browser_keeps_its_own_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-sandbox` is what a container needs, not what a laptop should get.

    It is the only real isolation the browser has. Handing it away everywhere so
    that one environment works would make the deployed concession the default.
    """

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)

    assert container_flags() == []


def test_a_container_browser_gets_the_flags_it_cannot_start_without(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chromium as root refuses its sandbox, and 64 MB of /dev/shm crashes it.

    Both are facts about the machine, so they are read from the machine rather
    than from a setting that has to be remembered when a profile changes.
    """

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)

    assert container_flags() == ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


def test_windows_is_never_treated_as_a_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")

    assert container_flags() == []
