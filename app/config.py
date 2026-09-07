"""Settings: `config.toml` for what may be edited, the environment for secrets.

Swapping the model or the endpoint is a configuration change, never a code
change; this is the only place that reads the environment or the file.

Two sources, one order. `config.toml` at the repository root (copied into the
deployed image beside the source) holds every setting that is not a secret:
model sets, the agent's policy, web limits. `.env` and the platform secret hold
credentials. A value found in the environment (or `.env`) wins over the file,
so an old `.env` keeps working and a deployment can still override one line;
the file wins over the defaults below. `CONFIG_FILE` names another file, and
an empty `CONFIG_FILE` reads none (the test suite's setting).
"""

from __future__ import annotations

import os
import tomllib
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

CONFIG_FILE_VAR = "CONFIG_FILE"
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[1] / "config.toml"

# A sentinel for "the caller said nothing", as distinct from `None`, which
# means "read no file" the way `_env_file=None` means "read no .env".
_UNSET: Any = object()


def config_path(chosen: Any = _UNSET) -> Path | None:
    """Which file to read: the caller's, else `CONFIG_FILE`, else the root's."""

    if chosen is not _UNSET:
        return Path(chosen) if chosen else None
    named = os.environ.get(CONFIG_FILE_VAR)
    if named is None:
        return DEFAULT_CONFIG_FILE
    return Path(named) if named.strip() else None


def load_config(chosen: Any = _UNSET) -> dict[str, Any]:
    """The whole file as a dict; an absent or unnamed file is an empty one."""

    path = config_path(chosen)
    if path is None or not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


# The section a settings class is being built from, handed to the source
# below without threading it through pydantic's own constructor.
_SECTION: ContextVar[dict[str, Any] | None] = ContextVar("config_section", default=None)


class _FileSection(PydanticBaseSettingsSource):
    """One `config.toml` section as a settings source, below the environment."""

    def __init__(self, settings_cls: type[BaseSettings], values: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self.values = values

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self.values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if k in self.settings_cls.model_fields}


def _passthrough(values: dict[str, Any]) -> dict[str, Any]:
    """The source arguments a nested settings object must be built with too."""

    return {k: v for k, v in values.items() if k in ("_env_file", "_config_file")}


class Configured(BaseSettings):
    """A settings class that also reads its section of `config.toml`.

    Order: an explicit argument, the environment, `.env`, the file section,
    the defaults. `section` says which part of the file is this class's.
    """

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        return {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _FileSection(settings_cls, _SECTION.get() or {}),
            file_secret_settings,
        )

    def __init__(self, **values: Any) -> None:
        config = load_config(values.get("_config_file", _UNSET))
        token = _SECTION.set(self.section(config, values))
        try:
            super().__init__(**{k: v for k, v in values.items() if k != "_config_file"})
        finally:
            _SECTION.reset(token)


def _sets(config: dict[str, Any]) -> dict[str, Any]:
    return dict((config.get("model") or {}).get("sets") or {})


def _unnamed_set(config: dict[str, Any]) -> dict[str, Any]:
    model = dict(config.get("model") or {})
    model.pop("chosen", None)
    model.pop("sets", None)
    return model


class ModelChoice(Configured):
    """`MODEL=comet` names which set of model lines the assistant reads.

    A set is every `MODEL_<NAME>_*` line, and `AGENT_<NAME>_CONTEXT_TOKENS`
    for its budget; the plain `MODEL_*` lines are the unnamed set, read when
    nothing is chosen. Sets live side by side so that switching the model is
    one line and no key overwrites another (the human, 2026-09-06).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model: str | None = None

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        chosen = (config.get("model") or {}).get("chosen")
        return {"model": chosen} if chosen else {}

    @property
    def name(self) -> str | None:
        chosen = (self.model or "").strip().upper()
        return chosen or None


def chosen_model(**init: Any) -> str | None:
    """The chosen set's name in upper case, or None for the unnamed set."""

    return ModelChoice(**_passthrough(init)).name


class _SharedKey(BaseSettings):
    """The plain `MODEL_API_KEY`, read for a named set that has none of its own."""

    model_config = SettingsConfigDict(env_prefix="MODEL_", env_file=".env", extra="ignore")

    api_key: str | None = None


