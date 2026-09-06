"""`MODEL_CHAT_TEMPLATE_KWARGS`: JSON in, a dict on the request; blank is nothing."""

from __future__ import annotations

from app.config import ModelSettings


def test_json_from_the_environment_becomes_a_dict(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": true, "reasoning_effort": "low"}')
    assert ModelSettings(_env_file=None).chat_template_kwargs == {
        "enable_thinking": True,
        "reasoning_effort": "low",
    }


def test_a_blank_line_sends_nothing(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CHAT_TEMPLATE_KWARGS", "")
    assert ModelSettings(_env_file=None).chat_template_kwargs is None


def test_extra_body_is_json_and_blank_is_none(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_EXTRA_BODY", '{"tool_stream": true}')
    assert ModelSettings(_env_file=None).extra_body == {"tool_stream": True}
    monkeypatch.setenv("MODEL_EXTRA_BODY", " ")
    assert ModelSettings(_env_file=None).extra_body is None


def test_unset_sends_nothing(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_CHAT_TEMPLATE_KWARGS", raising=False)
    assert ModelSettings(_env_file=None).chat_template_kwargs is None
