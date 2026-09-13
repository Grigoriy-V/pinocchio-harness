"""The status a person can look at any time: the conversation's context
against its budget, the switches, the folder, what the session has cost and
what the account has left.

One assembly for every interface that shows it. Nothing here wakes the
model: the context comes from `Agent.context_report`, the money from the
provider's own account endpoint, which is free to ask.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.agent.mode import current_mode
from app.agent.todo import planning_enabled
from app.config import ModelSettings, chosen_model
from app.context.choice import context_choice

if TYPE_CHECKING:
    from app.agent.runtime import Agent

log = logging.getLogger(__name__)

OPENROUTER = "openrouter.ai"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
CREDITS_TTL = 30.0


@dataclass(frozen=True)
class Credits:
    """What the provider says the account has bought and spent, in dollars."""

    total: float
    used: float

    @property
    def left(self) -> float:
        return self.total - self.used


class CreditsWatch:
    """The account's credits, asked at most every `CREDITS_TTL` seconds, and
    the spend since this process started: the provider counts the account,
    so a session's cost is the difference from its first reading."""

    def __init__(self, settings: ModelSettings | None = None) -> None:
        self.settings = settings or ModelSettings()
        self._first: Credits | None = None
        self._last: Credits | None = None
        self._asked = 0.0

    @property
    def available(self) -> bool:
        return OPENROUTER in self.settings.endpoint and bool(self.settings.api_key)

    async def read(self) -> Credits | None:
        if not self.available:
            return None
        if self._last is not None and time.monotonic() - self._asked < CREDITS_TTL:
            return self._last
        self._asked = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    CREDITS_URL, headers={"Authorization": f"Bearer {self.settings.api_key}"}
                )
                response.raise_for_status()
                data = response.json()["data"]
            credits = Credits(float(data["total_credits"]), float(data["total_usage"]))
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            log.warning("the account's credits could not be read", exc_info=True)
            return self._last
        if self._first is None:
            self._first = credits
        self._last = credits
        return credits

    def session_spend(self) -> float | None:
        if self._first is None or self._last is None:
            return None
        return max(0.0, self._last.used - self._first.used)


@dataclass(frozen=True)
class Status:
    thread_id: str
    model_set: str
    model_name: str
    # The last request's own count, or the next one's estimate before any.
    context_used: int
    context_estimated: bool
    # The model's window: read from the model once it has answered,
    # the configured `context_tokens` before that.
    context_window: int | None
    context_budget: int
    context_size: str
    messages: int
    summarized_through: int
    last_cached: int | None
    mode: str
    plan: bool
    workspace: str
    # This agent's calls' own cost when the provider says it, else the
    # account's usage since the process started.
    session_spend: float | None
    session_spend_exact: bool
    credits_total: float | None
    credits_used: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def status_of(agent: Agent, thread_id: str, credits: Credits | None, spend: float | None) -> Status:
    """Assembled from the store and the last request's own count."""

    report = agent.context_report(thread_id)
    model = ModelSettings()
    workspace = agent.workspace
    return Status(
        thread_id=thread_id,
        model_set=chosen_model() or "plain",
        model_name=model.name or model.endpoint,
        context_used=report.last_used if report.last_used is not None else sum(report.layers.values()),
        context_estimated=report.last_used is None,
        context_window=report.ceiling,
        context_budget=report.budget,
        context_size=report.size,
        messages=report.messages,
        summarized_through=report.summarized_through,
        last_cached=report.last_cached,
        mode=current_mode(workspace),
        plan=planning_enabled(workspace),
        workspace=str(agent.folder(thread_id)),
        session_spend=agent.spent if agent.spent is not None else spend,
        session_spend_exact=agent.spent is not None,
        credits_total=credits.total if credits else None,
        credits_used=credits.used if credits else None,
    )
