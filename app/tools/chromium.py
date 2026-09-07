"""Starting a headless browser and talking to it, without deciding what for.

Two capabilities need a real browser: inspecting a local HTML artifact and
looking at a public web page. They differ in what they allow the page to do —
one blocks the network entirely, the other exists to reach it — and in nothing
else. Keeping the process, the DevTools handshake and the CDP session here means
a fix to the fragile part is a fix in both, and neither module has to remember
which flags a container cannot start without.

`open_page` starts the process and hands back the raw CDP session; nothing at
that level navigates anywhere. `BrowserSession` above it is the one page API
every browser capability drives — observation and action alike — and the trust
boundary is a property of the session: offline for a local document, a request
policy for a public page.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

MAX_VISIBLE_TEXT = 8_000


def find_chromium_browser() -> Path | None:
    """Find an installed Chromium browser without downloading anything."""

    for name in ("msedge", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path("/usr/bin/microsoft-edge"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def container_flags() -> list[str]:
    """The launch flags a browser needs to start at all inside a container.

    Chromium refuses to run its own sandbox as root, and a container's default
    `/dev/shm` is 64 MB, which is where a renderer crashes rather than fails
    honestly. Both are container facts, so they are decided by looking at the
    machine rather than by a setting someone has to remember to turn on.

    They are dropped on a normal desktop deliberately. `--no-sandbox` is the
    concession that buys the container a browser at all; giving it up where it
    is not needed would be trading away the only isolation the browser has.
    """

    if os.name == "nt" or getattr(os, "geteuid", None) is None or os.geteuid() != 0:
        return []
    return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_json(url: str, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("browser returned a non-object DevTools response")
    return payload


class CdpSession:
    """One page, driven over the DevTools protocol.

    Events that arrive while a call is outstanding are not discarded: console
    errors are what make a broken page look broken instead of empty, and a
    paused request is a decision this session has to make before the page can
    continue.
    """

    def __init__(
        self,
        websocket: Any,
        allow: Callable[[str], Awaitable[bool]] | None = None,
        serve: "Server | None" = None,
    ) -> None:
        self.websocket = websocket
        self.next_id = 1
        self.console_errors: list[str] = []
        self.refused: list[str] = []
        self._allow = allow
        self._serve = serve

    def _take_id(self) -> int:
        """Every message takes its id here.

        One counter, one rule. Interception previously incremented first and
        sent second, which handed a later `call` the same id an intercepted
        request was still owed a reply for — so a page's evidence arrived as the
        acknowledgement of a `Fetch.continueRequest`, and every rendered page
        came back with no text and no title. Found by rendering a real page,
        which is the only place a screenshot and an empty transcript disagree.
        """

        taken = self.next_id
        self.next_id += 1
        return taken

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a command without waiting for its answer.

        Used from inside the receive loop, where waiting for a reply would mean
        waiting on the loop that is not running. The reply arrives later as an
        unmatched message and is ignored, which is correct: a paused request has
        exactly one right answer and nothing depends on its acknowledgement.
        """

        await self.websocket.send(
            json.dumps({"id": self._take_id(), "method": method, "params": params})
        )

    async def _decide(self, params: dict[str, Any]) -> None:
        """Let one intercepted request through, or fail it.

        This is where the browser stops being a hole in the destination policy.
        `Page.navigate` checks the address the caller asked for and nothing
        else: the page's own redirects, its fetches and every subresource are
        requests the browser makes on its own, and one of them pointing at
        169.254.169.254 is a page reading the container it is being viewed in.
        """

        identifier = params.get("requestId")
        url = str(params.get("request", {}).get("url", ""))
        if identifier is None or (self._allow is None and self._serve is None):
            # No policy means interception was never enabled, so this event is
            # not ours to answer. Answering it anyway would be this session
            # letting a request through on a page whose rule is "no network".
            return
        if self._serve is not None:
            served = self._serve(url)
            if served is not None:
                media_type, body = served
                await self._notify(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": identifier,
                        "responseCode": 200,
                        "responseHeaders": [{"name": "Content-Type", "value": media_type}],
                        "body": base64.b64encode(body).decode("ascii"),
                    },
                )
                return
        if self._allow is not None and await self._allow(url):
            await self._notify("Fetch.continueRequest", {"requestId": identifier})
            return
        self.refused.append(url)
        await self._notify(
            "Fetch.failRequest", {"requestId": identifier, "errorReason": "AccessDenied"}
        )

    async def _event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Fetch.requestPaused":
            await self._decide(params)
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            exception = details.get("exception", {})
            self.console_errors.append(
                str(exception.get("description") or details.get("text") or "JavaScript error")
            )
        elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "assert"}:
            values = [
                item.get("value", item.get("description", ""))
                for item in params.get("args", [])
            ]
            self.console_errors.append(" ".join(str(value) for value in values).strip())
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            # A request this session refused is reported as refused, not as an
            # error of the page: the page did nothing wrong by asking.
            if entry.get("level") == "error" and str(entry.get("url", "")) not in self.refused:
                self.console_errors.append(str(entry.get("text", "browser log error")))

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0
    ) -> dict[str, Any]:
        request_id = self._take_id()
        await self.websocket.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(await asyncio.wait_for(self.websocket.recv(), timeout=timeout))
            if message.get("id") != request_id:
                await self._event(message)
                continue
            if "error" in message:
                detail = message["error"].get("message", message["error"])
                raise RuntimeError(f"{method}: {detail}")
            return message.get("result", {})

    async def evaluate(self, expression: str, timeout: float = 5.0) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout,
        )
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise RuntimeError(str(remote.get("description", "browser evaluation failed")))
        return remote.get("value")

    async def viewport(self, width: int, height: int) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )

    async def wait_for_load(self, attempts: int = 50, pause: float = 0.05) -> bool:
        for _ in range(attempts):
            if await self.evaluate("document.readyState") == "complete":
                return True
            await asyncio.sleep(pause)
        return False

    async def screenshot(self, timeout: float = 15.0) -> str:
        result = await self.call(
            "Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout
        )
        return str(result["data"])


