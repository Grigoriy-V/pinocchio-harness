"""Three web tools, because they are three different acts.

Searching asks a provider for links and spends its credit. Fetching reads one
page over plain HTTP and spends nothing. Viewing opens a page in a real browser,
which is the only one that runs someone else's code and the only one that
produces a picture.

Collapsing them into a single "browse" tool would hide exactly the differences
the agent should be choosing between — cost, what executes, and whether the
answer is text or an image.

`view_web_page` follows the presentation rule the rest of the workspace tools
follow: the screenshot is evidence for the agent, saved in the workspace, and
nothing reaches the person until the agent decides to `send_file` it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.config import WebSettings
from app.models import ContentPart
from app.tools.base import Tool, ToolError, handover
from app.web import (
    WebError,
    fetch_page,
    format_results,
    render_page,
    search_web,
)

MAX_VIEWS_KEPT = 20


async def _fetch(settings: WebSettings, url: str, offset: int = 0) -> str:
    try:
        return (await fetch_page(url, settings)).as_text(offset=offset)
    except WebError as error:
        raise ToolError(str(error), code=error.code) from error


async def _search(settings: WebSettings, query: str, count: int | None) -> str:
    try:
        return format_results(query, await search_web(query, settings, count))
    except WebError as error:
        raise ToolError(str(error), code=error.code) from error


def _artifact(root: Path, url: str) -> Path:
    """A stable name per address, so viewing the same page twice keeps one file."""

    identity = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return root / ".agent" / "web" / f"page-{identity}.png"


def _keep_recent(directory: Path, protect: Path, keep: int | None = None) -> None:
    """Bound what viewing leaves behind.

    Each distinct address keeps one screenshot, and on a deployed volume that
    storage is permanent — a capability that quietly grows a person's workspace
    forever is a leak, not a feature. The oldest go first, and the file just
    written is never one of them: the agent has just been told that path.
    """

    budget = MAX_VIEWS_KEPT if keep is None else keep
    shots = sorted(directory.glob("page-*.png"), key=lambda path: path.stat().st_mtime)
    stale = [path for path in shots if path != protect][: max(0, len(shots) - budget)]
    for path in stale:
        path.unlink(missing_ok=True)


async def _view(
    root: Path, settings: WebSettings, url: str, full_page: bool
) -> list[ContentPart]:
    try:
        rendered = await render_page(url, settings, full_page)
    except WebError as error:
        raise ToolError(str(error), code=error.code) from error

    artifact = _artifact(root, rendered.url)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(rendered.screenshot)
    _keep_recent(artifact.parent, artifact)
    note = [
        rendered.as_text(),
        "",
        f"Screenshot saved at {artifact.relative_to(root).as_posix()} for your inspection. "
        f"Nothing was sent to the person; {handover(artifact.relative_to(root).as_posix(), 'this screenshot')}",
    ]
    if rendered.console_errors:
        note.append(f"Browser errors on the page: {'; '.join(rendered.console_errors)}")
    return [
        ContentPart(kind="text", text="\n".join(note)),
        ContentPart(kind="image", data=rendered.screenshot, media_type="image/png"),
    ]


def web_search_tools(root: Path, settings: WebSettings | None = None) -> list[Tool]:
    """The search tool, or nothing at all where no provider is configured.

    Nothing, rather than a tool that always fails: the assistant's account of
    itself is generated from the tools it holds, so an unusable tool in that list
    is the assistant claiming an ability it does not have.
    """

    resolved = settings or WebSettings()
    if not resolved.firecrawl_api_key:
        return []
    return [
        Tool(
            name="search_web",
            replay_safe=True,
            description=(
                "Search the internet for pages about something, when you do not already "
                "have an address. It does not read any page: follow a result with "
                "fetch_page."
            ),
            returns="ranked titles, URLs and the short summaries the pages wrote about themselves.",
            leaves="the query with an outside search provider.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 2,
                        "description": "What to search for, in ordinary words.",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": f"How many results. Defaults to {resolved.search_results}.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            run=lambda query, count=None: _search(resolved, query, count),
        )
    ]


def web_fetch_tools(root: Path, settings: WebSettings | None = None) -> list[Tool]:
    resolved = settings or WebSettings()
    return [
        Tool(
            name="fetch_page",
            replay_safe=True,
            description=(
                "Read one public web page as text over a direct HTTP request, without "
                "running its JavaScript. The cheapest way to read the web. A page that "
                "builds itself in the browser comes back nearly empty: open those with "
                "view_web_page. Public http and https addresses only."
            ),
            returns=(
                "the page's text, in pages: the end of one says which offset to ask for next."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "minLength": 8,
                        "description": "The full http/https address of a public page.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Character offset to continue a long page from. Defaults to 0.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            run=lambda url, offset=0: _fetch(resolved, url, int(offset)),
        )
    ]


def web_view_tools(root: Path, settings: WebSettings | None = None) -> list[Tool]:
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")
    resolved = settings or WebSettings()
    return [
        Tool(
            name="view_web_page",
            replay_safe=True,
            description=(
                "Open a public web page in a real browser, with its JavaScript run, and "
                "look at it. For a page that needs JavaScript to show anything, or when the "
                "layout, a chart or a picture is what matters; slower than fetch_page."
            ),
            returns=(
                "the rendered text, a screenshot shown to you, and the workspace path the "
                "screenshot was saved to."
            ),
            leaves=(
                "the screenshot as a .png in your workspace; nothing reaches the person "
                "unless you send_file it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "minLength": 8,
                        "description": "The full http/https address of a public page.",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": (
                            "Capture the whole scrollable page instead of the first screen. "
                            "Defaults to false."
                        ),
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            run=lambda url, full_page=False: _view(
                resolved_root, resolved, url, bool(full_page)
            ),
        )
    ]


def web_tools(root: Path, settings: WebSettings | None = None) -> list[Any]:
    """All three, for a caller that wants the whole capability at once."""

    resolved = settings or WebSettings()
    return [
        *web_search_tools(root, resolved),
        *web_fetch_tools(root, resolved),
        *web_view_tools(root, resolved),
    ]