class ModelSettings(Configured):
    """How to reach the OpenAI-compatible endpoint that serves the model.

    Read from `MODEL_<NAME>_*` when `MODEL=<name>` is set, else from `MODEL_*`.
    """

    model_config = SettingsConfigDict(env_prefix="MODEL_", env_file=".env", extra="ignore")

    def __init__(self, **values: Any) -> None:
        named = "_env_prefix" not in values and chosen_model(**values)
        if named:
            values["_env_prefix"] = f"MODEL_{named}_"
        super().__init__(**values)
        if named and self.api_key is None:
            # A set without a key of its own uses the plain `MODEL_API_KEY`:
            # the Modal Apps share one proxy token, and one line should cover
            # them (the human, 2026-09-07).
            shared = _SharedKey(**_passthrough(values))
            if shared.api_key is not None:
                self.api_key = shared.api_key

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        name = chosen_model(**values)
        if name:
            return dict(_sets(config).get(name.lower()) or {})
        return _unnamed_set(config)

    endpoint: str = "http://127.0.0.1:8000/v1"
    name: str = "gemma-4-12b-it"
    api_key: str | None = None
    # Ordinary OpenAI-compatible services use bearer auth. Modal web endpoints
    # can instead require the proxy token as two headers; keeping this explicit
    # avoids guessing from a URL or from the shape of a secret.
    auth_style: Literal["bearer", "modal_proxy"] = "bearer"
    # How long a request may wait for its first byte, and a stream between
    # bytes. 600 since 2026-09-05, from 120: a deployed endpoint that has to
    # create a snapshot on a worker type that has none takes six to seven
    # minutes before it answers, the request waits queued at the edge the whole
    # time, and a client that gives up sooner and asks again only queues a
    # second copy (ISS-0044). An endpoint that fails answers with an error
    # well inside this; a local server never sleeps.
    timeout: float = 600.0
    # An output cap, not reserved output and not the server's context length
    # (64k since 2026-08-30). 8192 since 2026-09-04: a single file of about
    # 15k characters is 5k tokens, and 4096 cut it mid-call (ISS-0031); output
    # is cheap next to prefill, and the loop's budget bounds the rest.
    max_tokens: int = 8192
    temperature: float = 0.0
    # Extra attempts after the first, for failures that say "later", not "no".
    retries: int = 2
    retry_backoff: float = 0.5
    # What the server's chat template is told on every request, as JSON: for
    # Qwen3.8, `{"enable_thinking": true, "reasoning_effort": "low"}` turns
    # reasoning on at an effort, over the server's own default. A setting,
    # because it is the one model-side dial a person may want to move without
    # booting the model App; empty sends nothing and the server's default holds.
    chat_template_kwargs: dict[str, object] | None = None
    # Fields merged into the body of every request, last, as JSON: the place
    # for what one hosted service wants and the OpenAI shape has no word for
    # (`{"tool_stream": true}` for GLM through CometAPI, `{"thinking":
    # {"type": "disabled"}}` for DeepSeek). Merged last so it can also override
    # a field the client sets; empty sends nothing.
    extra_body: dict[str, object] | None = None
    # A directory where every streamed response is kept as it arrived, one
    # `.sse` file per call with the request body first. Empty keeps nothing.
    # The one way to know what a model wrote when the parser kept none of it
    # (2026-09-06: 8,192 tokens, no text, no call); it holds conversation
    # content, so it is a workspace or a scratch path, never the repository.
    dump_dir: str | None = None
    # Which hosts may serve the model at a router such as OpenRouter, as the
    # router's own slugs (`novita/fp8`). The first is the one asked; the rest
    # are asked only after the first has failed the client's own retries,
    # never because it was slow: a change of host loses the prefix cache, and
    # 11 of 89 deployed calls of 2026-09-06 paid that for no visible reason.
    # Empty leaves routing to the service.
    providers: list[str] | None = None

    @field_validator("providers", mode="before")
    @classmethod
    def _providers_list(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace(";", ",").split(",")]
            return [part for part in parts if part] or None
        return value or None

    @field_validator("chat_template_kwargs", "extra_body", mode="before")
    @classmethod
    def _empty_means_none(cls, value: object) -> object:
        # An `.env` line left blank is "send nothing", not a parse error.
        if isinstance(value, str) and not value.strip():
            return None
        return value


