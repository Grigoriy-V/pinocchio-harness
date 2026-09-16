# Roadmap 31, research: how the references bound what the model reads, keeps and produces

**Date:** 2026-09-16. **Status:** research with options; nothing here is
approved until the human says so in words. Written under the AGENTS.md rule
of 2026-09-14 (a large step starts with the references; the build cites the
report and is checked against it) and the rule "A limit is derived, not
written".

Read on 2026-09-16 by five subagents from the sources in §6: Claude Code
(official docs: context-window, model-config, memory, tools-reference,
env-vars; issues), Codex (`codex-rs/protocol/src/openai_models.rs`,
`core/src/compact.rs`, `tools/context.rs`, `unified_exec.rs`,
`agents_md.rs`, `config/defaults.toml`), DeepSeek Harness
(`docs/config-catalog.md`, package READMEs, architecture notes), Hermes
(`agent/context_compressor.py`, `tools/*.py`, docs), OpenClaw (docs:
compaction, session-pruning, token-use, exec, web-fetch, system-prompt). A
sixth subagent inventoried every written limit in this repository (§2).
What was not found is in §7. What 27 step 3's research already covered
(read by lines, search paging, shell output spill in the references) is
cited, not repeated.

## 1. What each reference does

### 1.1 Context: when to fold, what to keep, how big the summary

| | Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|---|
| trigger | at the model's window (200K models) or ~967K on 1M models; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` lowers it; `/autocompact <tokens>` "capped at the model's context window" | `auto_compact_token_limit` = 90% of `context_window`; a configured value is clamped to that; `effective_context_window_percent` 95% for inputs | `thresholdRatio` 0.8 of the window; plus overflow recovery when the provider rejects | `compression.threshold` × `context_length` (0.5 or 0.8, the sources disagree); 75% for windows under 512K | usage ≥ window − reserve (20,000 tokens for windows ≥ 80K) | `max_input_tokens` = min(chosen size 128K/256K/512K, window); fold when the estimate exceeds it |
| kept verbatim | recent turns; re-reads up to five recently touched files, a file over 5,000 tokens as a path | recent user messages within 20,000 tokens (`COMPACT_USER_MESSAGE_MAX_TOKENS`) | `retainRatio` 0.16 of the window | `protect_last_n` 20 messages, `protect_first_n` 3, tail `target_ratio` 0.20 of the threshold, lean tail 10K–25K tokens | `keepRecentTokens` 20,000; tool calls kept paired with results | `keep_turns` 2 exchanges, `KEEP_STEPS` 2 inside a long tool turn |
| summary size | what the summarizer produces (no cap in docs) | not stated | `maxTokens` 8,192 for the summarization call | 20% of the folded content, floor 2,000, ceiling 10,000 tokens (or 5% of the window, ceiling 12,000, the docs disagree) | not stated | 15 words a message, floor 150, ceiling 600 words; `SUMMARY_ALLOWANCE` 800 tokens |
| old tool results | "microcompact" clears old tool outputs without a model call | `tool_output_token_limit` per stored output | pruner: head 4,096 + tail 1,024 chars when over 8,192, before compaction | over 1,500 chars outside the protected tail → "[Old tool output cleared to save context space]"; newest 6 tool rounds kept; newest 3 images kept | pruning skipped under 30% usage; over 4,000 chars → first and last 1,500; at 50% usage hard-clear; last three assistant turns never pruned | `keep_results` 2 newest whole, older → stub with `read_history <position>` when over `STUB_MIN_CHARS` 200 |
| the way back | files re-read from disk | — | the raw events stay in the append-only log | `session_search` named in the summary; skills reloadable | full history on disk, `sessions_*` | `search_history`, `read_history`, named in the prelude |
| media kept | — | — | — | newest 3 images | images omitted from the summary input, marked | `MEDIA_BUDGET` image 4 / audio 1 per request, older → "[image …]" |

### 1.2 Output tokens

| Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, default reported 32K, clamped by the CLI | per-model metadata | `maxTokens` 256,000, "a model's own cap and explicit request values win"; on the cut: partial text kept, "Send 'continue'" | per-model from provider metadata (`max_completion_tokens` / `max_output_tokens` / `max_tokens`; OpenRouter default 4,096 when unset) | `models.providers.*.models[].maxTokens` per model | `MODEL_MAX_TOKENS` 8,192, one number for every model set; `silent_cut()` when nothing was said |

### 1.3 Tool output: the cap and the way back

