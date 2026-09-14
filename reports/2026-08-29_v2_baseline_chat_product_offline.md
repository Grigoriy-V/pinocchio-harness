# Queue 2A — Telegram UX baseline, implemented and tested offline

**Date:** 2026-08-29
**Scope:** ROADMAP queue item 2, sub-step 2A only.
**Status:** implementation evidence. **No deploy, no live Telegram acceptance,
no product-runtime worker, no model call, no change to Telegram's live
configuration.** Queue 2 is not closed by this report.

## What this changed

Telegram was a correct transport that read like a debug console: raw internal
tool names in the chat, an unformatted wall of text for every answer, a typed
command list nobody could discover, and approval buttons that stayed pressable
after the decision they described had already been made. 2A is the presentation
half of that, and only the presentation half. The semantic answer, the
capability decisions, the tool policy and the domain `Message` / `ContentPart`
types are untouched.

### 1. A native command shell

`PRODUCT_COMMANDS` in `ui/telegram/api.py` is the menu — `/new`, `/can`,
`/stop`, `/help`, each with a readable description — alongside the bot's long
and short descriptions. `/check` is deliberately not in it: it tries every
capability for real, which makes it a diagnostic rather than one of four things
to offer a person. It remains a fully working, model-free typed command, and
both halves of that are asserted.

`tools/telegram_profile.py` publishes the menu and descriptions. It prints what
it would send and does nothing else unless `--publish` is passed. **It has not
been run**; publishing changes what every user of the bot sees and is a
deployment action for the human.

### 2. Concise, rendered onboarding

`/start` and `/help` send one short card built from the same command list, so
the menu and the card cannot drift. It is written as ordinary Markdown and put
through the same renderer as an assistant answer, which makes the onboarding
card evidence that the formatting path works rather than a special case beside
it. It does not restate the tool inventory: `/can` remains the truthful
capability source.

### 3. Ordinary Markdown, rendered safely

`ui/telegram/markdown.py` is new, standard-library only, and renders the
deliberately small subset the baseline promises: paragraphs, headings, bold,
italic, ordered and unordered lists, inline code, fenced code blocks, quotes and
links. The canonical assistant text stays ordinary Markdown — that is what the
store keeps, asserted by a test — and this is only how Telegram shows it.

Three properties make formatting unable to lose an answer:

- **blocks, not one string.** `Formatted` now holds `(html, plain)` pairs and a
  message is only ever cut between whole blocks, so a piece can never begin
  inside a `<b>`. A block too long to send by itself (`pack` returns `None`)
  degrades the entire message to complete plain text. A long fenced code block
  is pre-split into several `<pre>` blocks so length alone does not cost the
  formatting.
- **every block carries its plain reading.** If Telegram refuses to parse the
  markup, `send_message` resends that same piece unformatted rather than
  raising. The plain reading of a block is never longer than its markup, so it
  always fits.
- **two safety nets before that.** A renderer exception falls back to the
  escaped source text, and `markdown.balanced` re-checks every block's tags and
  replaces any that does not come out well-formed.

Unsupported or malformed markup is emitted as text: unbalanced `**`, a lone
backtick, `snake_case`, `2 ** 3`, model-written HTML and a `javascript:` link
all stay literal and readable.

### 4. Tool activity instead of tool names

`TOOL_ACTIVITY` in `ui/telegram/adapter.py` maps every tool the agent can
currently call to a concise English label (`search_web → Searching the web…`),
with `Working…` for anything unknown. The mapping in
`reports/archive/telegram_baseline_chat_product.md` is used verbatim; the four remaining
real tools (`inspect_page`, `list_files`, `remember_fact`, `search_memory`) were
given labels in the same style, because `Working…` is the safety net for a tool
that does not exist yet, not the plan for four that do. A test asserts every
name in the live toolbox has a label. Labels are English whatever language the
conversation is in — this is interface chrome, not part of the answer.

`ToolActivity` sends **one** transient status message per turn, edits it as the
work moves on, and deletes it the moment there is an answer to read, so it
cannot compete with the final message and nothing is left in the history. It is
used by the ordinary answer path and by the resumed-approval path. Nothing in it
can fail a turn: a status that could not be sent, edited or deleted is a
cosmetic loss.

### 5. Truthful, reusable inline settlement

`Incoming` now carries `callback_message_id`, which is the smallest extension
that lets the adapter edit the keyboard of the message the button belongs to.
After — and only after — the application transition succeeds, that same message's
keyboard becomes one status button: `✓ Approved` with Telegram's `success`
style, or `✕ Rejected` with `danger`. `style` is a recent Bot API field, so a
refusal retries once without it; the word is the state and the colour is only an
enhancement.

Settled buttons carry a `settled:` callback prefix that `wire.needs_model`
recognises, so pressing one is classified model-free at the front door and never
spends a GPU wake. The adapter answers such a callback and returns **before**
reading or creating any thread, so it cannot start a conversation, repeat the
approval or resume a task.

A failure is never displayed as settled. The distinction asserted is precise: a
task that resumes and then fails on its own work *was* genuinely approved and
says so; the transition itself failing settles nothing. The existing task-plan
approval uses this common behaviour rather than a plan-specific path.

