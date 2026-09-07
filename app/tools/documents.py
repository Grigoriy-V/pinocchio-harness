"""Reading a saved document, one bounded slice at a time.

The document is already in the workspace — an attachment is saved there rather
than pasted into the turn — so this tool's job is to hand back a part of it with
its labels intact and to stop before it fills the context.

Two decisions are worth stating. The tool never returns a whole large document
in one call, because a tool result goes straight into the next request and a
model cannot decline what it has already been given. And it always says what it
did not show, so the model can ask for the rest instead of answering from a
fragment it thinks is the whole thing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.documents import (
    DOCUMENT_MEDIA_TYPES,
    MAX_PAGES_PER_VIEW,
    PDF,
    DocumentError,
    Section,
    media_type_for,
    page_count,
    read_sections,
    render_pages,
)
from app.models import ContentPart
from app.tools.base import BAD_ARGUMENTS, Tool, ToolError, handover
from app.tools.filesystem import NOT_A_FILE, NOT_FOUND, TOO_LARGE, resolve_in_root

MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 12_000

# The family's codes: a file this does not read, and one it tried to and could not.
UNSUPPORTED = "doc.unsupported"
UNREADABLE = "doc.unreadable"


def _existing_file(root: Path, path: str) -> Path:
    target = resolve_in_root(root, path)
    if not target.exists():
        raise ToolError(f"path {path!r} does not exist", code=NOT_FOUND)
    if not target.is_file():
        raise ToolError(f"path {path!r} is not a file", code=NOT_A_FILE)
    if target.stat().st_size > MAX_BYTES:
        raise ToolError(
            f"{target.name} is larger than the {MAX_BYTES // (1024 * 1024)} MB limit",
            code=TOO_LARGE,
        )
    return target


def _render(name: str, sections: list[Section], start: int, budget: int) -> str:
    """Format from `start` until the budget runs out, and say where it stopped."""

    lines = [f"{name}: {len(sections)} section(s)"]
    used = 0
    shown = 0
    for index in range(start, len(sections)):
        section = sections[index]
        block = f"\n[{index + 1}. {section.label}]\n{section.text}"
        if used + len(block) > budget and shown:
            break
        lines.append(block[: budget - used] if used + len(block) > budget else block)
        used += len(block)
        shown += 1
    last = start + shown
    if last < len(sections):
        lines.append(
            f"\n... stopped after section {last} of {len(sections)}. Call read_document "
            f"again with from_section={last + 1} to continue."
        )
    return "\n".join(lines)


def read_document(root: Path, path: str, from_section: int = 1) -> str:
    target = _existing_file(root, path)
    media_type = media_type_for(target.name, None)
    if media_type is None:
        readable = ", ".join(sorted(DOCUMENT_MEDIA_TYPES.values()))
        raise ToolError(
            f"{target.name} is not a document this reads ({readable})", code=UNSUPPORTED
        )
    try:
        sections = read_sections(target.read_bytes(), media_type)
    except DocumentError as error:
        raise ToolError(str(error), code=UNREADABLE) from error
    if from_section < 1 or from_section > len(sections):
        raise ToolError(
            f"from_section must be between 1 and {len(sections)} for {target.name}",
            code=BAD_ARGUMENTS,
        )
    return _render(target.name, sections, from_section - 1, MAX_CHARS)


def view_pages(root: Path, path: str, page: int = 1, pages: int = 1) -> list[ContentPart]:
    """Hand the model a picture of a page, which is how a scan is read.

    Not OCR. The model is multimodal, so it looks at the page the way a person
    does; nothing here recognises characters and then claims to have read them.
    That keeps the failure honest — an illegible scan looks illegible instead of
    becoming confident nonsense.
    """

    target = _existing_file(root, path)
    if media_type_for(target.name, None) != PDF:
        raise ToolError(
            f"{target.name} is not a PDF, so there are no pages to look at", code=UNSUPPORTED
        )
    wanted = max(1, min(int(pages), MAX_PAGES_PER_VIEW))
    data = target.read_bytes()
    try:
        total = page_count(data)
        rendered = render_pages(data, int(page), wanted)
    except DocumentError as error:
        raise ToolError(str(error), code=UNREADABLE) from error

    numbers = ", ".join(str(number) for number, _ in rendered)
    last = rendered[-1][0]
    preview_root = root / ".agent" / "documents"
    preview_root.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(data).hexdigest()[:12]
    saved = []
    for number, image in rendered:
        preview = preview_root / f"{identity}-page-{number}.png"
        preview.write_bytes(image)
        saved.append(preview.relative_to(root).as_posix())
    note = (
        f"{target.name}: page(s) {numbers} of {total}, rendered as images for your "
        f"inspection. Saved rendered page path(s): {', '.join(saved)}. Nothing was sent "
        f"to the person; {handover(saved[0], 'a page')}"
    )
    if last < total:
        note += f" Call view_pages again with page={last + 1} for what follows."
    parts: list[ContentPart] = [ContentPart(kind="text", text=note)]
    parts.extend(
        ContentPart(kind="image", data=image, media_type="image/png")
        for _, image in rendered
    )
    return parts


def document_tools(root: Path) -> list[Tool]:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"the tool root {root} is not a directory")
    readable = ", ".join(sorted(DOCUMENT_MEDIA_TYPES.values()))
    return [
        Tool(
            name="read_document",
            replay_safe=True,
            description=(
                f"Read a document in your workspace ({readable}) as text. A document the "
                "person sends is saved in your workspace under the name the turn gives "
                "you, not shown to you directly: this is how you read it."
            ),
            returns=(
                "numbered sections with their own labels, page numbers for a PDF, "
                "headings for Markdown and .docx, and where it stopped, so the rest can "
                "be asked for with `from_section`."
            ),
            leaves="nothing.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Path inside the workspace, absolute or relative.",
                    },
                    "from_section": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Which numbered section to start at. Defaults to the first."
                        ),
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            run=lambda path, from_section=1: read_document(resolved, path, from_section),
        ),
        Tool(
            name="view_pages",
            replay_safe=True,
            description=(
                "Look at PDF pages as pictures: scans, layout, tables, diagrams, forms. "
                f"At most {MAX_PAGES_PER_VIEW} page(s) per call; ask again for the next."
            ),
            returns="the rendered pages, shown to you, and their workspace paths.",
            leaves=(
                "one .png per page under .agent/documents/ in your workspace; nothing "
                "reaches the person unless you send_file it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Path to a PDF inside the workspace.",
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "The first page to look at, counting from one.",
                    },
                    "pages": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_PAGES_PER_VIEW,
                        "description": "How many pages from there. Defaults to one.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            run=lambda path, page=1, pages=1: view_pages(resolved, path, page, pages),
        ),
    ]
