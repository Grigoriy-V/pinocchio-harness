"""`config.toml` below the environment: settings from the file, secrets from `.env`."""

from __future__ import annotations

from pathlib import Path

from app.config import AgentSettings, ModelSettings, TelegramSettings, WebSettings, chosen_model

FILE = """
[model]
chosen = "or"
endpoint = "http://plain/v1"

[model.sets.or]
endpoint = "https://openrouter.ai/api/v1"
name = "z-ai/glm-5.3-flash"
providers = ["novita/fp8", "z-ai/fp8"]
extra_body = { reasoning_effort = "low" }
context_tokens = 262144

[agent]
keep_turns = 3

[telegram]
poll_timeout = 7

[web]
max_bytes = 4096
"""


def write(tmp_path: Path, text: str = FILE) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_file_names_the_set_and_fills_it(tmp_path: Path) -> None:
    path = write(tmp_path)

    assert chosen_model(_env_file=None, _config_file=path) == "OR"
    chosen = ModelSettings(_env_file=None, _config_file=path)
    assert chosen.endpoint == "https://openrouter.ai/api/v1"
    assert chosen.name == "z-ai/glm-5.3-flash"
    assert chosen.providers == ["novita/fp8", "z-ai/fp8"]
    assert chosen.extra_body == {"reasoning_effort": "low"}
    assert chosen.api_key is None
    assert AgentSettings(_env_file=None, _config_file=path).context_tokens == 262144


def test_the_environment_wins_over_the_file(monkeypatch, tmp_path: Path) -> None:
    """A secret, or one overriding line, still comes from `.env` or the platform."""

    path = write(tmp_path)
    monkeypatch.setenv("MODEL_OR_API_KEY", "sk-or")
    monkeypatch.setenv("MODEL_OR_NAME", "z-ai/glm-5.3-flash:free")
    monkeypatch.setenv("AGENT_OR_CONTEXT_TOKENS", "131072")

    chosen = ModelSettings(_env_file=None, _config_file=path)
    assert chosen.api_key == "sk-or"
    assert chosen.name == "z-ai/glm-5.3-flash:free"
    assert chosen.endpoint == "https://openrouter.ai/api/v1"
    assert AgentSettings(_env_file=None, _config_file=path).context_tokens == 131072

    monkeypatch.setenv("MODEL", "")
    assert ModelSettings(_env_file=None, _config_file=path).endpoint == "http://plain/v1"


def test_every_family_reads_its_section(tmp_path: Path) -> None:
    path = write(tmp_path)

    assert AgentSettings(_env_file=None, _config_file=path).keep_turns == 3
    assert TelegramSettings(_env_file=None, _config_file=path).poll_timeout == 7
    assert WebSettings(_env_file=None, _config_file=path).max_bytes == 4096


def test_no_file_means_the_defaults(tmp_path: Path) -> None:
    assert ModelSettings(_env_file=None, _config_file=None).endpoint == "http://127.0.0.1:8000/v1"
    assert ModelSettings(_env_file=None, _config_file=tmp_path / "absent.toml").providers is None


def test_config_file_variable_names_the_file(monkeypatch, tmp_path: Path) -> None:
    path = write(tmp_path)
    monkeypatch.setenv("CONFIG_FILE", str(path))
    assert AgentSettings(_env_file=None).keep_turns == 3
    monkeypatch.setenv("CONFIG_FILE", "")
    assert AgentSettings(_env_file=None).keep_turns == 2


def test_providers_from_the_environment_are_a_list(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDERS", "novita/fp8, z-ai/fp8")
    assert ModelSettings(_env_file=None, _config_file=None).providers == ["novita/fp8", "z-ai/fp8"]
    monkeypatch.setenv("MODEL_PROVIDERS", "")
    assert ModelSettings(_env_file=None, _config_file=None).providers is None


def test_the_repository_file_parses_and_names_a_set() -> None:
    from app.config import DEFAULT_CONFIG_FILE, load_config

    config = load_config(DEFAULT_CONFIG_FILE)
    assert config["model"]["chosen"] in config["model"]["sets"]
    for name, chosen in config["model"]["sets"].items():
        assert "api_key" not in chosen, f"{name}: a key belongs in .env"