class TelegramSettings(Configured):
    """How to reach Telegram, and who is allowed to reach the assistant."""

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_", env_file=".env", extra="ignore"
    )

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        return dict(config.get("telegram") or {})

    token: str = ""
    # Telegram includes this exact value in every webhook request. It is not
    # the bot token and must be configured independently.
    webhook_secret: str = ""
    api_base: str = "https://api.telegram.org"
    # Comma-separated numeric Telegram user ids. Empty means nobody, because the
    # safe answer has to be the default: an assistant reachable by whoever finds
    # the bot spends the owner's GPU and reads the owner's memory.
    allowed_users: str = ""
    # Admit every Telegram account instead of consulting the list above. Each
    # account still gets its own conversations, memory and workspace, but they
    # share one GPU, so this is a deliberate choice and never a default.
    open_access: bool = False
    # Long-poll duration asked of Telegram. The HTTP timeout must exceed it.
    poll_timeout: int = 25
    timeout: float = 60.0

    @property
    def allowed(self) -> frozenset[int]:
        found = set()
        for part in self.allowed_users.replace(";", ",").split(","):
            part = part.strip()
            if part:
                found.add(int(part))
        return frozenset(found)


class WebSettings(Configured):
    """How the assistant reaches the public web, and how far it may go.

    Three capabilities, configured separately because they cost differently.
    Search asks a provider and spends its credit. Fetching spends nothing and
    runs wherever the agent runs. Viewing runs a browser over someone else's
    JavaScript, which is why it can be pointed at a renderer that holds none of
    this deployment's secrets — and why an unset renderer URL means the browser
    runs here, which is the right answer on a personal machine and the wrong one
    in a container full of credentials.
    """

    model_config = SettingsConfigDict(env_prefix="WEB_", env_file=".env", extra="ignore")

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        return dict(config.get("web") or {})

    # Search. Empty means the assistant has no search tool at all rather than a
    # tool that fails when it is used.
    firecrawl_api_key: str = ""
    firecrawl_endpoint: str = "https://api.firecrawl.dev/v1"
    search_results: int = 5
    search_timeout: float = 30.0

    # Direct fetch. The byte cap is what a page is allowed to spend of the
    # context it is about to be pasted into, not a network limit.
    #
    # Two identities, because sites disagree about what an honest client looks
    # like. Most answer an unknown client with a challenge page, so the default
    # is browser-shaped. A few — Wikimedia measurably — refuse browser strings
    # from anything that is not a browser and ask for a client that names itself
    # and a way to be contacted. That second identity is empty by default,
    # because inventing a contact address for someone would be worse than the
    # refusal: set it to `name/version (contact)` and a page that asks for one
    # becomes readable. Measured: Wikipedia 403 with the browser string, 200
    # with a contactable identity, unchanged on five other sites.
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    fallback_user_agent: str = ""
    # Per-wait and overall. The first is what httpx bounds — connect, read one
    # chunk — and a server that sends one byte per second satisfies it forever,
    # so the second bounds the whole call including its redirects.
    fetch_timeout: float = 20.0
    fetch_total_timeout: float = 45.0
    max_redirects: int = 3
    max_bytes: int = 1_000_000

    # Viewing. `renderer_url` is the isolated CPU function; `renderer_key` is its
    # Modal proxy token pair, in the same `<wk-...>.<ws-...>` form as MODEL_API_KEY.
    #
    # `local_browser` is a statement about *this* process, made by whoever built
    # the environment: may a stranger's JavaScript run beside what is in here?
    # True on a personal machine, where the browser, the agent and the person are
    # already one trust boundary. The deployed agent image sets it to 0, so a
    # container holding the bot token and the database URL fails loudly instead
    # of quietly rendering when the renderer URL is missing.
    local_browser: bool = True
    renderer_url: str = ""
    renderer_key: str = ""
    renderer_timeout: float = 120.0
    viewport_width: int = 1200
    viewport_height: int = 900
    render_timeout: float = 30.0
    # A tall page is still one screenshot, and one screenshot is tokens. This is
    # where a full-page capture stops growing.
    max_render_height: int = 4000