| tool | Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|---|
| shell output | 30,000 chars inline (`bashOutputMaxChars`, max 150,000), the full output in a file up to 5 GB | `max_output_tokens` 10,000 a call; "Warning: truncated output (original token count: N)"; the session id polls the rest | 64,000 bytes a stream, then the tail; "the full output saved to a spill file whose path is reported", 64 MiB | `tool_output.max_bytes` 50,000; "the full text saved to a file" | chunked to chat; a live cap by window: 16K chars under 100K tokens, 32K at 100K+, 64K at 200K+; one result at most 30% of the window | 16,000 chars, middle cut, nothing saved; background 4,000 tail, no offset |
| read | 2,000 lines, long lines cut, `offset/limit`, "PARTIAL view" | the shell | 2,000 lines / 2,000 chars a line / 51,200 bytes; "Use offset=N to continue" | 100,000 chars, `next_offset`, `limit` ≤ 2,000 | — | 500 lines / 30,000 chars / 2,000 a line, offset (27.3) |
| search | `head_limit`, `offset`; Glob 100 with a truncation flag | — | 250 matches inline, the rest to a spill file; glob 100 | `limit` 50, `offset` | — | 100 a page, offset (27.3) |
| web fetch | 100 KB of text, then a small model extracts | — | 5 MB / 100,000 chars, `truncated` flag | — | `maxChars` 20,000, body 750,000 bytes, `truncated: true` and a `spill` file | `fetch_page` 12,000 chars with offset; `view_web_page` 12,000 chars, no offset |
| browser snapshot | — | — | — | — | `maxChars` per call (default not found) | 12,000 chars, `query` filters; visible text 8,000 with no way back; 12 `<select>` options; 80 chars of a value; full-page screenshot 6,000 px, silently |
| images per result | resized to the model's limits; a 20-page PDF cap per read | `view_image` `detail` high/original | 600 images a request, 20 MiB inline | newest 3 kept in context | `view_image` `maxImages` 20; `imageMaxDimensionPx` 1,200 | `MAX_IMAGES` 4 a result; `MAX_PAGES_PER_VIEW` 2; `MEDIA_BUDGET` 4 a request (from vLLM) |
| CSV / documents | — | — | — | — | — | 200 rows, no paging; `read_document` sections with `from_section` |
| instruction file | CLAUDE.md up to 4 MiB in full, a larger file skipped; auto-memory index 200 lines / 25 KB with an error telling the model to rewrite it | `project_doc_max_bytes` 32,768, truncated silently (issue #7138) | `maxBytes` (65,536 reported), whole broader files dropped first, a "Workspace instruction budget …" notice naming the paths | `context_file_max_chars`; memory 2,200 / 1,375 chars, a full memory returns an error rather than dropping | `bootstrapMaxChars` 20,000 a file, 60,000 total, a notice "to read the affected files directly" | 8,000 bytes ("roughly two Telegram messages"), a "[… the rest … was too long to include …]" tail |
| facts retrieved | by the memory file's size | — | — | by chars | by chars (`memoryGetMaxChars`) | 5 facts (`retrieved_facts`) |

### 1.4 Command timeouts

| Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|
| default 120 s, max 600 s, both settings; the effective ceiling "the larger of" the two; past it the command is killed | no timeout: `yield_time_ms` 10 s (250–30,000) returns a session id; `write_stdin` polls | 120 s default, `maxTimeoutMs` 600 s a setting; background runs "no execution timeout applies" | 180 s default, cap 600 s (`TERMINAL_MAX_FOREGROUND_TIMEOUT`); a request above the cap is **started as a tracked background process with notify** and told so, "instead of being refused" | 1,800 s default, `0` disables, `yieldMs` auto-backgrounds after 10 s | 120 s default, 600 s max, a request above 600 **clamped silently**; deployed the Modal function dies at 660 s; background locally only |

### 1.5 The principle, stated

- Codex: `usable_context_window = context_window × 95%`,
  `auto_compact_token_limit = 90% of context_window`, a configured value
  "clamps to 90% of the context window when available".
- OpenClaw: "OpenClaw derives the live tool-result cap from the effective
  model context window"; a single result at most 30% of the window; the
  pruning trigger `max(50000, floor(contextWindow × 0.3))`.
- DeepSeek: "pressure scales ratios against capacity from the route-owning
  LLM adapter".
- Hermes: thresholds and summary size as fractions of `model.context_length`.
- Claude Code: "Claude Code caps the window at the model's context window".
- None of them derives the tool-level byte and line caps (read lines, grep
  matches, shell bytes) from the window: those are settings with defaults,
  and every one has a way back (offset, session, spill file).

## 2. What this harness has (the inventory, 2026-09-16)

About 19 settings and 70 constants bound what the model reads, keeps or
produces. Of the content cuts, nine have a way back (read, search and
history pages; `read_document` sections; `view_pages` pages; `fetch_page`
offset; the snapshot's `query`; stubs with `read_history <position>`; the
fold, whose words stay in the store) and about eighteen do not: the shell
output's middle (foreground and background), the executor's backstop
`MAX_RESULT_CHARS` 32,000 with its 2,000-char tail, `MAX_IMAGES` 4 and
`MAX_IMAGE_BYTES` a result, `MAX_FAILURE_CHARS` 400 (silent), the
browser's visible text 8,000, a snapshot line 120, a value 80, twelve
`<select>` options, the full-page screenshot at 6,000 px, `view_web_page`
text 12,000 with no offset, CSV 200 rows, the instruction file's tail,
`MEDIA_BUDGET`'s dropped media (a placeholder that says what it was, not
how to see it again), twenty kept web views (old screenshots deleted).
Constants tied to something gone or to one interface: `MEDIA_BUDGET`
"mirrors `MM_LIMITS` in `deploy/modal/model_app.py`" (vLLM),
`parse_context_limit` reads vLLM's `max_model_len`, `MAX_INSTRUCTION_BYTES`
"roughly two Telegram messages", `MAX_ITEMS` 10 "Telegram's album limit",
`MAX_PAGES_PER_VIEW` 2 "the serving limit is four images per prompt",
`MAX_RESULT_CHARS`'s comment naming per-tool numbers that have drifted.
The fold's own budget is already derived: `max_input_tokens = min(size,
window)`; `keep_turns`, `keep_results`, `retrieved_facts`, `max_tokens`
are settings; everything else is a constant.

## 3. What follows

1. **Two kinds of limit, and the references keep them apart.** What
   concerns the request as a whole (when to fold, how much recent text to
   keep, the summary's size, the live size of one tool result, the
   instruction file's share) is a fraction of the window in every
   reference that states it. What concerns one tool's page (lines a read,
   matches a search, bytes a shell result shows) is a setting with a
   default, the same across models, and the rest is reachable.
2. **A cut always has a way back, and it is one of three shapes:** an
   offset on the same call (read, search, fetch), a file whose path is
   reported (shell output in four of five references, grep overflow in
   DeepSeek, web fetch in OpenClaw), or a locator into stored history
   (this harness's stub, OpenClaw's on-disk history, DeepSeek's log).
3. **A timeout above the cap is not clamped:** Hermes starts the command
   in the background and says so; Codex has no timeout at all; Claude Code
   and DeepSeek cap with a setting the person can raise. Nobody silently
   lowers the number the model asked for.
4. **Output tokens are the model's number**, read from the provider's
   metadata or set per model, never one constant for every model set;
   when the cut happens the partial text is kept and the model or the
   person is told.
5. **Media per request is the model's limit**, not a serving engine's; the
   references keep the newest few images in context and drop older ones
   with a marker, and cap what one call may add (20 in OpenClaw).
6. **The instruction file's cap is a share with a notice**, and the notice
   tells the model how to read the rest (OpenClaw), or the write is
   refused so nothing is dropped silently (Hermes's memory, Claude Code's
   memory index).

## 4. Options, with a recommendation

### 4.1 The shape: three options

- **A, everything derived:** every number a fraction of the window or of
  the request budget, including read lines and search matches. Nobody
  does this; a page of 2,000 lines is the same page on every model, and a
  fraction would make `read_file` return different pages on different
  models for no reason.
- **B, the references' split (recommended):** the whole-request limits
  derived from the window and the budget; the per-tool page limits become
  settings with the references' defaults; every cut gets a way back;
  timeouts without a silent clamp. The table in 4.2.
- **C, settings only:** every constant becomes a setting, nothing derived.
  Satisfies "not written once" but leaves the fold and the media budget
  disconnected from the model, which is the audit's complaint.

### 4.2 Per limit, under B

| limit | today | recommended | reference |
|---|---|---|---|
| fold trigger | `max_input_tokens = min(size, window)`, fold when over | keep; add the headroom the references keep for the output: fold at `budget − max_tokens − summary allowance` so a request never lands within the output's reach | Codex 95%/90%, DeepSeek 0.8 |
| kept verbatim after a fold | `keep_turns` 2, `KEEP_STEPS` 2 | a token share: `keep_recent_share` of the budget (default 0.15, floor two exchanges), so a 512K conversation keeps more than a 128K one; `keep_turns` stays as the floor | DeepSeek 0.16, OpenClaw 20K, Codex 20K |
| summary size | 15 words a message, 150–600 words | a share of the folded text (0.2) with floor and ceiling as shares of the window (ceiling 5%, at most 12K tokens); `SUMMARY_ALLOWANCE` becomes that ceiling in tokens | Hermes |
| old tool results | `keep_results` 2, `STUB_MIN_CHARS` 200 | keep the count; the stub threshold a share of the budget (a result under 0.1% of the budget stays whole) rather than 200 chars | Hermes 1,500 chars, OpenClaw 4,000 |
| live cap on one tool result (`MAX_RESULT_CHARS` 32,000) | constant, middle cut, nothing saved | derived: `result_share` of the budget (default 1/8, so 32K tokens ≈ 96K chars at 256K); when a result is over it, the whole text is written to `.agent/results/<call id>.txt` and the note names the path and the offset of the cut (`read_file` pages it) | OpenClaw 30%; DeepSeek/Hermes spill file |
| shell output (`MAX_OUTPUT_CHARS` 16,000, tail 4,000) | constant, middle cut | a setting `shell_output_chars` (default 30,000, the references' 30–64K) under the live cap; the full output always written to `.agent/commands/<id>.txt` (foreground and background), the path and the size in the result; `command_output` gets `offset` | Claude Code, DeepSeek, Hermes |
| executor tail, `MAX_IMAGES`, `MAX_IMAGE_BYTES`, `MAX_FAILURE_CHARS` | constants | `MAX_IMAGES` derived from the media budget (below); the failure cut told ("… (cut at N chars)"); the tail a share of the cap | — |
| output tokens `max_tokens` 8,192 | one setting for every set | per model set (`MODEL_<SET>_MAX_TOKENS`), default from the provider's metadata when it says (OpenRouter's `top_provider.max_completion_tokens`), else the model's documented cap; `silent_cut()` stays | Hermes, Codex, DeepSeek |
| media per request (`MEDIA_BUDGET` image 4 / audio 1) | constant from vLLM | a setting per model set (`MODEL_<SET>_MAX_IMAGES`, `MAX_AUDIO`), default 4/1 so nothing changes until a set says more; the placeholder for a dropped item names how to see it again (a stored file's path for `read_file`, a page number for `view_pages`) | OpenClaw 20 a call, Hermes newest 3 |
| pages per view (`MAX_PAGES_PER_VIEW` 2) | constant from vLLM | `min(images budget, what the call asks)`; the schema's `maximum` follows the budget | Claude Code 20 pages |
| instruction file 8,000 bytes | constant from Telegram | a share of the budget (2%, at most 32 KiB); the notice says "read_file <path> for the rest" as OpenClaw's does; the write path refuses over the same number, with the number | OpenClaw, Codex 32 KiB, DeepSeek |
| facts retrieved 5 | setting | keep as a setting; the facts layer bounded in chars by a share too (a fact is 500 chars at most already) | Hermes by chars |
| read page 500 lines, 2,000 a line, 30,000 chars | constants | settings `read_lines` (2,000, the references' number) and `line_chars` (2,000); the page's char cap derived from the live result cap, not written | all four |
| search 100 a page, 400-char preview | constants | settings; over the page the rest is paged (already) | DeepSeek 250, Hermes 50 |
| `fetch_page` 12,000, `view_web_page` 12,000 without offset | constants | one setting `web_text_chars` (20,000); `view_web_page` gets `offset` too; over the live cap the text spills to a file (OpenClaw's `spill`) | OpenClaw |
| browser: visible text 8,000, snapshot 12,000, line 120, value 80, 12 options, screenshot 6,000 px | constants | `max_chars` and `offset` on `snapshot` and on the visible text (the way back the cut note already names but the tool lacks, audit §1.4); a `<select>` with more options says how many and `query` shows them; a full-page screenshot says when it was clipped and at what height; the pixel cap a setting | OpenClaw `maxChars` |
| CSV 200 rows | constant | `from_row` paging on `read_document`, the label names the next call | — |
| kept web views 20 | constant | a setting; unchanged otherwise (storage, not context) | — |
| command timeout 120 default / 600 max, clamped | constants, silent | both settings; a request above the max: locally the command is started in the background with the reason and the id (Hermes); deployed, where the runner cannot keep a process, a refusal naming the ceiling and the reason (the function's own deadline), never a silent clamp; the deployed ceiling read from one place (`control_app`'s timeout minus the margin) | Hermes, Claude Code |
| chars-per-token 3.0, calibration | constant with calibration | unchanged (already calibrated per model); the token counts above are computed through it | — |

### 4.3 Where the numbers live

One place: `ContextPolicy` for the shares (`keep_recent_share`,
`summary_share`, `result_share`, `instruction_share`, `stub_share`) with
their floors and ceilings, filled from `AgentSettings` (env
`AGENT_<NAME>`), and the per-tool page settings on `AgentSettings` too
(`read_lines`, `line_chars`, `search_page`, `shell_output_chars`,
`web_text_chars`, `snapshot_chars`, `screenshot_height`,
`command_timeout`, `command_timeout_max`); the per-model numbers on
`ModelSettings` (`max_tokens`, `max_images`, `max_audio`, `context_tokens`).
The tools receive their numbers from the registry when they are built,
not from module constants, so a test can build a toolbox with a small
budget and see every cut and every way back.

### 4.4 Measurement

- Offline: a test per limit that the cut is told and the rest reachable
  (the spill file exists and `read_file` pages it; `offset` on the browser
  text; `from_row`; the timeout above the max backgrounds locally and
  refuses deployed with the number); a test that a policy built for a 128K
  budget and one for a 512K budget differ in every derived number and in
  none of the page settings.
- Live (a gate, priced): one local smoke, GLM: a command whose output is
  200 KB (the spill file and the page), a 5,000-line file read, a page with
  a long `<select>`; then scenario I of the wider set (the shortened
  result read back). The deployed side changes at the next deploy: the
  timeout refusal, the spill file on the volume.

### 4.5 Out of this step, noted

- Parallel calls and streamed tool output (32) change how a spill file is
  read while a command runs; the file path is the same either way.
- Reading the provider's model metadata for `max_tokens` and the window
  belongs to the model layer (33); this step adds the per-set settings
  with a default and leaves the metadata read to 33.
- Hermes's "newest N images kept" and OpenClaw's usage-based pruning are
  a second pruning stage beside the fold; not this step's.

## 5. Acceptance for the step

Every constant named in §2 is a setting, a share of the budget, or gone;
`grep -n "= [0-9_]\+$"` over `app/context`, `app/tools`, `app/instructions.py`
finds only shares with a comment saying what they are a share of, and
Telegram's own numbers in `ui/telegram/`; every cut names its way back
and a test walks it; the build report lists each row of §4.2 with where
it landed.

## 6. Sources

Claude Code: code.claude.com/docs/en/{context-window, model-config,
memory, tools-reference, env-vars, settings-reference}; anthropics/
claude-code issues #25881, #24159, #6910, #22364. Codex: openai/codex
`codex-rs/protocol/src/openai_models.rs`, `core/src/compact.rs`,
`core/src/compact_token_budget.rs`, `core/src/tools/context.rs`,
`core/src/tools/handlers/{shell_spec,unified_exec,view_image_spec}.rs`,
`core/src/agents_md.rs`, `config/defaults.toml`; the config reference;
issues #16068, #7138. DeepSeek Harness: `docs/config-catalog.md`,
`packages/shell/{tool-bash,bash-local}/README.md`,
`packages/fs/{tool-fs,tool-fs-search}/README.md`,
`packages/web/web-fetch-http/README.md`,
`packages/context/agent-instructions/README.md`,
`packages/llm/llm-deepseek/README.md`, the architecture note of
2026-07-10 on compaction pressure, discussion #1166. Hermes:
`agent/context_compressor.py`, `agent/model_metadata.py`,
`tools/{file_tools,terminal_tool,terminal_hints}.py`,
`website/docs/developer-guide/context-compression-and-caching.md`,
`website/docs/user-guide/{configuration,features/memory}.md`. OpenClaw:
docs.openclaw.ai/{concepts/compaction, concepts/session-pruning,
reference/token-use, reference/session-management-compaction/compaction,
gateway/config-agents/heartbeat-compaction-and-streaming, tools/exec,
tools/web-fetch, concepts/system-prompt, channels/telegram/media}.

## 7. Not found

- Claude Code's official default for output tokens and whether an
  over-max Bash timeout is clamped or refused; Codex's max output tokens
  field and its collection cap in bytes; DeepSeek's and Codex's
  clamp-or-refuse wording for a timeout above the max; Hermes's
  compression threshold default (0.5 or 0.8) and summary ceiling (10K or
  12K); OpenClaw's read tool caps and the browser snapshot default.
- A reference that derives a per-tool page size from the window: none;
  the finding in §3.1 rests on that absence.
