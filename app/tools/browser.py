"""The page tool: one real browser page the model drives, one call per action.

The model chooses this tool like any other capability. It is not a verifier and
contains no task-specific acceptance logic: it returns page evidence — the
structure with refs, the visible text, console errors, a screenshot on request —
and the effect of each action, for the model to judge.

The page is driven through `BrowserSession` in `chromium.py`, the one API every
browser capability uses. What is decided here is what this page is allowed to
be: a file in the workspace, served to the page at a synthetic origin together
with its sibling files, or a public address; in both cases the public internet
reachable under the public renderer's policy and nothing private.

Until 2026-09-07 this was `inspect_page`: open, look, close, nothing else. A
page "fixed" from a static screenshot (ISS-0008) is what that bought. Now the
page stays open across calls within a turn, so a click is followed by a look
at what it did (roadmap 16).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import ContentPart
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError, handover
from app.tools.chromium import (
    LOAD_FAILED,
    MAX_SNAPSHOT_CHARS,
    MAX_VISIBLE_TEXT,
    REFUSED,
    STALE_REF,
    UNAVAILABLE,
    BrowserError,
    BrowserSession,
    container_flags,
    find_chromium_browser,
    open_browser,
    serve_directory,
)
from app.tools.documents import UNSUPPORTED
from app.tools.filesystem import NOT_A_FILE, NOT_FOUND, TOO_LARGE, resolve_in_root
from app.web import public_request_policy

MAX_HTML_BYTES = 2 * 1024 * 1024

# How long an open page waits for the next action before the browser is
# closed. A turn's actions come seconds apart; a page nobody has touched for
# this long belongs to a turn that ended, and a browser is not kept for it.
IDLE_SECONDS = 120.0

TOOL_NAME = "use_page"

ACTIONS = ("open", "snapshot", "click", "type", "press", "select", "evaluate", "screenshot", "console")

__all__ = [
    "ACTIONS",
    "IDLE_SECONDS",
    "LOAD_FAILED",
    "MAX_HTML_BYTES",
    "MAX_SNAPSHOT_CHARS",
    "MAX_VISIBLE_TEXT",
    "REFUSED",
    "STALE_REF",
    "TOOL_NAME",
    "UNAVAILABLE",
    "Pages",
    "browser_tools",
    "container_flags",
    "find_chromium_browser",
    "page_report",
    "write_png",
]


def write_png(path: Path, encoded: str) -> bytes:
    """Save a base64 screenshot into the workspace without a torn file."""

    data = base64.b64decode(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return data


def _local_document(root: Path, path: str) -> Path:
    target = resolve_in_root(root, path)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_file():
        raise ToolError(f"path {path!r} is not a file", code=NOT_A_FILE)
    if target.suffix.lower() not in {".html", ".htm"}:
        # The remedy beside the refusal (DeepSeek's shape): live, R 2026-09-04,
        # the model brought a chart here to look at it and was told only no.
        raise ToolError(
            f"{TOOL_NAME} opens .html and .htm files; an image is looked at "
            "with read_file, a PDF with view_pages",
            code=UNSUPPORTED,
        )
    if target.stat().st_size > MAX_HTML_BYTES:
        raise ToolError(
            f"HTML file exceeds the {MAX_HTML_BYTES}-byte browser limit", code=TOO_LARGE
        )
    return target


def page_report(
    *,
    title: str,
    structure: str,
    truncated: bool,
    text: str,
    console_errors: list[str],
    refused: list[str],
    did: str = "",
) -> str:
    """What the model reads about a page, as text it can quote from.

    Sections, not JSON: the structure and the visible text are multi-line, and
    a JSON string of them is one long escaped line the model has to unpick.
    `did` is the action's own line, first, so the result reads as cause and
    effect.
    """

    errors = "\n".join(f"- {line}" for line in console_errors) if console_errors else "none"
    cut = " (cut; the page has more)" if truncated else ""
    lines = []
    if did:
        lines.append(did)
    lines.append(f"title: {title or '(none)'}")
    lines.append(f"\nconsole errors since the last call:\n{errors}")
    if refused:
        blocked = "\n".join(f"- {line}" for line in refused)
        lines.append(f"\nrequests refused (the page asked for these and the policy said no):\n{blocked}")
    lines.append(f"\nstructure{cut}; an interactive element carries a ref:\n{structure or '(empty page)'}")
    lines.append(f"\nvisible text:\n{text or '(none)'}\n")
    return "\n".join(lines)


@dataclass
class _Open:
    """The page that is open: its session, and what has already been reported."""

    session: BrowserSession
    context: Any
    seen_errors: int = 0
    seen_refused: int = 0
    closer: asyncio.TimerHandle | None = None


@dataclass
class Pages:
    """One open page per workspace root, kept between calls of one turn.

    Held by the registry rather than by a toolbox, because a toolbox is built
    per thread and a turn's calls must find the same page. The page is closed
    by the next `open`, by `close`, or by `IDLE_SECONDS` without an action.
    """

    browser: Path | None = None
    idle_seconds: float = IDLE_SECONDS
    _open: dict[Path, _Open] = field(default_factory=dict)

    def get(self, root: Path) -> _Open | None:
        return self._open.get(root)

    async def open(self, root: Path, *, file: Path | None = None, url: str | None = None) -> _Open:
        await self.close(root)
        if file is not None:
            context = open_browser(
                self.browser,
                offline=True,
                serve=serve_directory(root),
                allow=public_request_policy(),
            )
        else:
            context = open_browser(self.browser, offline=False, allow=public_request_policy())
        session = await context.__aenter__()
        held = _Open(session=session, context=context)
        self._open[root] = held
        try:
            if file is not None:
                await session.open(file=file, root=root)
            else:
                assert url is not None
                await session.navigate(url)
        except BaseException:
            await self.close(root)
            raise
        self.touch(root)
        return held

    def touch(self, root: Path) -> None:
        """Push the idle close back: an action just happened."""

        held = self._open.get(root)
        if held is None:
            return
        if held.closer is not None:
            held.closer.cancel()
        loop = asyncio.get_running_loop()
        held.closer = loop.call_later(
            self.idle_seconds, lambda: asyncio.ensure_future(self.close(root))
        )

    async def close(self, root: Path) -> None:
        held = self._open.pop(root, None)
        if held is None:
            return
        if held.closer is not None:
            held.closer.cancel()
        try:
            await held.context.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - a browser that died is closed already
            pass

    async def close_all(self) -> None:
        for root in list(self._open):
            await self.close(root)


def _need(arguments: dict[str, Any], name: str, action: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ToolError(f"{action} needs {name}", code=BAD_ARGUMENTS)
    return value


async def _report(held: _Open, did: str = "", query: str | None = None) -> str:
    """The page as it is now, and what the page said since the last report."""

    session = held.session
    snapshot = await session.snapshot(MAX_SNAPSHOT_CHARS, query)
    text = await session.visible_text(MAX_VISIBLE_TEXT)
    title = await session.title()
    # One more evaluation drains console events that arrived after the last
    # call, which is where a script's late error would otherwise hide.
    await session.evaluate("0")
    errors = session.console()
    refused = [url for url in session.refused if not url.endswith("/favicon.ico")]
    new_errors, held.seen_errors = errors[held.seen_errors :], len(errors)
    new_refused, held.seen_refused = refused[held.seen_refused :], len(refused)
    return page_report(
        title=title,
        structure=snapshot.text,
        truncated=snapshot.truncated,
        text=text,
        console_errors=new_errors,
        refused=new_refused,
        did=did,
    )


async def use_page(root: Path, pages: Pages, action: str, **arguments: Any) -> list[ContentPart]:
    """One action on the page, and what the page looks like after it."""

    if action not in ACTIONS:
        raise ToolError(
            f"unknown action {action!r}; one of {', '.join(ACTIONS)}", code=BAD_ARGUMENTS
        )
    try:
        if action == "open":
            path, url = arguments.get("path"), arguments.get("url")
            if bool(path) == bool(url):
                raise ToolError("open takes exactly one of path and url", code=BAD_ARGUMENTS)
            if path:
                target = _local_document(root, str(path))
                held = await pages.open(root, file=target)
                did = f"opened {target.relative_to(root).as_posix()}"
            else:
                held = await pages.open(root, url=str(url))
                did = f"opened {url}"
            return [ContentPart(kind="text", text=await _report(held, did))]

        held = pages.get(root)
        if held is None:
            raise ToolError(
                f"no page is open; call {TOOL_NAME} with action open first", code=STALE_REF
            )
        pages.touch(root)
        session = held.session
        if action == "snapshot":
            return [ContentPart(kind="text", text=await _report(held, query=arguments.get("query")))]
        if action == "click":
            ref = _need(arguments, "ref", action)
            await session.click(ref)
            return [ContentPart(kind="text", text=await _report(held, f"clicked {ref}"))]
        if action == "type":
            ref = _need(arguments, "ref", action)
            text = arguments.get("text")
            if not isinstance(text, str):
                raise ToolError("type needs text", code=BAD_ARGUMENTS)
            await session.type(ref, text)
            return [ContentPart(kind="text", text=await _report(held, f"typed {text!r} into {ref}"))]
        if action == "press":
            key = _need(arguments, "key", action)
            await session.press(key)
            return [ContentPart(kind="text", text=await _report(held, f"pressed {key}"))]
        if action == "select":
            ref = _need(arguments, "ref", action)
            value = _need(arguments, "value", action)
            await session.select(ref, value)
            return [ContentPart(kind="text", text=await _report(held, f"selected {value!r} in {ref}"))]
        if action == "evaluate":
            expression = _need(arguments, "expression", action)
            value = await session.evaluate(expression)
            try:
                shown = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                shown = str(value)
            return [ContentPart(kind="text", text=f"result: {shown}")]
        if action == "console":
            errors = session.console()
            new, held.seen_errors = errors[held.seen_errors :], len(errors)
            listed = "\n".join(f"- {line}" for line in new) if new else "none"
            return [ContentPart(kind="text", text=f"console errors since the last call:\n{listed}")]
        # screenshot
        name = Path(str(await session.location()).rsplit("/", 1)[-1] or "page").stem or "page"
        artifact = root / ".agent" / "browser" / f"{name}-{secrets.token_hex(4)}.png"
        full = bool(arguments.get("full_page", False))
        image = await asyncio.to_thread(write_png, artifact, await session.screenshot(full_page=full))
        shown = artifact.relative_to(root).as_posix()
        return [
            ContentPart(
                kind="text",
                text=(
                    f"screenshot: {shown}; you see it below, the person has not; "
                    f"{handover(shown, 'this screenshot')}"
                ),
            ),
            ContentPart(kind="image", data=image, media_type="image/png"),
        ]
    except BrowserError as error:
        raise ToolError(str(error), code=error.code, detail=error.detail) from error


DESCRIPTION = (
    "Drive a real Chromium page. It is the same thing as Playwright or Puppeteer, "
    "packed into one tool: instead of writing a script, you call it once per action, "
    "with `action` and that action's fields.\n"
    "- open, with `path` (an .html file in your workspace) or `url` (a public page). "
    "Call this first; every other action needs an open page. Open a url here only "
    "when you need to act on that page; to read one, fetch_page is the default, and "
    "to see one rendered, view_web_page.\n"
    "- click with `ref`; type with `ref` and `text` (clears the field first); press "
    "with `key` (Enter, Tab, Escape, ArrowDown, or one character); select with `ref` "
    "and `value`. Refs come from the latest result, like e12; an older ref is refused.\n"
    "- snapshot: the page as it is now; `query` keeps only the lines that mention a word.\n"
    "- evaluate with `expression`: JavaScript in the page, as in the DevTools console.\n"
    "- screenshot (`full_page` for the whole scroll); console: errors since the last call.\n"
    "The page stays open between your calls until the turn ends or you open another. "
    "To check that a page works, do what the person would do — click the button, type "
    "into the field, press Enter — and read what came back; one screenshot is not a run. "
    "The page reaches public addresses and nothing private."
)

RETURNS = (
    "after open, snapshot and every action: the title, console errors since the last "
    "call, the structure with a ref on every interactive element, and the visible "
    "text; evaluate: the value as JSON; screenshot: the picture, shown to you, and its "
    "workspace path; console: the errors."
)

LEAVES = (
    "screenshot writes a .png under .agent/browser/ in your workspace and sends "
    "nothing to the person; the other actions leave nothing outside the page."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "path": {"type": "string", "description": "open: an .html file in the workspace."},
        "url": {"type": "string", "description": "open: a public http/https address."},
        "ref": {"type": "string", "description": "click, type, select: a ref from the latest result."},
        "text": {"type": "string", "description": "type: the text."},
        "key": {"type": "string", "description": "press: the key."},
        "value": {"type": "string", "description": "select: the option's value or visible text."},
        "expression": {"type": "string", "description": "evaluate: a JavaScript expression."},
        "query": {"type": "string", "description": "snapshot: keep only lines that mention this."},
        "full_page": {"type": "boolean", "description": "screenshot: the whole page. Defaults to false."},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def browser_tools(root: Path, browser: Path | None = None, pages: Pages | None = None) -> list[Tool]:
    """Build the model-selected page capability for one allowed root.

    `pages` is where the open page lives between calls; a caller that builds
    toolboxes more than once per turn passes the same one each time.
    """

    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")
    held = pages if pages is not None else Pages(browser=browser)

    async def run(action: str, **arguments: Any) -> list[ContentPart]:
        return await use_page(resolved, held, action, **arguments)

    return [
        Tool(
            name=TOOL_NAME,
            # Replaying an action on a page after a worker died is not safe:
            # the page is gone with the worker, and a click may have counted.
            replay_safe=False,
            description=DESCRIPTION,
            returns=RETURNS,
            leaves=LEAVES,
            parameters=PARAMETERS,
            run=run,
        )
    ]
