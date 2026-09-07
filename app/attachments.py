"""UI-independent admission policy for user-supplied attachments.

An interface may reject a file earlier for a nicer experience, but this module
is the authoritative boundary.  A future UI or API gets the same limits by
calling :func:`load_attachments` before starting an agent turn.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.models import ContentPart

MAX_FILES = 5
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 50 * 1024 * 1024

MEDIA_KINDS = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/mpeg": "audio",
    "audio/flac": "audio",
    "audio/ogg": "audio",
}
ACCEPTED_MEDIA_TYPES = tuple(MEDIA_KINDS)

# Where a sent file lands, relative to the workspace. Never the root: the
# root is the person's project.
INBOX = "inbox"


class AttachmentError(ValueError):
    """An incoming attachment cannot safely become model input."""


@dataclass(frozen=True)
class AttachmentSource:
    path: Path
    media_type: str | None
    name: str


@dataclass(frozen=True)
class AttachmentBytes:
    """An upload an interface already holds in memory rather than on disk."""

    name: str
    media_type: str | None
    data: bytes


def _check_size(name: str, size: int) -> int:
    if size == 0:
        raise AttachmentError(f"{name}: empty files are not supported")
    if size > MAX_FILE_SIZE_BYTES:
        raise AttachmentError(f"{name}: file exceeds the 20 MB limit")
    return size


def _check_kind(name: str, media_type: str | None) -> str:
    if media_type not in MEDIA_KINDS:
        raise AttachmentError(f"{name}: unsupported file type ({media_type or 'unknown type'})")
    return MEDIA_KINDS[media_type]


def _check_count(count: int) -> None:
    if count > MAX_FILES:
        raise AttachmentError(f"at most {MAX_FILES} files can be sent in one message")


def _check_total(sizes: Sequence[int]) -> None:
    if sum(sizes) > MAX_TOTAL_SIZE_BYTES:
        raise AttachmentError("attachments exceed the 50 MB total limit")


def _size(source: AttachmentSource) -> int:
    try:
        if not source.path.is_file():
            raise AttachmentError(f"{source.name}: uploaded file is unavailable")
        size = source.path.stat().st_size
    except OSError as exc:
        raise AttachmentError(f"{source.name}: uploaded file is unavailable") from exc
    return _check_size(source.name, size)


def load_attachment_bytes(uploads: Sequence[AttachmentBytes]) -> tuple[ContentPart, ...]:
    """Admit uploads an interface received as bytes, under the same limits.

    Telegram hands over file contents rather than paths. Writing them to disk
    only to read them back would put a second, quietly different admission
    policy in the adapter, so both paths meet here instead.
    """

    _check_count(len(uploads))
    kinds = [_check_kind(upload.name, upload.media_type) for upload in uploads]
    sizes = [_check_size(upload.name, len(upload.data)) for upload in uploads]
    _check_total(sizes)
    return tuple(
        ContentPart(kind=kind, data=upload.data, media_type=upload.media_type)
        for kind, upload in zip(kinds, uploads, strict=True)
    )


def safe_filename(name: str) -> str:
    """A saved name that cannot climb out of the directory it is saved in.

    The name comes from whoever sent the file, so it is treated as hostile:
    directory separators, `..` and control characters are stripped rather than
    substituted, and anything left unusable becomes a neutral name. Substituting
    would be enough to stop traversal and not enough to stop two different files
    from landing on one path.
    """

    cleaned = "".join(
        character
        for character in name.replace("\\", "/").rsplit("/", 1)[-1]
        if character.isprintable() and character not in '<>:"|?*'
    ).strip(" .")
    return cleaned[:120] or "document"


def _free_path(directory: Path, name: str) -> Path:
    """A path that does not overwrite what is already there.

    Two files called `report.pdf` are two documents, and an assistant that
    silently replaced the first one would answer the second question against a
    file the person thinks is still there.
    """

    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, _, suffix = name.rpartition(".")
    stem, suffix = (stem, f".{suffix}") if stem else (name, "")
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise AttachmentError(f"{name}: too many files by that name are already saved")


def admit_uploads(
    uploads: Sequence[AttachmentBytes], workspace: Path
) -> tuple[ContentPart, ...]:
    """Admit one message's uploads: media becomes input, anything else a file.

    A picture is shown to the model directly because that is what a model does
    with a picture. Everything else is saved under `inbox/` in the person's
    workspace and named in the turn: a document, a config, a script, an
    archive. A long document would spend the whole context before the model
    had decided which part mattered, and the rest are things the model works
    on with its file and shell tools rather than reads. The folder is its own
    so a sent file never lands on top of the person's project; moving it into
    place is the model's decision.

    Until 2026-09-07 a file that was neither media nor one of five document
    formats refused the whole message, `sedan_solid.json` among them.
    """

    _check_count(len(uploads))
    sizes = [_check_size(upload.name, len(upload.data)) for upload in uploads]
    _check_total(sizes)

    parts: list[ContentPart] = []
    saved: list[str] = []
    for upload in uploads:
        kind = MEDIA_KINDS.get(upload.media_type or "")
        if kind is not None:
            parts.append(
                ContentPart(kind=kind, data=upload.data, media_type=upload.media_type)
            )
            continue
        inbox = workspace / INBOX
        inbox.mkdir(parents=True, exist_ok=True)
        target = _free_path(inbox, safe_filename(upload.name))
        target.write_bytes(upload.data)
        saved.append(f"{INBOX}/{target.name}")
    if saved:
        listed = ", ".join(saved)
        parts.append(
            ContentPart(
                kind="text",
                text=(
                    f"[The person attached {listed}. Saved in your workspace under "
                    "exactly those paths. Before answering anything about a file, "
                    "read it: read_document for a document, read_file or a "
                    "command for anything else. Unpack an archive with a "
                    "command. Move a file out of that folder when the work "
                    "needs it elsewhere.]"
                ),
            )
        )
    return tuple(parts)


def load_attachments(sources: Sequence[AttachmentSource]) -> tuple[ContentPart, ...]:
    """Validate a complete upload batch, then read it into domain messages.

    The batch is all-or-nothing: one bad file refuses the whole user message so
    the model cannot answer as though it had seen an attachment that was lost.
    """

    _check_count(len(sources))
    sizes: list[int] = []
    for source in sources:
        _check_kind(source.name, source.media_type)
        sizes.append(_size(source))
    _check_total(sizes)

    parts = []
    for source in sources:
        try:
            data = source.path.read_bytes()
        except OSError as exc:
            raise AttachmentError(f"{source.name}: uploaded file is unavailable") from exc
        parts.append(
            ContentPart(
                kind=MEDIA_KINDS[source.media_type],
                data=data,
                media_type=source.media_type,
            )
        )
    return tuple(parts)