The chat text is held to the same rule as the keyboard. Pressing approve answers
immediately with a neutral `Starting…`, and only the first proof that the resume
happened turns that message into `Approved; working…` and settles the buttons. A
transition that raises therefore leaves neither an approved button nor an
approved line — just the neutral message and the failure notice.

## Ownership and boundaries

| Concern | Owner |
| --- | --- |
| Markdown → Telegram HTML blocks, balance check | `ui/telegram/markdown.py` (new, stdlib only) |
| `Formatted`, packing, parse-refusal fallback, keyboards, menu text, Bot API calls | `ui/telegram/api.py` |
| Callback message identity, model-free classification of settled callbacks | `ui/telegram/wire.py` (still stdlib only) |
| Activity labels, transient status, settlement after a successful transition | `ui/telegram/adapter.py` |
| Publishing the menu and descriptions | `tools/telegram_profile.py` (new) |

Preserved deliberately: observation versus presentation is unchanged — tool
evidence, screenshots and page renders stay internal, and only an explicitly
outbound part reaches the chat, per the correction in
`reports/2026-08-29_v2_capabilities_browser_workspace_documents.md`. The task
plan and result keep their existing structured `Formatted` presentation and now
gain safe multi-message splitting for free. `deploy/modal/control_app.py` needed
no change: the deployed worker runs the same adapter.

Not done, by exclusion: 2B streaming, fake streaming, stop-generation, harness
or agent-loop changes, session-event logging, permissions, or any cross-platform
UI abstraction.

## Files changed

- `ui/telegram/markdown.py` — new.
- `ui/telegram/api.py` — `Formatted` as blocks, `from_markdown`, `Piece`,
  `pack`, parse-refusal fallback, `settled_keyboard`, `BotCommand`,
  `PRODUCT_COMMANDS`, bot descriptions, `edit_reply_markup`, `delete_message`,
  `set_my_commands`, `set_my_description`, `set_my_short_description`.
- `ui/telegram/wire.py` — `callback_message_id`, `settled:` constants,
  model-free classification of settled callbacks.
- `ui/telegram/adapter.py` — rendered onboarding, `TOOL_ACTIVITY`,
  `activity_labels`, `ToolActivity`, Markdown answer delivery, `_settle`.
- `tools/telegram_profile.py` — new.
- `tests/test_telegram_markdown.py` — new (19 tests).
- `tests/test_telegram_adapter.py` — labels replace raw names in the existing
  batch test; 17 tests added.
- `tests/test_telegram_webhook.py` — settled callbacks spend no GPU wake.

## Checks run

All offline, in-process, with no network, no model endpoint and no credential.

| Check | Result |
| --- | --- |
| `pytest tests/test_telegram_adapter.py` | 68 passed |
| `pytest tests/test_telegram_markdown.py` | 19 passed |
| `pytest tests/test_telegram_webhook.py` | 15 passed |
| `pytest` (whole suite) | **660 passed, 1 skipped** |
| `ruff check` on every changed and new file | passed |
| `git diff --check` | passed |

`ruff` is not installed in `.venv`; the check was run with the ruff 0.12.11 on
`PATH`, against the repository's own `pyproject.toml` configuration.

The webhook's import-cost test and the `wire.py` dependency test both still
pass, so `api.py` importing `markdown.py` and `wire.py` did not put the agent
stack back on the webhook's import path.

## Limits

- **This is not product acceptance.** Every runtime claim above is offline
  evidence. Telegram's official Bot API documentation currently defines
  `style` values `success` and `danger`, but the real parser, the deployed bot
  response and the actual client rendering have not been observed in this
  workflow.
- The `style` fallback is exercised by a test that forces a refusal; whether a
  particular installed Telegram client visibly renders the colour remains live
  evidence.
- `deleteMessage` fails for messages older than 48 hours. A turn that runs that
  long would leave its status behind. Accepted: it is cosmetic and cannot fail
  the turn.
- The menu and descriptions exist only in this repository. **Telegram's live
  configuration is unchanged.**
- Settlement needs `callback_message_id`; a callback arriving without one
  (Telegram omits the message for very old callbacks) simply does not settle,
  which is the honest outcome rather than a false one.
- The activity label mapping is complete for today's toolbox. A tool added
  without a label reads as `Working…`, and the toolbox test will say so.

## What remains

**For Codex review:** the diff across the five changed and three new files, in
particular the settlement timing rule (settle on the first proof the transition
was accepted) and the decision to extend the doc's example label mapping to the
four remaining real tools.

**Gated, and not performed here:**

1. Publishing the command menu and descriptions with
   `tools/telegram_profile.py --publish` — this mutates live Telegram
   configuration for every user of the bot.
2. Deploying `assistant-control` so the deployed worker serves the new
   presentation.
3. The live scenarios that actually close 2A —
   `reports/archive/telegram_baseline_chat_product.md` §15 A–F: onboarding, formatting,
   tool activity, inline settlement, capability truth, and an ordinary
   tool-capable answer. Each of these wakes a product-runtime worker and needs
   its own explicit permission.