class ModelBudget(Configured):
    """The budget that goes with a named model set.

    `AGENT_<NAME>_CONTEXT_TOKENS` in the environment, or `context_tokens` in
    the set's own section of `config.toml`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    context_tokens: int | None = None

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        name = chosen_model(**values)
        if not name:
            return {}
        chosen = _sets(config).get(name.lower()) or {}
        return {"context_tokens": chosen["context_tokens"]} if "context_tokens" in chosen else {}


class AgentSettings(Configured):
    """Where the agent stores memory, what it may read, and how much it keeps."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    @classmethod
    def section(cls, config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        return dict(config.get("agent") or {})

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        # The context budget belongs with the model, because it is the one
        # agent setting that changes with the endpoint (a hosted service
        # reports no length). A named set's budget wins over the plain one.
        name = chosen_model(**values)
        if name and "context_tokens" not in values:
            budget = ModelBudget(_env_prefix=f"AGENT_{name}_", **_passthrough(values))
            if budget.context_tokens is not None:
                self.context_tokens = budget.context_tokens

    database: str = "data/memory.sqlite3"
    # Where the deployed profile keeps conversations instead. Empty means the
    # SQLite file above, which is what the local profile uses and will keep
    # using: a personal machine has one process and a disk under it.
    #
    # `PostgresStore` is provider-agnostic; everything a provider needs lives in
    # this one string. For Neon that means the **pooled** endpoint — a fleet
    # that scales to zero opens and drops connections in bursts, and a direct
    # endpoint runs out of them long before the database runs out of capacity —
    # together with `sslmode=require`. It is a credential and belongs in the
    # environment or a platform secret, never in the repository.
    database_url: str = ""
    # A second database, used only to measure one against the other. It exists
    # because the deployed store's latency turned out to be dominated by the
    # distance between the worker and the database, and that claim is worth a
    # measurement rather than a map. Empty means there is nothing to compare to.
    alt_database_url: str = ""
    # Keeps this application's tables together in a database that may hold
    # other things, and gives a test a namespace of its own.
    database_schema: str = "public"
    # In-flight turns only, in LangGraph's own schema. Kept apart from the
    # database so that discarding it costs no conversation.
    checkpoints: str = "data/checkpoints.sqlite3"
    # The only directory the filesystem tools may reach, created on first use.
    # It defaults to a sandbox rather than to the current directory: the default
    # should be the safe answer, and pointing the agent at real work is then a
    # deliberate act rather than the consequence of where it was started.
    workspace: str = "workspace"
    # How many of the newest exchanges always stay verbatim (`keep_turns`).
    keep_turns: int = 2
    retrieved_facts: int = 5
    # How many of the newest tool results of stored history a request carries
    # in full; older ones are stubs on the surface and whole in history. The
    # turn in progress is never shortened (ISS-0041). Two is the result the
    # model was reading when the previous turn ended and the one before it.
    keep_results: int = 2
    # After how many seconds of work a turn is asked, between two of its
    # steps, whether it is on track; and again after each further interval.
    # No ceiling on steps, calls or seconds: a ceiling ends work that is going
    # well (the human, 2026-09-07). Zero asks never.
    turn_check_seconds: float = 600.0
    # The share of the model's own context a request may occupy. The limit
    # itself is read from the server, never copied here: two copies of one
    # number are one number and one lie waiting to happen. The headroom is what
    # absorbs the difference between an estimated request size and the real one.
    context_fraction: float = 0.8
    # A budget in tokens, when someone has chosen one, instead of the share
    # above. Always clamped to what the server actually accepts, so a choice can
    # only ever ask for less than the model allows and never for more than it
    # can serve. Unset by default, which means the fraction decides.
    #
    # Per-person by construction: an `Agent` belongs to one user, so this is
    # already the place a chosen size would land. Nothing stores or offers that
    # choice yet — the command and the per-user column are 4.6a, where
    # compaction is good enough to make a smaller budget a real trade rather
    # than a way to lose history faster.
    context_tokens: int | None = None
    # Record what each turn cost and where its time went. On by default because
    # a turn nobody measured is a turn nobody can improve, and off is a
    # redeployed setting rather than a reverted release. When it is off the
    # application still runs every code path; it simply records nothing.
    telemetry: bool = True
    # Where the local profile keeps that record. Its own file, like the
    # checkpoints: telemetry is disposable in a way a conversation is not, so
    # deleting it has to cost nothing. The deployed profile uses `database_url`
    # instead, in tables of its own.
    telemetry_database: str = "data/telemetry.sqlite3"
    # Ask the model for its answer as it is written, so an interface can show it
    # growing. Off, the conversational turn is one complete request again. It is
    # a switch rather than a constant because the visible half of a turn is the
    # part a person notices breaking: turning it off is a redeployed setting,
    # not a reverted release.
    stream_answers: bool = True
