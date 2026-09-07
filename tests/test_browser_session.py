"""One page, the whole operation set, and the boundary that goes with it.

The snapshot is what every later action is built on, so most of this is about
what the snapshot says and what a ref bought from it can do. The real-browser
tests run wherever Chrome or Edge is installed and are skipped elsewhere; the
rest need no browser at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import ToolCall
from app.tools import Toolbox, browser_tools
from app.tools.chromium import (
    ARTIFACT_ORIGIN,
    REFUSED,
    STALE_REF,
    UNAVAILABLE,
    BrowserError,
    BrowserSession,
    CdpSession,
    find_chromium_browser,
    format_snapshot,
    open_browser,
    serve_directory,
)
from tests.test_chromium_requests import FakeSocket

needs_browser = pytest.mark.skipif(
    find_chromium_browser() is None, reason="Chrome/Edge is unavailable"
)

PAGE = """
<title>Counter</title>
<main>
  <h1>Tasks</h1>
  <p id="count">0 clicks</p>
  <button id="more" onclick="bump()">More</button>
  <form onsubmit="event.preventDefault(); add()">
    <input id="task" placeholder="Add a task">
  </form>
  <select id="kind" onchange="document.getElementById('chosen').textContent = this.value">
    <option value="home">Home</option><option value="work">Work</option>
  </select>
  <span id="chosen">home</span>
  <ul id="list"></ul>
  <a href="/gone" id="gone">Remove me</a>
  <div hidden>invisible</div>
</main>
<script>
  let n = 0;
  function bump() { n += 1; document.getElementById('count').textContent = n + ' clicks'; }
  function add() {
    const input = document.getElementById('task');
    const item = document.createElement('li'); item.textContent = input.value;
    document.getElementById('list').appendChild(item); input.value = '';
    document.getElementById('gone').remove();
  }
