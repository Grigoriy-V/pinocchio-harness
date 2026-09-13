"""The only part of the UI worth testing offline: the adaptation to `Message`.

Rendering is Chainlit's; turning an attachment into a content part is ours, and
a wrong media type there fails silently against a live model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("chainlit", reason="the ui dependency group is optional")

from app.models import ContentPart, Message
from app.attachments import AttachmentError
from ui.chainlit_app import (
    canonical_thread_id,
    command_of,
    media_parts,
    spoken,
    to_message,
)


class FakeElement:
    def __init__(self, path: str, mime: str | None, name: str | None = None) -> None:
        self.path = path
        self.mime = mime
        self.name = name


class FakeMessage:
    def __init__(
        self,
        content: str = "",
        elements: list[FakeElement] | None = None,
        command: str | None = None,
    ) -> None:
        self.content = content
        self.elements = elements or []
        self.command = command


def test_a_plain_message_becomes_one_text_part(tmp_path: Path) -> None:
    message = to_message(FakeMessage("hello"), tmp_path)

    assert message.role == "user"
    assert [part.kind for part in message.content] == ["text"]


def test_an_image_attachment_keeps_its_bytes_and_media_type(tmp_path: Path) -> None:
    picture = tmp_path / "shot.png"
    picture.write_bytes(b"\x89PNG\x00")

    message = to_message(
        FakeMessage("what is this", [FakeElement(str(picture), "image/png")]), tmp_path
    )

    assert [part.kind for part in message.content] == ["text", "image"]
    assert message.content[1].data == b"\x89PNG\x00"
    assert message.content[1].media_type == "image/png"


def test_an_audio_attachment_is_carried_as_audio(tmp_path: Path) -> None:
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")

    message = to_message(FakeMessage("", [FakeElement(str(clip), "audio/wav")]), tmp_path)

    assert [part.kind for part in message.content] == ["audio"]


def test_a_document_is_saved_to_the_inbox_and_named_in_the_turn(tmp_path: Path) -> None:
    """Any file, the way the harness admits it: not to the model, to `inbox/`."""

    upload = tmp_path / "upload"
    upload.mkdir()
    document = upload / "report.pdf"
    document.write_bytes(b"%PDF")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    message = to_message(
        FakeMessage("read this", [FakeElement(str(document), "application/pdf")]), workspace
    )

    assert (workspace / "inbox" / "report.pdf").read_bytes() == b"%PDF"
    assert [part.kind for part in message.content] == ["text", "text"]
    assert "inbox/report.pdf" in (message.content[1].text or "")


def test_a_blank_message_does_not_become_an_empty_model_turn(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError, match="no text or usable attachments"):
        to_message(FakeMessage(), tmp_path)


# --- commands ----------------------------------------------------------------


def test_a_command_comes_from_the_menu_or_a_slash() -> None:
    assert command_of(FakeMessage("on", command="plan")) == ("/plan", "on")
    assert command_of(FakeMessage("/Mode careful")) == ("/mode", "careful")
    assert command_of(FakeMessage("/status")) == ("/status", "")
    assert command_of(FakeMessage("hello /status")) is None


# --- what comes back out -----------------------------------------------------


def test_media_is_picked_out_to_be_shown_not_described() -> None:
    """Building the Chainlit element is Chainlit's; choosing what to show is ours."""

    message = Message(
        role="user",
        content=[
            ContentPart(kind="text", text="what is this"),
            ContentPart(kind="image", data=b"\x89PNG", media_type="image/png"),
            ContentPart(kind="audio", data=b"RIFF", media_type="audio/wav"),
        ],
    )

    assert [part.media_type for part in media_parts(message)] == ["image/png", "audio/wav"]


def test_a_text_only_message_has_nothing_to_show() -> None:
    message = Message(role="assistant", content=[ContentPart(kind="text", text="hi")])

    assert media_parts(message) == []


def test_only_explicit_tool_media_is_selected_for_outbound_delivery() -> None:
    observed = ContentPart(kind="image", data=b"seen", media_type="image/png")
    selected = ContentPart(
        kind="image",
        data=b"sent",
        media_type="image/png",
        name="chosen.png",
        outbound=True,
    )
    message = Message(role="tool", content=[observed, selected], tool_call_id="call")

    assert media_parts(message, outbound_only=True) == [selected]


def test_spoken_joins_only_the_text_parts() -> None:
    message = Message(
        role="assistant",
        content=[
            ContentPart(kind="text", text="here it is"),
            ContentPart(kind="image", data=b"\x89PNG", media_type="image/png"),
        ],
    )

    assert spoken(message) == "here it is"


def test_canonical_thread_id_does_not_use_ephemeral_session_id() -> None:
    session = type("Session", (), {"id": "socket", "thread_id": "conversation"})()

    assert canonical_thread_id(session) == "conversation"
