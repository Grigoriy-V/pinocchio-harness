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


def test_a_chosen_set_is_read_under_its_own_prefix(monkeypatch) -> None:
    """`MODEL=comet`: the COMET lines, the plain ones untouched, defaults for the rest."""

    monkeypatch.setenv("MODEL", "comet")
    monkeypatch.setenv("MODEL_ENDPOINT", "http://plain/v1")
    monkeypatch.setenv("MODEL_API_KEY", "wk-plain.ws-plain")
    monkeypatch.setenv("MODEL_AUTH_STYLE", "modal_proxy")
    monkeypatch.setenv("MODEL_COMET_ENDPOINT", "https://api.cometapi.com/v1")
    monkeypatch.setenv("MODEL_COMET_NAME", "glm-5.3-flash")
    monkeypatch.setenv("MODEL_COMET_API_KEY", "sk-comet")
    monkeypatch.setenv("MODEL_COMET_EXTRA_BODY", '{"tool_stream": true}')

    chosen = ModelSettings(_env_file=None)

    assert chosen.endpoint == "https://api.cometapi.com/v1"
    assert chosen.name == "glm-5.3-flash"
    assert chosen.api_key == "sk-comet"
    assert chosen.auth_style == "bearer"
    assert chosen.extra_body == {"tool_stream": True}
    assert chosen.chat_template_kwargs is None

    monkeypatch.setenv("MODEL", "")
    assert ModelSettings(_env_file=None).endpoint == "http://plain/v1"


def test_a_chosen_set_brings_its_own_context_budget(monkeypatch) -> None:
    from app.config import AgentSettings

    monkeypatch.setenv("MODEL", "comet")
    monkeypatch.setenv("AGENT_CONTEXT_TOKENS", "32768")
    monkeypatch.setenv("AGENT_COMET_CONTEXT_TOKENS", "131072")
    assert AgentSettings(_env_file=None).context_tokens == 131072

    monkeypatch.delenv("AGENT_COMET_CONTEXT_TOKENS")
    assert AgentSettings(_env_file=None).context_tokens == 32768

    monkeypatch.setenv("MODEL", "")
    monkeypatch.setenv("AGENT_COMET_CONTEXT_TOKENS", "131072")
    assert AgentSettings(_env_file=None).context_tokens == 32768


def test_unset_sends_nothing(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_CHAT_TEMPLATE_KWARGS", raising=False)
    assert ModelSettings(_env_file=None).chat_template_kwargs is None
