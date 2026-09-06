"""How large a request is thought to be, and what that number is allowed to do.

The estimate exists so a conversation can be folded *before* an oversized
request is sent rather than after one is refused. It is an estimate on purpose —
the exact answer needs the model's own tokenizer, which is a dependency and a
cold start on every worker — so what these assert is that it is wrong in the
safe direction, that it learns from what the endpoint reports, and that nothing
it learns can widen it far enough to stop folding.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.agent.runtime import Agent
from app.config import ModelSettings
from app.memory import SqliteStore
from app.models import ContentPart, Message, ToolCall, Usage
from app.models.base import CHARS_PER_TOKEN, MEDIA_TOKENS, measure_request
from app.models.openai_compatible import (
    CALIBRATION_MIN_CHARS,
    CHARS_PER_TOKEN_CEILING,
    OpenAICompatibleBackend,
)
from tests.fakes import ScriptedBackend, says


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "memory.sqlite3"


def text(body: str, role: str = "user") -> Message:
    return Message(role=role, content=[ContentPart(kind="text", text=body)])


def picture() -> Message:
    return Message(
        role="user",
        content=[ContentPart(kind="image", data=b"\x89PNG", media_type="image/png")],
    )


def test_text_is_measured_in_characters_and_media_in_tokens() -> None:
    chars, media = measure_request([text("hello"), picture()])

    assert chars == len("hello")
    assert media == MEDIA_TOKENS["image"]


def test_a_tool_call_counts_as_part_of_the_request() -> None:
    """A model writing a file carries the file in its arguments.

    Measuring only the prose would miss the largest thing in the request, which
    is precisely the request most likely to be the one that does not fit.
    """

    contents = "x" * 4_000
    call = Message(
        role="assistant",
        tool_calls=[ToolCall(id="1", name="write_file", arguments={"text": contents})],
    )

    chars, _ = measure_request([call])

    assert chars > len(contents)


def test_an_empty_request_costs_nothing() -> None:
    assert measure_request([]) == (0, 0)


def test_the_estimate_grows_with_the_conversation() -> None:
    backend = ScriptedBackend()
    short = backend.estimate_tokens([text("hello")])
    long = backend.estimate_tokens([text("hello" * 500)])

    assert 0 < short < long


def test_a_picture_is_worth_more_than_the_sentence_beside_it() -> None:
    """The failure this prevents is a request that looks small and is not."""

    backend = ScriptedBackend()

    assert backend.estimate_tokens([picture()]) > backend.estimate_tokens([text("a photo")])


def backend_at(ratio_source: str) -> OpenAICompatibleBackend:
    """A backend whose transport is never used; only the estimate is under test."""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    backend = OpenAICompatibleBackend(ModelSettings(endpoint="http://x/v1"), transport=transport)
    backend._chars_per_token = float(ratio_source)  # noqa: SLF001 - the subject
    return backend


def test_calibration_moves_the_ratio_towards_what_was_reported() -> None:
    backend = backend_at(CHARS_PER_TOKEN)
    body = text("a" * 3_000)

    backend._calibrate([body], Usage(input_tokens=500))  # noqa: SLF001

    # 3000 chars reported as 500 tokens is 6.0 per token; the ratio moves that
    # way without jumping to it, because one request is not the endpoint.
    assert CHARS_PER_TOKEN < backend._chars_per_token < 6.0  # noqa: SLF001


def test_a_request_carrying_media_does_not_calibrate_anything() -> None:
    """The reported total includes the image, which this ratio must not absorb."""

    backend = backend_at(CHARS_PER_TOKEN)

    backend._calibrate([text("a" * 3_000), picture()], Usage(input_tokens=500))  # noqa: SLF001

    assert backend._chars_per_token == CHARS_PER_TOKEN  # noqa: SLF001


def test_a_short_request_does_not_calibrate_anything() -> None:
    """Short requests are mostly template overhead and say nothing about text."""

    backend = backend_at(CHARS_PER_TOKEN)

    backend._calibrate([text("a" * (CALIBRATION_MIN_CHARS - 1))], Usage(input_tokens=2))  # noqa: SLF001

    assert backend._chars_per_token == CHARS_PER_TOKEN  # noqa: SLF001


def test_a_request_with_no_reported_usage_does_not_calibrate_anything() -> None:
    backend = backend_at(CHARS_PER_TOKEN)

    backend._calibrate([text("a" * 3_000)], Usage())  # noqa: SLF001

    assert backend._chars_per_token == CHARS_PER_TOKEN  # noqa: SLF001


def test_a_nonsense_report_cannot_widen_the_ratio_out_of_range() -> None:
    """The dangerous direction: a ratio too high estimates every request as small.

    A conversation would then never look over budget and nothing would ever be
    folded, which is a failure that shows up as a refused request much later.
    """

    backend = backend_at(CHARS_PER_TOKEN)
    for _ in range(50):
        backend._calibrate([text("a" * 100_000)], Usage(input_tokens=1))  # noqa: SLF001

    assert backend._chars_per_token <= CHARS_PER_TOKEN_CEILING  # noqa: SLF001


async def test_the_budget_is_a_share_of_what_the_server_reports(
    database: Path, workspace: Path
) -> None:
    agent = Agent(
        ScriptedBackend(limit=65_536),
        SqliteStore(database),
        workspace,
        context_fraction=0.6,
    )

    assert await agent.budget() == 39_321


async def test_a_chosen_budget_wins_over_the_share(
    database: Path, workspace: Path
) -> None:
    """Per-person by construction: one agent belongs to one user."""

    agent = Agent(
        ScriptedBackend(limit=65_536),
        SqliteStore(database),
        workspace,
        context_fraction=0.6,
        context_tokens=16_000,
    )

    assert await agent.budget() == 16_000


async def test_a_chosen_budget_cannot_exceed_what_the_server_serves(
    database: Path, workspace: Path
) -> None:
    """Why the ceiling is asked of the server instead of being configured.

    A setting left behind by a smaller deployment, or a choice made when the
    endpoint was larger, must not become a request the endpoint refuses.
    """

    agent = Agent(
        ScriptedBackend(limit=16_384),
        SqliteStore(database),
        workspace,
        context_tokens=131_072,
    )

    assert await agent.budget() == 16_384


async def test_a_silent_server_leaves_the_request_unbounded_here(
    database: Path, workspace: Path
) -> None:
    """Nothing to take a share of and nothing to clamp against.

    The overflow path is what bounds a request in that case, which is the
    arrangement that existed before any of this and still has to hold.
    """

    agent = Agent(
        ScriptedBackend(limit=None),
        SqliteStore(database),
        workspace,
    )

    assert await agent.budget() is None


async def test_a_chosen_budget_stands_when_the_server_says_nothing(
    database: Path, workspace: Path
) -> None:
    """A hosted service reports no length; the person's number is the only one.

    Without it a paid model with a million-token window would never fold and
    every call would carry the whole thread.
    """

    agent = Agent(
        ScriptedBackend(limit=None),
        SqliteStore(database),
        workspace,
        context_tokens=16_000,
    )

    assert await agent.budget() == 16_000


async def test_the_answer_is_unchanged_when_nothing_is_over_budget(
    database: Path, workspace: Path
) -> None:
    """The check costs one estimate and must not otherwise alter a normal turn."""

    backend = ScriptedBackend(says("an ordinary answer"), limit=65_536)
    agent = Agent(backend, SqliteStore(database), workspace, context_fraction=0.6)

    produced = await agent.answer("t1", Message(role="user", content=[ContentPart(kind="text", text="hello")]))

    assert len(backend.requests) == 1
    assert produced[-1].content[0].text == "an ordinary answer"


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_every_media_kind_the_model_accepts_has_a_price(kind: str) -> None:
    """An unpriced modality would make a request carrying it look free."""

    assert MEDIA_TOKENS.get(kind, 0) > 0
