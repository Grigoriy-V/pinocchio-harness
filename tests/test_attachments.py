from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.attachments import (
    ACCEPTED_MEDIA_TYPES,
    MAX_FILES,
    MAX_FILE_SIZE_BYTES,
    MAX_TOTAL_SIZE_BYTES,
    AttachmentError,
    AttachmentSource,
    load_attachments,
)


def source(path: Path, media_type: str, name: str | None = None) -> AttachmentSource:
    return AttachmentSource(path, media_type, name or path.name)


@pytest.mark.parametrize(
    ("media_type", "kind"),
    [
        ("image/jpeg", "image"),
        ("image/png", "image"),
        ("image/webp", "image"),
        ("audio/wav", "audio"),
        ("audio/x-wav", "audio"),
        ("audio/mpeg", "audio"),
        ("audio/flac", "audio"),
        ("audio/ogg", "audio"),
    ],
)
def test_supported_media_becomes_a_domain_part(
    tmp_path: Path, media_type: str, kind: str
) -> None:
    uploaded = tmp_path / "upload"
    uploaded.write_bytes(b"content")

    [part] = load_attachments([source(uploaded, media_type)])

    assert part.kind == kind
    assert part.media_type == media_type
    assert part.data == b"content"


def test_unknown_type_refuses_the_whole_batch(tmp_path: Path) -> None:
    uploaded = tmp_path / "notes.txt"
    uploaded.write_text("hello", encoding="utf-8")

    with pytest.raises(AttachmentError, match=r"notes\.txt: unsupported file type"):
        load_attachments([source(uploaded, "text/plain")])


def test_empty_file_is_refused(tmp_path: Path) -> None:
    uploaded = tmp_path / "empty.png"
    uploaded.touch()

    with pytest.raises(AttachmentError, match="empty files"):
        load_attachments([source(uploaded, "image/png")])


def test_too_many_files_are_refused_before_reading(tmp_path: Path) -> None:
    missing = source(tmp_path / "missing.png", "image/png")

    with pytest.raises(AttachmentError, match=f"at most {MAX_FILES} files"):
        load_attachments([missing] * (MAX_FILES + 1))


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    uploaded = tmp_path / "large.png"
    with uploaded.open("wb") as handle:
        handle.truncate(MAX_FILE_SIZE_BYTES + 1)

    with pytest.raises(AttachmentError, match="20 MB limit"):
        load_attachments([source(uploaded, "image/png")])


def test_oversized_batch_is_refused(tmp_path: Path) -> None:
    uploads = []
    for index in range(3):
        uploaded = tmp_path / f"part-{index}.wav"
        with uploaded.open("wb") as handle:
            handle.truncate(MAX_TOTAL_SIZE_BYTES // 3 + 1)
        uploads.append(source(uploaded, "audio/wav"))

    with pytest.raises(AttachmentError, match="50 MB total limit"):
        load_attachments(uploads)


def test_chainlit_early_limits_match_the_authoritative_policy() -> None:
    config_path = Path(__file__).resolve().parents[1] / ".chainlit" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    upload = config["features"]["spontaneous_file_upload"]

    # Any file: the harness admits every kind since 2026-09-07 (a document to
    # `inbox/`), and since 2026-09-13 Chainlit does too.
    assert upload["accept"] == ["*/*"]
    assert upload["max_files"] == MAX_FILES
    assert upload["max_size_mb"] * 1024 * 1024 == MAX_FILE_SIZE_BYTES