# How long a freshly launched browser may take to open its DevTools port. A
# budget in seconds rather than a count of polls, because what it has to cover
# is the browser's own first launch in a fresh container: on 2026-09-05 the
# renderer's first page open in a cold deployed container failed at 3.4 s
# — the old budget was sixty polls of 50 ms — and the retry a second later
# found the browser ready (ISS-0051). Fifteen seconds is generous for a warm
# machine and still bounded for a broken one.
DEVTOOLS_READY_SECONDS = 15.0


async def _wait_for_debugger(
    port: int, process: subprocess.Popen[bytes], timeout: float = DEVTOOLS_READY_SECONDS
) -> str:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"browser exited before DevTools was ready ({process.returncode})")
        try:
            payload = await asyncio.to_thread(_read_json, url)
            return str(payload["Browser"])
        except (OSError, KeyError, RuntimeError):
            if time.monotonic() >= deadline:
                raise RuntimeError("browser DevTools endpoint did not become ready") from None
            await asyncio.sleep(0.05)


@contextlib.asynccontextmanager
async def open_page(
    browser: Path | None = None,
    extra_flags: Sequence[str] = (),
    max_message_bytes: int = 8 * 1024 * 1024,
    allow: Callable[[str], Awaitable[bool]] | None = None,
    serve: "Server | None" = None,
) -> AsyncIterator[tuple[CdpSession, str]]:
    """Start a private browser, open one blank page, and always close both.

    The profile is a temporary directory, so nothing survives the call: no
    cookies, no history, no cache carried from one page to the next. That is a
    privacy property as much as a hygiene one — two pages the assistant looks at
    for two different people never share a session.

    `allow` is asked about every request the browser makes, once interception is
    enabled. A caller that passes nothing gets no interception, which is right
    for a page that has already been forbidden the network entirely.
    """

    executable = Path(browser).resolve() if browser else find_chromium_browser()
    if executable is None or not executable.is_file():
        raise RuntimeError("no installed Chrome/Edge browser was found")

    port = _free_port()
    # Chromium's parent can exit a fraction before one of its helpers stops
    # touching the profile. On Linux that race used to make rmtree raise
    # ``Directory not empty`` and discard an otherwise successful screenshot.
    # A temporary browser profile is cleanup, not the product result, so a
    # best-effort removal must never turn evidence already collected into a
    # failed tool call. The container is ephemeral, and on a personal machine
    # the next normal temp cleanup can collect anything a late helper retained.
    with tempfile.TemporaryDirectory(
        prefix="local-agent-browser-", ignore_cleanup_errors=True
    ) as profile:
        process = subprocess.Popen(
            [
                str(executable),
                "--headless=new",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                *container_flags(),
                *extra_flags,
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            name = await _wait_for_debugger(port, process)
            page = await asyncio.to_thread(_read_json, f"http://127.0.0.1:{port}/json/new", "PUT")
            async with connect(
                str(page["webSocketDebuggerUrl"]),
                open_timeout=5,
                max_size=max_message_bytes,
            ) as websocket:
                session = CdpSession(websocket, allow, serve)
                for domain in ("Runtime.enable", "Page.enable", "Log.enable", "Network.enable"):
                    await session.call(domain)
                if allow is not None or serve is not None:
                    # Every request, not only documents: a subresource is a
                    # request to an address just as much as a navigation is.
                    await session.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
                yield session, name
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 3)


# --- one page, with the full operation set -----------------------------------

# The family's codes live here because the session is where they arise; the
# tool module re-exports them.
UNAVAILABLE = "browser.unavailable"  # no browser on this machine
LOAD_FAILED = "browser.load_failed"  # the page could not be shown
STALE_REF = "browser.stale_ref"  # a ref the last snapshot did not give, or its element is gone
REFUSED = "browser.refused"  # an address this session's policy does not allow

MAX_SNAPSHOT_CHARS = 12_000
MAX_LINE_CHARS = 120

# Where a local artifact lives while the browser looks at it. A `data:` URL has
# no origin, so `localStorage` throws and a relative `styles.css` resolves to
# nothing; seen live 2026-09-03, the model was told a SecurityError the app
# does not have (ISSUES.md ISS-0014). A synthetic http origin, answered from
# the directory and from nowhere else, is what the person's browser gives the
# same files.
ARTIFACT_ORIGIN = "http://artifact.local"

# What answers a request: the media type and the bytes, or None for "not here".
Server = Callable[[str], "tuple[str, bytes] | None"]


def serve_directory(root: Path, max_bytes: int = 8 * 1024 * 1024) -> Server:
    """Answer requests under `ARTIFACT_ORIGIN` with files from one directory.

    The URL path is resolved inside the root the way every path-taking tool
    resolves one; anything that leaves it, is not a file, or is too large is
    simply not served, and the request fails as any other refused one does.
    """

    base = Path(root).resolve()

    def serve(url: str) -> tuple[str, bytes] | None:
        if not url.startswith(ARTIFACT_ORIGIN + "/"):
            return None
        path = url[len(ARTIFACT_ORIGIN) + 1 :].split("?", 1)[0].split("#", 1)[0]
        try:
            target = (base / urllib.request.url2pathname(path)).resolve()
        except (OSError, RuntimeError):
            return None
        if base not in target.parents or not target.is_file() or target.stat().st_size > max_bytes:
            return None
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in ("application/javascript", "application/json"):
            media_type += "; charset=utf-8"
        return media_type, target.read_bytes()

    return serve


def artifact_url(root: Path, file: Path) -> str:
    relative = Path(file).resolve().relative_to(Path(root).resolve()).as_posix()
    return f"{ARTIFACT_ORIGIN}/{urllib.request.pathname2url(relative)}"

# What the page's DOM is turned into: one line per element that means
# something to a person — a role, a name, a state — and a ref on everything
# that can be acted on. The map from ref to element is kept on the window so an
# action can find the element the model was just shown; it is regenerated by
# every snapshot, so a ref is valid until the next one.
_SNAPSHOT = r"""
(() => {
  const ROLES = {a: 'link', button: 'button', textarea: 'textbox', select: 'combobox',
    option: 'option', img: 'img', h1: 'heading', h2: 'heading', h3: 'heading',
    h4: 'heading', h5: 'heading', h6: 'heading', ul: 'list', ol: 'list', li: 'listitem',
    nav: 'navigation', main: 'main', header: 'banner', footer: 'contentinfo',
    form: 'form', table: 'table', tr: 'row', th: 'columnheader', td: 'cell',
    dialog: 'dialog', label: 'label', summary: 'button', details: 'group',
    section: 'region', article: 'article', canvas: 'canvas', video: 'video',
    audio: 'audio', p: 'paragraph', pre: 'code', code: 'code', blockquote: 'quote'};
  const INPUTS = {checkbox: 'checkbox', radio: 'radio', button: 'button',
    submit: 'button', reset: 'button', range: 'slider', number: 'spinbutton',
    file: 'button', color: 'button', hidden: null};
  const ACTIONABLE = new Set(['link', 'button', 'textbox', 'checkbox', 'radio',
    'combobox', 'slider', 'spinbutton', 'tab', 'menuitem', 'switch', 'searchbox']);
  // A container is named only by what its author called it; its text is its
  // children's, and repeating it on the container would say everything twice.
  const CONTAINERS = new Set(['main', 'navigation', 'banner', 'contentinfo', 'form',
    'region', 'article', 'list', 'listitem', 'table', 'row', 'group', 'dialog',
    'canvas', 'video', 'audio']);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'META', 'LINK', 'HEAD', 'TITLE']);
  const refs = {};
  const lines = [];
  let count = 0;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const hidden = (el) => {
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return true;
    const style = getComputedStyle(el);
    return style.display === 'none' || style.visibility === 'hidden';
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return type in INPUTS ? INPUTS[type] : 'textbox';
    }
    if (tag === 'a' && !el.hasAttribute('href')) return null;
    if (el.isContentEditable && !(tag in ROLES)) return 'textbox';
    return ROLES[tag] || null;
  };
  const nameOf = (el, role) => {
    const labelled = el.getAttribute('aria-labelledby');
    if (labelled) {
      const parts = labelled.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean);
      if (parts.length) return clean(parts.map((p) => p.innerText || p.textContent).join(' '));
    }
    const own = el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('title');
    if (own) return clean(own);
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText || el.labels[0].textContent);
    if (el.placeholder) return clean(el.placeholder);
    if (role === 'textbox' || role === 'combobox' || role === 'slider' || role === 'spinbutton') {
      return clean(el.name || el.id);
    }
    if (role === 'img' || CONTAINERS.has(role)) return '';
    return clean(el.innerText !== undefined ? el.innerText : el.textContent);
  };
  const actionable = (el, role) =>
    ACTIONABLE.has(role) || el.hasAttribute('onclick') || el.isContentEditable ||
    (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1');
  const emit = (depth, text) => lines.push([depth, text]);
  const walk = (node, depth) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = clean(node.textContent);
      if (text) emit(depth, 'text: ' + text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || SKIP.has(node.tagName)) return;
    if (hidden(node)) return;
    const role = roleOf(node);
    let next = depth;
    if (role) {
      const parts = [role];
      const name = nameOf(node, role);
      if (name) parts.push(JSON.stringify(name));
      if (actionable(node, role)) {
        count += 1;
        const ref = 'e' + count;
        refs[ref] = node;
        parts.push('[ref=' + ref + ']');
      }
      if (node.tagName === 'A' && node.getAttribute('href')) parts.push('href=' + JSON.stringify(node.getAttribute('href')));
      if ((role === 'textbox' || role === 'combobox' || role === 'spinbutton' || role === 'slider') && node.value !== undefined && node.value !== '') {
        parts.push('value=' + JSON.stringify(String(node.value).slice(0, 80)));
      }
      if (role === 'combobox' && node.options) {
        parts.push('options=' + JSON.stringify(Array.from(node.options).slice(0, 12).map((o) => o.textContent.trim())));
      }
      if (node.checked) parts.push('checked');
      if (node.disabled) parts.push('disabled');
      if (node.getAttribute('aria-expanded')) parts.push('expanded=' + node.getAttribute('aria-expanded'));
      if (role === 'heading') parts.push('level=' + (node.getAttribute('aria-level') || node.tagName.slice(1)));
      emit(depth, parts.join(' '));
      next = depth + 1;
      // A named control already carries its text; walking into it repeats it.
      if (ACTIONABLE.has(role)) return;
      if (['heading', 'paragraph', 'option', 'label', 'cell', 'columnheader', 'code', 'quote'].includes(role)) return;
    }
    for (const child of node.childNodes) walk(child, next);
  };
  walk(document.body || document.documentElement, 0);
  Object.defineProperty(window, '__agent_refs__', {value: refs, configurable: true, enumerable: false, writable: true});
  return {lines: lines, refs: Object.keys(refs)};
})()
"""

# The keys a person presses that are not a character. Chromium wants the
# Windows virtual key code to route them; `text` is what the key inserts.
_KEYS: dict[str, tuple[int, str]] = {
    "Enter": (13, "\r"),
    "Tab": (9, ""),
    "Escape": (27, ""),
    "Backspace": (8, ""),
    "Delete": (46, ""),
    "Space": (32, " "),
    "ArrowLeft": (37, ""),
    "ArrowUp": (38, ""),
    "ArrowRight": (39, ""),
    "ArrowDown": (40, ""),
    "Home": (36, ""),
    "End": (35, ""),
    "PageUp": (33, ""),
    "PageDown": (34, ""),
}


class BrowserError(RuntimeError):
    """The session could not do what was asked; `code` says which way."""

    def __init__(
        self, message: str, *, code: str = LOAD_FAILED, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Snapshot:
    """The page as text: an outline with a ref on everything that can be acted on."""

    text: str
    refs: tuple[str, ...]
    total_lines: int
    shown_lines: int

    @property
    def truncated(self) -> bool:
        return self.shown_lines < self.total_lines


def format_snapshot(
    lines: Sequence[tuple[int, str]],
    max_chars: int = MAX_SNAPSHOT_CHARS,
    query: str | None = None,
) -> tuple[str, int]:
    """Indent, filter and bound the lines the page gave; returns text and lines shown.

    `query` keeps the lines that mention it, so a page too large to show whole
    can still be asked about the part that matters. The bound is a character
    budget, because that is what the model pays in; a cut says how much is
    missing and how to see it.
    """

    needle = query.strip().lower() if query else ""
    kept = [(depth, text) for depth, text in lines if not needle or needle in text.lower()]
    if needle and not kept:
        return f"(nothing on the page mentions {query!r})", 0
    shown: list[str] = []
    used = 0
    for depth, text in kept:
        if len(text) > MAX_LINE_CHARS:
            text = text[: MAX_LINE_CHARS - 1] + "…"
        line = "  " * depth + "- " + text
        if used + len(line) + 1 > max_chars:
            break
        shown.append(line)
        used += len(line) + 1
    count = len(shown)
    if count < len(kept):
        missing = len(kept) - count
        shown.append(f"… {missing} more line(s) not shown; narrow with a query or raise max_chars")
    return "\n".join(shown), count


class BrowserSession:
    """One page in a private browser, driven as a person would drive it.

    Designed for the whole set — open, snapshot, screenshot, evaluate, console,
    navigate, click, type, press, select — so that exposing an action later
    changes nothing about what `snapshot` returns. Which of them a tool offers
    the model is the tool's decision.

    The trust boundary is a property of the session. An `offline` session has
    every network scheme blocked and opens documents, not addresses: that is
    the local artifact, rendered where the agent runs. A session with an
    `allow` policy is asked about every request the page makes: that is the
    public page, rendered where nothing is worth reaching.

    Actions take a ref from the last snapshot, never a selector: a ref is what
    the model was just shown, and a selector is a guess.
    """

    def __init__(self, cdp: CdpSession, browser_name: str, *, offline: bool) -> None:
        self.cdp = cdp
        self.browser_name = browser_name
        self.offline = offline
        self._refs: tuple[str, ...] = ()

    # -- observation ---------------------------------------------------------

    async def open(
        self,
        *,
        document: str | None = None,
        url: str | None = None,
        file: Path | None = None,
        root: Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Show a document given as text, a file the session serves, or an address.

        `file` needs a session opened with `serve=serve_directory(root)` and the
        same `root`: the page then has an origin, storage and its sibling files.
        """

        given = [value for value in (document, url, file) if value is not None]
        if len(given) != 1:
            raise ValueError("open takes exactly one of document, url and file")
        if file is not None:
            if root is None:
                raise ValueError("open(file=) needs the root the session serves")
            url = artifact_url(root, file)
        elif document is not None:
            encoded = base64.b64encode(document.encode("utf-8")).decode("ascii")
            url = f"data:text/html;charset=utf-8;base64,{encoded}"
        assert url is not None
        await self.navigate(url, timeout=timeout)

    async def navigate(self, url: str, timeout: float = 10.0) -> None:
        """Go to an address and wait for the page to finish loading."""

        if self.offline and not url.startswith(("data:", "about:", ARTIFACT_ORIGIN + "/")):
            raise BrowserError(
                "this session shows local documents only and reaches no address",
                code=REFUSED,
            )
        navigation = await self.cdp.call("Page.navigate", {"url": url}, timeout=timeout)
        if navigation.get("errorText"):
            raise BrowserError(
                "the page could not be opened",
                code=LOAD_FAILED,
                detail=str(navigation["errorText"]),
            )
        attempts = max(1, int(timeout / 0.05))
        if not await self.cdp.wait_for_load(attempts=attempts, pause=0.05):
            raise BrowserError("the page did not finish loading", code=LOAD_FAILED)
        await self._settle()
        self._refs = ()

    async def _settle(self, seconds: float = 0.2) -> None:
        """Let the page's own scripts run before it is looked at."""

        await asyncio.sleep(seconds)

    async def snapshot(
        self, max_chars: int = MAX_SNAPSHOT_CHARS, query: str | None = None
    ) -> Snapshot:
        """The page as an outline with refs. Regenerates the ref map."""

        result = await self.cdp.evaluate(_SNAPSHOT)
        found = result if isinstance(result, dict) else {}
        lines = [(int(depth), str(text)) for depth, text in found.get("lines", []) if str(text)]
        self._refs = tuple(str(ref) for ref in found.get("refs", []))
        text, shown = format_snapshot(lines, max_chars, query)
        return Snapshot(text=text, refs=self._refs, total_lines=len(lines), shown_lines=shown)

    async def visible_text(self, max_chars: int = MAX_VISIBLE_TEXT) -> str:
        value = await self.cdp.evaluate(
            f"(document.body && document.body.innerText || '').slice(0, {int(max_chars)})"
        )
        return str(value or "")

    async def title(self) -> str:
        return " ".join(str(await self.cdp.evaluate("document.title") or "").split())

    async def location(self) -> str:
        return str(await self.cdp.evaluate("location.href") or "")

    async def viewport(self, width: int, height: int) -> None:
        await self.cdp.viewport(width, height)

    async def screenshot(
        self, full_page: bool = False, max_height: int = 6_000, timeout: float = 15.0
    ) -> str:
        """A PNG of the viewport, base64; the whole page when asked."""

        if full_page:
            metrics = await self.cdp.call("Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            visual = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
            width = int(visual.get("clientWidth") or size.get("width") or 900)
            height = int(size.get("height") or visual.get("clientHeight") or 700)
            await self.cdp.viewport(width, max(1, min(height, max_height)))
        return await self.cdp.screenshot(timeout=timeout)

    async def evaluate(self, expression: str, timeout: float = 5.0) -> Any:
        """Run an expression in the page and bring back its value."""

        try:
            return await self.cdp.evaluate(expression, timeout)
        except RuntimeError as error:
            raise BrowserError("the expression failed in the page", detail=str(error)) from error

    def console(self) -> list[str]:
        """Every distinct error the page has logged or thrown so far."""

        return list(dict.fromkeys(self.cdp.console_errors))

    @property
    def refused(self) -> list[str]:
        """Requests the policy did not let out, distinct, in order."""

        return list(dict.fromkeys(self.cdp.refused))

    # -- action --------------------------------------------------------------

    def _element(self, ref: str) -> str:
        if ref not in self._refs:
            raise BrowserError(
                f"{ref!r} is not a ref from the last snapshot; take a snapshot and use its refs",
                code=STALE_REF,
            )
        return f"(window.__agent_refs__ || {{}})[{json.dumps(ref)}]"

    async def _center(self, ref: str) -> tuple[float, float]:
        element = self._element(ref)
        rect = await self.cdp.evaluate(
            f"""(() => {{
              const el = {element};
              if (!el || !el.isConnected) return null;
              el.scrollIntoView({{block: 'center', inline: 'center'}});
              const r = el.getBoundingClientRect();
              return {{x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height}};
            }})()"""
        )
        if not isinstance(rect, dict):
            raise BrowserError(
                f"the element {ref!r} is no longer on the page; take a new snapshot",
                code=STALE_REF,
            )
        if not rect.get("w") or not rect.get("h"):
            raise BrowserError(f"the element {ref!r} has no size to click", code=STALE_REF)
        return float(rect["x"]), float(rect["y"])

    async def click(self, ref: str) -> None:
        """A real click at the element's centre, so handlers and defaults both run."""

        x, y = await self._center(ref)
        for kind in ("mousePressed", "mouseReleased"):
            await self.cdp.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )
        await self._settle()

    async def type(self, ref: str, text: str, clear: bool = True) -> None:
        """Focus the element and enter text the way typing does."""

        element = self._element(ref)
        focused = await self.cdp.evaluate(
            f"""(() => {{
              const el = {element};
              if (!el || !el.isConnected) return false;
              el.focus();
              if ({json.dumps(clear)}) {{
                if (typeof el.select === 'function') el.select();
                else if (el.isContentEditable) {{
                  const range = document.createRange(); range.selectNodeContents(el);
                  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
                }}
              }}
              return document.activeElement === el;
            }})()"""
        )
        if focused is not True:
            raise BrowserError(
                f"the element {ref!r} could not take focus; take a new snapshot",
                code=STALE_REF,
            )
        if clear:
            await self._key("Delete", 46, "")
        if text:
            await self.cdp.call("Input.insertText", {"text": text})
        await self._settle(0.05)

    async def _key(self, key: str, code: int, text: str) -> None:
        down: dict[str, Any] = {
            "type": "keyDown" if text else "rawKeyDown",
            "key": key,
            "windowsVirtualKeyCode": code,
        }
        if text:
            down["text"] = text
        await self.cdp.call("Input.dispatchKeyEvent", down)
        await self.cdp.call(
            "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "windowsVirtualKeyCode": code}
        )

    async def press(self, key: str) -> None:
        """Press one key on whatever has focus: `Enter`, `Tab`, an arrow, or a character."""

        if key in _KEYS:
            code, text = _KEYS[key]
        elif len(key) == 1:
            code, text = (ord(key.upper()) if key.isalnum() else 0), key
        else:
            raise BrowserError(
                f"unknown key {key!r}; use one of {', '.join(_KEYS)} or a single character",
                code=STALE_REF,
            )
        await self._key(key, code, text)
        await self._settle()

    async def select(self, ref: str, value: str) -> None:
        """Choose an option of a select by its value or its visible text."""

        element = self._element(ref)
        chosen = await self.cdp.evaluate(
            f"""(() => {{
              const el = {element};
              if (!el || !el.isConnected || !el.options) return 'not-a-select';
              const wanted = {json.dumps(value)};
              const option = Array.from(el.options).find(
                (o) => o.value === wanted || o.textContent.trim() === wanted);
              if (!option) return 'no-such-option';
              el.value = option.value;
              el.dispatchEvent(new Event('input', {{bubbles: true}}));
              el.dispatchEvent(new Event('change', {{bubbles: true}}));
              return 'ok';
            }})()"""
        )
        if chosen == "not-a-select":
            raise BrowserError(f"{ref!r} is not a select; take a new snapshot", code=STALE_REF)
        if chosen == "no-such-option":
            raise BrowserError(f"the select {ref!r} has no option {value!r}", code=STALE_REF)
        await self._settle(0.05)


@contextlib.asynccontextmanager
async def open_browser(
    browser: Path | None = None,
    *,
    offline: bool = False,
    allow: Callable[[str], Awaitable[bool]] | None = None,
    serve: Server | None = None,
    viewport: tuple[int, int] = (900, 700),
    max_message_bytes: int = 8 * 1024 * 1024,
) -> AsyncIterator[BrowserSession]:
    """A `BrowserSession` on a fresh private browser, closed on the way out.

    `offline` confines navigation to documents, `about:` and the artifact
    origin; without `serve` or `allow` it also blocks every network scheme, so
    a document cannot fetch, redirect or embed its way anywhere. `serve`
    answers requests under `ARTIFACT_ORIGIN` from a directory. `allow` is the
    request policy asked about everything not served; a session with neither
    fails every other request. A session must be offline or have a policy; a
    browser with no rule is not something any capability here wants.
    """

    if not offline and allow is None:
        raise ValueError("a session that navigates anywhere needs a request policy")
    if serve is not None and not offline:
        raise ValueError("a served directory belongs to an offline session")
    try:
        async with open_page(
            browser, allow=allow, serve=serve, max_message_bytes=max_message_bytes
        ) as (cdp, name):
            if offline and serve is None and allow is None:
                await cdp.call(
                    "Network.setBlockedURLs",
                    {"urls": ["http://*", "https://*", "file://*", "ftp://*", "ws://*", "wss://*"]},
                )
            await cdp.viewport(*viewport)
            yield BrowserSession(cdp, name, offline=offline)
    except BrowserError:
        raise
    except RuntimeError as error:
        # `open_page` raises one kind for "no browser here" and "the browser
        # broke"; which it was is the only thing the caller needs.
        code = UNAVAILABLE if "no installed" in str(error) else LOAD_FAILED
        raise BrowserError(str(error), code=code) from error
