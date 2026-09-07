"""Does each capability actually work here, or does the assistant only say so?

`capabilities.py` reports what is wired up. This exercises it. The two read the
same registry on purpose: a capability that is advertised and does not run is
the assistant lying about itself, and that is now a mechanical comparison rather
than a thing someone remembers to check.

Every failure this exists to catch has already happened, in production, today:

- a store read left the connection in a transaction, so the next call failed —
  and no single-operation test saw it, because only the *order* was wrong;
- the page tool worked while the agent ran on a machine with a browser and
  stopped when execution moved into a container that has none;
- the deployment module was missing from its own image;
- checkpoint tables were created in one schema and looked for in another.

None of those are visible offline, so this is built to run **where the agent
runs** — the same code in the local profile and inside a deployed worker.

Cost is part of the contract. A probe declares whether it is free, spends a
provider's credit, or wakes the GPU, and everything but the free ones stays out
unless asked for by name. A diagnostic that quietly spends money is one nobody
runs.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.models import ToolCall
from app.tools import Toolbox, ToolError

Cost = Literal["free", "credit", "gpu"]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    cost: Cost = "free"

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} {self.name}: {self.detail}"


@dataclass(frozen=True)
class Probe:
    """One thing to try, named after the capability it stands for."""

    name: str
    cost: Cost
    run: Callable[[], Awaitable[str]]


async def attempt(probe: Probe) -> Check:
    """Run one probe. A probe that raises is a failure, never an exception here.

    A diagnostic that stops at the first problem hides the rest of them, and the
    rest are what tell you whether one thing broke or the environment did.
    """

    try:
        return Check(probe.name, True, await probe.run(), probe.cost)
    except Exception as error:  # noqa: BLE001 - reporting is the whole job
        detail = str(error) or type(error).__name__
        return Check(probe.name, False, f"{type(error).__name__}: {detail}", probe.cost)


async def run(
    probes: Sequence[Probe], include: Sequence[Cost] = ("free",)
) -> list[Check]:
    return [await attempt(probe) for probe in probes if probe.cost in include]


def report(checks: Sequence[Check]) -> str:
    if not checks:
        return "nothing was checked"
    failed = [check for check in checks if not check.ok]
    lines = [check.line() for check in checks]
    lines.append("")
    lines.append(
        f"{len(checks) - len(failed)}/{len(checks)} passed"
        if failed
        else f"all {len(checks)} passed"
    )
    return "\n".join(lines)


# --- the probes themselves ----------------------------------------------------


def store_probes(store, user_id: str) -> list[Probe]:
    """Exercise the store in the order a turn uses it, which is where it broke.

    Reading before assembling context is what a real turn does and what no
    isolated measurement did. The rows are written under a throwaway owner and
    the thread is deleted, so this leaves nothing behind but a fact, which is
    the one thing the contract says survives a deleted conversation.
    """

    async def turn_order() -> str:
        from app.models import ContentPart, Message

        thread = f"preflight-{uuid.uuid4().hex[:12]}"
        marker = f"preflight marker {uuid.uuid4().hex[:8]}"
        try:
            store.append(
                thread,
                [Message(role="user", content=[ContentPart(kind="text", text=marker)])],
                user_id,
            )
            store.threads(user_id)
            context = store.turn_context(thread, user_id, marker, 5)
            if not context.messages:
                raise RuntimeError("the context read returned no history")
            return f"append, list and context read in a turn's order ({thread})"
        finally:
            store.delete_thread(thread)

    async def memory() -> str:
        fact = f"preflight fact {uuid.uuid4().hex[:8]}"
        store.remember(fact, user_id)
        if fact not in store.search(fact.split()[-1], user_id):
            raise RuntimeError("a fact was saved and could not be found again")
        return "a fact was saved and retrieved by search"

    return [
        Probe("store.turn", "free", turn_order),
        Probe("store.memory", "free", memory),
    ]


def tool_probes(tools: Toolbox, root: Path) -> list[Probe]:
    """Run the file and browser tools the way the model runs them.

    Through `Toolbox`, not around it: the model's path includes argument
    validation and the rooted-path checks, and a probe that called the
    underlying function directly would pass while the model's call failed.
    """

    def _text(result) -> str:
        """The result's text, or the failure it reports, exactly as the model has it."""

        if result.failure is not None:
            raise ToolError(result.failure.message, code=result.failure.code)
        return " ".join(part.text or "" for part in result.content)

    def call(name: str, **arguments: object) -> str:
        return _text(tools.run(ToolCall(f"preflight-{name}", name, arguments)))

    async def call_async(name: str, **arguments: object) -> str:
        """The path an async tool actually takes.

        `Toolbox.run` refuses an async tool rather than running it half-way, and
        a probe that used it reported the capability broken while the tool was
        fine. Caught by `/check` itself, which is the job.
        """

        return _text(await tools.run_async(ToolCall(f"preflight-{name}", name, arguments)))

    async def files() -> str:
        name = f"preflight-{uuid.uuid4().hex[:8]}.txt"
        marker = uuid.uuid4().hex
        call("write_file", path=name, content=marker)
        try:
            if call("read_file", path=name).strip() != marker:
                raise RuntimeError("the file did not read back as written")
            call("list_files")
        finally:
            (root / name).unlink(missing_ok=True)
        return f"write, read and list inside {root}"

    async def browser() -> str:
        name = f"preflight-{uuid.uuid4().hex[:8]}.html"
        page = root / name
        page.write_text(
            "<html><body><p>preflight</p></body></html>", encoding="utf-8"
        )
        try:
            opened = await tools.run_async(
                ToolCall("preflight-browser", "use_page", {"action": "open", "path": name})
            )
            if "preflight" not in _text(opened):
                raise RuntimeError("the page opened but its text did not come back")
            result = await tools.run_async(
                ToolCall("preflight-browser-shot", "use_page", {"action": "screenshot"})
            )
            _text(result)
            images = [part for part in result.content if part.kind == "image"]
            if not images:
                raise RuntimeError("the page rendered but returned no screenshot")
            return f"a page was opened, read and returned {len(images)} screenshot(s)"
        finally:
            page.unlink(missing_ok=True)
            # The browser tool also writes the screenshot into the workspace.
            # A diagnostic that can be run whenever must not accumulate.
            for shot in (root / ".agent" / "browser").glob(f"{page.stem}-*.png"):
                shot.unlink(missing_ok=True)

    async def documents() -> str:
        """A real parser on a real file, because that is where this breaks.

        The library is an optional dependency group, so the failure this catches
        is an image built without it — invisible offline, and indistinguishable
        from "the document was empty" to whoever sent one.
        """

        from app.documents import PDF, read_sections
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        try:
            read_sections(buffer.getvalue(), PDF)
        except Exception as error:
            # A blank page has no text layer, which this is supposed to say. Any
            # other failure means the parser is not there or does not work.
            if "no text layer" not in str(error):
                raise
        name = f"preflight-{uuid.uuid4().hex[:8]}.md"
        (root / name).write_text("# Heading\nbody text\n", encoding="utf-8")
        try:
            text = call("read_document", path=name)
            if "Heading" not in text:
                raise RuntimeError("a document was written and did not read back")
        finally:
            (root / name).unlink(missing_ok=True)
        return "a PDF parser answered and a document read back with its headings"

    async def pages() -> str:
        """Render a page and check that a picture, not a promise, comes back.

        This is the path a scan takes, and it uses a second native library. An
        image built without it fails here rather than in front of someone who
        just sent a scanned contract.
        """

        from app.documents import render_pages

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        name = f"preflight-{uuid.uuid4().hex[:8]}.pdf"
        (root / name).write_bytes(buffer.getvalue())
        previews = root / ".agent" / "documents"
        before = set(previews.glob("*.png")) if previews.is_dir() else set()
        try:
            result = await tools.run_async(
                ToolCall("preflight-pages", "view_pages", {"path": name})
            )
            _text(result)
            images = [part for part in result.content if part.kind == "image"]
            if not images or not (images[0].data or b"").startswith(b"\x89PNG"):
                raise RuntimeError("a page was requested and no image came back")
            render_pages(buffer.getvalue(), 1, 1)
        finally:
            (root / name).unlink(missing_ok=True)
            after = set(previews.glob("*.png")) if previews.is_dir() else set()
            for preview in after - before:
                preview.unlink(missing_ok=True)
        return "a PDF page was rendered to an image the model can look at"

    async def presentation() -> str:
        """Select a real workspace file without involving an interface."""

        name = f"preflight-{uuid.uuid4().hex[:8]}.txt"
        marker = uuid.uuid4().hex.encode("ascii")
        (root / name).write_bytes(marker)
        try:
            result = tools.run(ToolCall("preflight-presentation", "send_file", {"path": name}))
            _text(result)
            outgoing = [part for part in result.content if part.outbound]
            if len(outgoing) != 1 or outgoing[0].data != marker:
                raise RuntimeError("the selected file did not become one outbound item")
        finally:
            (root / name).unlink(missing_ok=True)
        return "a workspace file became one explicit outbound item"

    async def fetch() -> str:
        """Reach one real public page, because egress is the thing in doubt.

        `example.com` is IANA's own reserved documentation domain: it exists to
        be requested by things like this, it has no owner to inconvenience, and
        its content does not change. What this catches is a container with no
        outbound network at all — invisible offline, and indistinguishable from
        "the page was empty" to whoever asked a question about it.
        """

        text = await call_async("fetch_page", url="https://example.com/")
        if "example" not in text.lower():
            raise RuntimeError("the page was fetched and carried nothing recognisable")
        return "a public page was fetched and read as text"

    async def view() -> str:
        """Render the same page wherever this profile renders, and read it back.

        Both halves are checked because they failed separately. A picture with
        no text is what a broken DevTools session produced — screenshots kept
        arriving while every page came back untitled and empty — and a probe
        that looked only at the PNG called that working.
        """

        result = await tools.run_async(
            ToolCall("preflight-view", "view_web_page", {"url": "https://example.com/"})
        )
        text = _text(result)
        if "example domain" not in text.lower():
            raise RuntimeError("the page was rendered but its text did not come back")
        images = [part for part in result.content if part.kind == "image"]
        if not images or not (images[0].data or b"").startswith(b"\x89PNG"):
            raise RuntimeError("a page was viewed and no screenshot came back")
        saved = root / ".agent" / "web"
        for shot in saved.glob("page-*.png"):
            shot.unlink(missing_ok=True)
        return f"a public page was rendered into {len(images[0].data or b'')} bytes of PNG"

    async def search() -> str:
        """The only probe that spends someone else's allowance, so it says so."""

        text = await call_async("search_web", query="Firecrawl", count=1)
        if "http" not in text:
            raise RuntimeError("the search provider answered with no result")
        return "the search provider answered with at least one result"

    available = set(tools.names)
    probes: list[Probe] = []
    if {"write_file", "read_file", "list_files"} <= available:
        probes.append(Probe("filesystem", "free", files))
    if "use_page" in available:
        probes.append(Probe("browser.page", "free", browser))
    if "read_document" in available:
        probes.append(Probe("documents.read", "free", documents))
    if "view_pages" in available:
        probes.append(Probe("documents.view", "free", pages))
    if "send_file" in available:
        probes.append(Probe("presentation.file", "free", presentation))
    if "fetch_page" in available:
        probes.append(Probe("web.fetch", "free", fetch))
    if "view_web_page" in available:
        probes.append(Probe("web.view", "free", view))
    if "search_web" in available:
        probes.append(Probe("web.search", "credit", search))
    return probes


def backend_probe(backend) -> Probe:
    """The only probe that costs money, so it is the only one named separately."""

    async def answer() -> str:
        from app.models import ContentPart, Message

        completion = await backend.invoke(
            [
                Message(
                    role="user",
                    content=[ContentPart(kind="text", text="Reply with the word ok.")],
                )
            ]
        )
        if not completion.text.strip():
            raise RuntimeError("the model returned an empty answer")
        return f"the model answered ({completion.text.strip()[:40]})"

    return Probe("model", "gpu", answer)
