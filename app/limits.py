"""Every bound on what the model reads, keeps and produces, in one place.

Two kinds of limit, kept apart the way the references keep them
(`reports/2026-09-16_item31_references.md` §3): what concerns the request as
a whole is a share of the request's budget, derived here from the budget in
tokens and the model's chars-per-token ratio; what concerns one tool's page
(lines a read shows, matches a search lists, characters of a command's
output shown inline) is a setting with the references' default, the same on
every model, and the rest of it is reachable by an offset, a page or a file
whose path the result names. A number here is never a constant chosen once
for a model that is gone (AGENTS.md: a limit is derived, not written).

The runtime builds one `Limits` per agent once the model's window is known
(`Agent.limits`), the context policy carries it into the fold and the
surface, and the capability registry hands it to every tool it builds.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# The budget a request may reach when nothing has said what the model's
# window is: the smallest of the three sizes a person may choose (`/context`).
DEFAULT_BUDGET_TOKENS = 131_072


@dataclass(frozen=True)
class Limits:
    # --- the request's budget ----------------------------------------------
    # Tokens one request may reach, after the size chosen and the model's
    # window (`app/context/choice.budget_of`).
    budget: int = DEFAULT_BUDGET_TOKENS
    # The model's ratio, calibrated by the backend from what it reports.
    chars_per_token: float = 3.0

    # --- shares of the budget (the whole-request limits) ---------------------
    # The largest one tool result the model is shown whole; past it the whole
    # text goes to a file and the result names the path (OpenClaw: a result at
    # most 30% of the window; DeepSeek, Hermes: the spill file). One eighth
    # (the human, 2026-09-16).
    result_share: float = 1 / 8
    # After a fold, the newest text kept verbatim (DeepSeek 0.16, OpenClaw and
    # Codex 20,000 tokens). 0.15 (the human, 2026-09-16); `keep_turns` is the
    # floor beneath it.
    keep_recent_share: float = 0.15
    # A rolling summary: at most this share of the budget, and never more
    # than the ceiling in tokens (Hermes: 5%, at most 12,000).
    summary_share: float = 0.05
    summary_ceiling_tokens: int = 12_000
    # A stored tool result smaller than this share of the budget is never
    # shortened to a stub: the stub would not be shorter.
    stub_share: float = 0.0005
    # The person's standing instructions, sent on every request: this share
    # of the budget in bytes, at most the ceiling (Codex 32 KiB, OpenClaw
    # 20,000 chars a file).
    instruction_share: float = 0.02
    instruction_ceiling_bytes: int = 32_768

    # --- one tool's page (settings; the references' defaults) ---------------
    read_lines: int = 2_000
    line_chars: int = 2_000
    search_page: int = 100
    preview_chars: int = 400
    shell_output_chars: int = 30_000
    web_text_chars: int = 20_000
    snapshot_chars: int = 12_000
    visible_text_chars: int = 8_000
    screenshot_height: int = 6_000
    csv_rows: int = 200
    command_timeout: int = 120
    command_timeout_max: int = 600

    # --- the loop's numbers (roadmap 32) ---------------------------------
    # How many read-only calls of one batch run at once (the references'
    # 8-10); a call that changes something runs alone, in the model's order.
    parallel_calls: int = 10
    # An identical call repeated: from this many earlier identical outcomes
    # the result carries a note saying so (DeepSeek's advisory), and from
    # `repeat_stop_after` the call is not run and the batch ends; one more
    # identical attempt ends the turn's tools (OpenClaw's critical block).
    repeat_note_after: int = 2
    repeat_stop_after: int = 8

    # --- the model's own numbers (per model set) -----------------------------
    max_images: int = 4
    max_audio: int = 1
    # The output cap the request carries (`MODEL_MAX_TOKENS`): the fold keeps
    # this much of the budget free for the answer.
    output_tokens: int = 8192

    # --- derived -----------------------------------------------------------
    def chars(self, share: float) -> int:
        return max(1, int(self.budget * share * self.chars_per_token))

    def tokens(self, share: float) -> int:
        return max(1, int(self.budget * share))

    @property
    def result_chars(self) -> int:
        """The most one tool result shows inline; the rest goes to a file."""

        return self.chars(self.result_share)

    @property
    def page_chars(self) -> int:
        """What a tool that pages (a read, a document) puts in one page: under
        the result cap with room for the page's own header and footer."""

        return max(1_000, int(self.result_chars * 0.9))

    @property
    def keep_recent_tokens(self) -> int:
        return self.tokens(self.keep_recent_share)

    @property
    def summary_tokens(self) -> int:
        return min(self.summary_ceiling_tokens, self.tokens(self.summary_share))

    @property
    def stub_min_chars(self) -> int:
        return self.chars(self.stub_share)

    @property
    def instruction_bytes(self) -> int:
        return min(self.instruction_ceiling_bytes, self.chars(self.instruction_share))

    @property
    def media_budget(self) -> dict[str, int]:
        return {"image": self.max_images, "audio": self.max_audio}

    def with_budget(self, budget: int | None, chars_per_token: float | None = None) -> Limits:
        """The same settings on a known budget."""

        return replace(
            self,
            budget=budget or self.budget,
            chars_per_token=chars_per_token or self.chars_per_token,
        )

    @classmethod
    def from_settings(cls, agent: Any = None, model: Any = None) -> Limits:
        """The settings' numbers, on the default budget until the model's window
        is known. Any object with the fields will do (a test's stand-in)."""

        values: dict[str, Any] = {}
        for name in (
            "result_share",
            "keep_recent_share",
            "summary_share",
            "summary_ceiling_tokens",
            "stub_share",
            "instruction_share",
            "instruction_ceiling_bytes",
            "read_lines",
            "line_chars",
            "search_page",
            "preview_chars",
            "shell_output_chars",
            "web_text_chars",
            "snapshot_chars",
            "visible_text_chars",
            "screenshot_height",
            "csv_rows",
            "command_timeout",
            "command_timeout_max",
            "parallel_calls",
            "repeat_note_after",
            "repeat_stop_after",
        ):
            if agent is not None and getattr(agent, name, None) is not None:
                values[name] = getattr(agent, name)
        for name in ("max_images", "max_audio"):
            if model is not None and getattr(model, name, None) is not None:
                values[name] = getattr(model, name)
        if model is not None and getattr(model, "max_tokens", None):
            values["output_tokens"] = int(model.max_tokens)
        return cls(**values)


DEFAULT_LIMITS = Limits()
