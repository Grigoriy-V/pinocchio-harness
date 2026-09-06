"""What leaves this machine is decided by a list, not by a file's contents.

The tool copies configuration from the developer's `.env` into the deployment's
platform secret. The only thing worth testing is the filter: everything else is
a subprocess call.
"""

from __future__ import annotations

from pathlib import Path

from tools.sync_control_secret import ALLOWED, describe, named, plan, read_env


def test_only_named_keys_are_published() -> None:
    """A wholesale copy would ship the test database and local paths."""

    values = {
        "TELEGRAM_TOKEN": "t",
        "AGENT_DATABASE_URL": "d",
        "AGENT_TEST_DATABASE_URL": "the suite drops schemas in this one",
        "AGENT_WORKSPACE": "workspace",
        "OPENAI_API_KEY": "someone else's",
    }

    present, _missing = plan(values)

    assert [target for _source, target in present] == ["TELEGRAM_TOKEN", "AGENT_DATABASE_URL"]


def test_the_renderer_address_is_carried_under_a_name_the_local_profile_ignores() -> None:
    """It decides *where* a page opens, so a copy in `.env` would repoint this machine.

    Locally that would send every `view_web_page` to the deployed renderer —
    starting a container to do what the browser here does for free.
    """

    present, _missing = plan({"DEPLOY_WEB_RENDERER_URL": "https://renderer.example/"})

    assert present == [("DEPLOY_WEB_RENDERER_URL", "WEB_RENDERER_URL")]
    assert describe(*present[0]) == "DEPLOY_WEB_RENDERER_URL -> WEB_RENDERER_URL"
    assert "WEB_RENDERER_URL" not in [named(entry)[0] for entry in ALLOWED]


def test_every_model_set_is_published_and_nothing_else_under_its_name() -> None:
    """`MODEL_COMET_*` and `AGENT_COMET_CONTEXT_TOKENS` go; a stray `MODEL_COMET_NOTE` stays."""

    values = {
        "MODEL": "comet",
        "MODEL_COMET_ENDPOINT": "https://api.cometapi.com/v1",
        "MODEL_COMET_API_KEY": "sk",
        "AGENT_COMET_CONTEXT_TOKENS": "131072",
        "MODEL_INT4_API_KEY": "wk.ws",
        "MODEL_COMET_NOTE": "not a setting",
        "AGENT_COMET_WORKSPACE": "not a setting",
    }

    present, _missing = plan(values)

    assert [target for _source, target in present] == [
        "MODEL",
        "AGENT_COMET_CONTEXT_TOKENS",
        "MODEL_COMET_API_KEY",
        "MODEL_COMET_ENDPOINT",
        "MODEL_INT4_API_KEY",
    ]


def test_an_empty_value_counts_as_absent() -> None:
    """A key present and blank is not configuration; publishing it would hide that."""

    present, missing = plan({"TELEGRAM_TOKEN": "", "MODEL_API_KEY": "k"})

    assert [target for _source, target in present] == ["MODEL_API_KEY"]
    assert "TELEGRAM_TOKEN" in [target for _source, target in missing]


def test_the_web_capability_can_be_configured_at_all() -> None:
    published = {named(entry)[1] for entry in ALLOWED}

    assert {"WEB_FIRECRAWL_API_KEY", "WEB_RENDERER_URL", "WEB_RENDERER_KEY"} <= published
    # Both are facts about the container, set by the image. Publishing either
    # would let a laptop's value repoint the deployed workspace or hand the
    # worker its own browser.
    assert "AGENT_WORKSPACE" not in published
    assert "WEB_LOCAL_BROWSER" not in published


def test_env_parsing_keeps_values_intact(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nTELEGRAM_TOKEN=123:ABC\nMODEL_API_KEY=\"wk-id.ws-secret\"\nBROKEN\n",
        encoding="utf-8",
    )

    values = read_env(env)

    assert values == {"TELEGRAM_TOKEN": "123:ABC", "MODEL_API_KEY": "wk-id.ws-secret"}