</script>
"""


# --- without a browser ---------------------------------------------------------


def test_the_snapshot_is_indented_and_bounded() -> None:
    lines = [(0, "main"), (1, 'heading "Tasks" level=1'), (1, 'button "More" [ref=e1]'), (1, "text: tail")]

    text, shown = format_snapshot(lines, max_chars=60)

    assert text.splitlines()[:2] == ["- main", '  - heading "Tasks" level=1']
    assert shown == 2
    assert "2 more line(s) not shown" in text


def test_a_query_keeps_only_the_lines_that_mention_it() -> None:
    lines = [(0, "main"), (1, 'button "Save" [ref=e1]'), (1, 'button "Cancel" [ref=e2]')]

    text, shown = format_snapshot(lines, query="cancel")

    assert text == '  - button "Cancel" [ref=e2]'
    assert shown == 1
    assert "nothing on the page mentions 'nope'" in format_snapshot(lines, query="nope")[0]


def test_a_long_line_is_cut_rather_than_dropped() -> None:
    text, _ = format_snapshot([(0, "text: " + "x" * 500)])

    assert len(text) < 130 and text.endswith("…")


async def test_an_offline_session_reaches_no_address() -> None:
    socket = FakeSocket()
    session = BrowserSession(CdpSession(socket), "fake", offline=True)

    with pytest.raises(BrowserError) as refused:
        await session.navigate("https://example.com/")

    assert refused.value.code == REFUSED
    assert socket.sent == []


async def test_a_ref_not_from_the_last_snapshot_is_stale_before_anything_is_sent() -> None:
    socket = FakeSocket()
    session = BrowserSession(CdpSession(socket), "fake", offline=True)

    with pytest.raises(BrowserError) as stale:
        await session.click("e9")

    assert stale.value.code == STALE_REF
    assert socket.sent == []


async def test_a_session_has_exactly_one_boundary() -> None:
    async def anything(_url: str) -> bool:
        return True

    with pytest.raises(ValueError):
        async with open_browser():
            pass
    with pytest.raises(ValueError):
        async with open_browser(serve=serve_directory(Path(".")), allow=anything):
            pass


def test_the_directory_server_answers_only_files_inside_its_root(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "styles.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    serve = serve_directory(tmp_path / "app")

    assert serve(f"{ARTIFACT_ORIGIN}/styles.css?v=1") == ("text/css; charset=utf-8", b"body{}")
    assert serve(f"{ARTIFACT_ORIGIN}/../secret.txt") is None
    assert serve(f"{ARTIFACT_ORIGIN}/missing.css") is None
    assert serve("https://example.com/styles.css") is None


async def test_a_served_file_is_fulfilled_and_anything_else_fails(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("1", encoding="utf-8")
    socket = FakeSocket(
        {"method": "Fetch.requestPaused", "params": {"requestId": "1", "request": {"url": f"{ARTIFACT_ORIGIN}/a.js"}}},
        {"method": "Fetch.requestPaused", "params": {"requestId": "2", "request": {"url": "https://example.com/x"}}},
        {"id": 1, "result": {}},
    )
    session = CdpSession(socket, serve=serve_directory(tmp_path))

    await session.call("Runtime.enable")

    sent = [(m["method"], m["params"].get("requestId")) for m in socket.sent if m["method"].startswith("Fetch.")]
    assert sent == [("Fetch.fulfillRequest", "1"), ("Fetch.failRequest", "2")]
    assert session.refused == ["https://example.com/x"]


async def test_with_a_policy_a_public_request_leaves_and_a_private_one_does_not(tmp_path: Path) -> None:
    """The human allowed it 2026-09-03: a local page may load its CDN styles."""

    (tmp_path / "a.js").write_text("1", encoding="utf-8")

    async def public_only(url: str) -> bool:
        return url.startswith("https://cdn.")

    socket = FakeSocket(
        {"method": "Fetch.requestPaused", "params": {"requestId": "1", "request": {"url": f"{ARTIFACT_ORIGIN}/a.js"}}},
        {"method": "Fetch.requestPaused", "params": {"requestId": "2", "request": {"url": "https://cdn.tailwindcss.com/"}}},
        {"method": "Fetch.requestPaused", "params": {"requestId": "3", "request": {"url": "http://169.254.169.254/"}}},
        {"id": 1, "result": {}},
    )
    session = CdpSession(socket, allow=public_only, serve=serve_directory(tmp_path))

    await session.call("Runtime.enable")

    sent = [(m["method"], m["params"].get("requestId")) for m in socket.sent if m["method"].startswith("Fetch.")]
    assert sent == [("Fetch.fulfillRequest", "1"), ("Fetch.continueRequest", "2"), ("Fetch.failRequest", "3")]
    assert session.refused == ["http://169.254.169.254/"]


async def test_no_browser_is_a_typed_failure_the_model_reads(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    box = Toolbox(browser_tools(tmp_path, browser=tmp_path / "no-such-browser"))

    result = await box.run_async(
        ToolCall("b", "use_page", {"action": "open", "path": "page.html"})
    )

    assert result.failure is not None and result.failure.code == UNAVAILABLE


# --- with one -------------------------------------------------------------------


@needs_browser
async def test_the_snapshot_names_controls_and_gives_them_refs() -> None:
    async with open_browser(offline=True) as session:
        await session.open(document=PAGE)
        snapshot = await session.snapshot()

    text = snapshot.text
    assert 'heading "Tasks" level=1' in text
    assert 'button "More" [ref=e1]' in text
    assert 'textbox "Add a task" [ref=e2]' in text
    assert 'combobox "kind" [ref=e3]' in text and '"Home"' in text
    assert 'link "Remove me" [ref=e4] href="/gone"' in text
    assert "invisible" not in text
    assert snapshot.refs == ("e1", "e2", "e3", "e4")


@needs_browser
async def test_a_click_runs_the_pages_own_handler() -> None:
    async with open_browser(offline=True) as session:
        await session.open(document=PAGE)
        await session.snapshot()
        await session.click("e1")
        await session.click("e1")
        after = await session.snapshot(query="clicks")

    assert "2 clicks" in after.text


@needs_browser
async def test_typing_then_enter_submits_and_a_removed_element_goes_stale() -> None:
    async with open_browser(offline=True) as session:
        await session.open(document=PAGE)
        await session.snapshot()
        await session.type("e2", "buy milk")
        await session.press("Enter")
        after = await session.snapshot()
        with pytest.raises(BrowserError) as gone:
            await session.click("e4")

    assert 'listitem' in after.text and "buy milk" in after.text
    assert "Remove me" not in after.text
    assert gone.value.code == STALE_REF


@needs_browser
async def test_select_fires_change_and_type_replaces_what_was_there() -> None:
    async with open_browser(offline=True) as session:
        await session.open(document=PAGE)
        await session.snapshot()
        await session.select("e3", "Work")
        await session.type("e2", "first")
        await session.type("e2", "second")
        after = await session.snapshot()
        with pytest.raises(BrowserError):
            await session.select("e3", "Nowhere")

    assert 'text: work' in after.text.lower()
    assert 'value="second"' in after.text and '"first"' not in after.text


@needs_browser
async def test_a_served_page_has_storage_and_its_sibling_files(tmp_path: Path) -> None:
    """Live 2026-09-03: under a data: URL localStorage threw and styles.css never
    loaded, so the model was told an error the app does not have."""

    app = tmp_path / "Task Board"
    app.mkdir()
    (app / "styles.css").write_text("#late { display: none }", encoding="utf-8")
    (app / "app.js").write_text(
        "localStorage.setItem('k', 'kept'); document.getElementById('out').textContent = localStorage.getItem('k');",
        encoding="utf-8",
    )
    (app / "index.html").write_text(
        '<link rel="stylesheet" href="styles.css"><p id="out">unset</p><p id="late">styled away</p>'
        '<script src="app.js"></script><script>fetch("https://example.com/").catch(() => {})</script>',
        encoding="utf-8",
    )
    async with open_browser(offline=True, serve=serve_directory(tmp_path)) as session:
        await session.open(file=app / "index.html", root=tmp_path)
        await session.evaluate("0")
        snapshot = await session.snapshot()
        errors = session.console()
        refused = session.refused

    assert 'paragraph "kept"' in snapshot.text
    assert "styled away" not in snapshot.text
    assert errors == []
    assert "https://example.com/" in refused


@needs_browser
async def test_a_scripts_error_reaches_the_console_and_a_fetch_goes_nowhere() -> None:
    async with open_browser(offline=True) as session:
        await session.open(
            document="<p>x</p><script>fetch('https://example.com/').catch(() => {});"
            " undefinedFunction();</script>"
        )
        await session.evaluate("0")
        errors = session.console()

    assert any("undefinedFunction" in line for line in errors)
