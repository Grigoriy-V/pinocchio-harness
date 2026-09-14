# Version 2 — the tool system finalized, and the queue after 4.4

**Date:** 2026-09-03
**Agent:** Claude, direct session
**Status:** analysis and records. No code changed. The finalized design is
`docs/v2_tool_system.md`; the draft it came from is
`reports/archive/v2_tool_system_design.md`. The queue below was approved in the chat on
2026-09-03 and is in `ROADMAP.md`; the durable choices are in `DECISIONS.md`
under the same date.

## What was asked

After 4.4 closed with known problems, the human asked for a new plan built on
the tool-system draft, with the tool architecture fixed durably, and gave four
constraints: `ask_user` and "saying only what was observed" go to the end of
the queue because they depend on the tools; the browser may follow the tools
and must be designed for its full function even though the first version needs
neither clicks nor navigation; the served-parser fix in `tools/gemma4_parser.py`
is **not** to be deployed, because it is a crutch for one model — the runtime
has to survive what any model emits; and DeepSeek Harness, Hermes Agent and
OpenClaw were to be read before finalizing.

The open question was where 4.6 (context engine, archive recovery) and 4.7
(restart, resume, scenario suite) go so that they neither block the tools nor
are rebuilt after them.

## The three references, on the questions that matter here

Read from source on 2026-09-03: DeepSeek `packages/core/tools/src/index.ts`,
`schema.ts`, `packages/core/agent-loop/src/tool-calls.ts`,
`docs/tool-execution-pipeline.md`, the `fs`, `tool-fs`, `spill`,
`compaction-tool-result-pruner` and `repeat-tool-reminder` package READMEs;
Hermes `tools/registry.py`, `model_tools.py`, `agent/tool_executor.py`,
`agent/tool_guardrails.py`, `tools/environments/base.py`, `modal.py`,
`local.py`, `tools/tool_result_storage.py`; OpenClaw
`packages/tool-call-repair/src/*`, `src/agents/tool-result-error.ts`,
`embedded-agent-tool-results.ts`, `harness/tool-result-middleware.ts`,
`embedded-agent-runner/run/attempt.tool-call-argument-repair.ts`,
`tool-call-argument-decoding.ts`, `tool-result-truncation.ts`,
`docs/tools/browser.md`, `docs/tools/loop-detection.md`. Six Hermes files were
rate-limited by GitHub and read through their callers instead;
`tool_result_classification.py` and `file_tools.py` were not read.

### What a tool result is

| | DeepSeek Harness | Hermes Agent | OpenClaw |
|---|---|---|---|
| Success | `{isError:false, value, content[]}`; `value` validated against a declared output schema, `render()` projects it to content | a JSON string the handler builds | `{content[], details?}` |
| Failure | `{isError:true, error:{message, info?{name, code}}, content:[text "Error: …"]}` | `{"error": "…"}` via `tool_error()`, bounded to 2,048 chars | inferred from `details.status`, `ok`, `success`, `exitCode`, `error`, `timedOut` — a 40-line heuristic |
| Who decides "failed" | the registry, from a discriminated union | the handler, by writing `error` | a reader, by inspecting shapes |

DeepSeek is the model to borrow: the result is a typed union and failure is a
field. OpenClaw is the warning: a result whose failure has to be inferred from
its details grows a classifier that lists every status word ever used. Hermes
sits between — a string convention, but one helper writes it and one dispatch
seam bounds it. Our current `error: ` prefix is the Hermes shape with the
helper missing.

**Taken:** `ToolOutcome(content, failure: ToolFailure | None)` with
`ToolFailure(code, message, detail)`. Not taken: DeepSeek's mandatory output
schema and renderer per tool — a second schema to keep true for eleven tools
whose results are already bounded text and images, with no consumer that
branches on a structured value.

### Where the failure lives after the call

DeepSeek appends `tool/result` to an append-only session log with the error
info beside the message, and *derives* the model's history from that log. We
have two stores with two lifetimes: the checkpoint (the turn in flight) and the
conversation store (history). Options considered:

1. **A field on `Message`**, checkpointed now, persisted when the store's next
   schema lands. Zero migration now; the two in-flight readers
   (`failed_before`, `todo.current`) get the typed field; stored history keeps
   the text projection, which is all the model reads from it.
2. **A `ContentPart` of kind `failure`.** Persisted for free inside the content
   JSON, but it puts `code` and `detail` into fields meant for media, and a
   failure is not content.
3. **A new column now.** Correct and a `user_version` bump on a populated Neon
   database, which the roadmap deliberately holds to one migration in 4.6a.

**Taken:** 1, with the column joining the 4.6a migration, where the context
engine is the first stored-history reader that needs it.

### Surviving what a model emits

This is where the human's constraint bites: no per-model parser on the server.
The references agree on the layer and differ on how far to go.

- **Hermes** parses arguments without repair (`_parse_tool_arguments`: not a
  JSON object → the tool is not executed and the model gets an error result),
  then `coerce_tool_args` fixes the few things open-weight models get wrong
  against the schema: `"42"` → `42`, `"true"` → `true`, a scalar where an array
  is declared, a JSON string where an object is. Legacy names are aliased at
  the dispatch seam. Errors are sanitized of role tags, fences and CDATA before
  the model sees them.
- **OpenClaw** goes furthest: HTML-entity decoding of arguments, a smart-quote
  and unclosed-string repairer that decides where a value ends by whether the
  next key is a *known argument name*, name normalisation (`functions.read` →
  `read`) against an allowlist, and promotion of a tool call the model wrote
  as plain text into a structured call — all bounded (64k buffer, 96-char
  prefix) and all refusing rather than guessing when the allowlist does not
  resolve.
- **DeepSeek** keeps invalid JSON as text, lets the tool validate, and returns
  `INVALID_ARGS` with path-qualified violations. No repair.

**Taken:** Hermes's coercion and alias seam, OpenClaw's allowlisted name
resolution, and our own fragment removal from 2026-08-31, which already
recognises the one corruption we have observed. Plus the correction the
reading exposed: `parse_arguments` today raises `BackendError` on invalid
JSON, which fails the whole request instead of one call. **Not taken, recorded
as the next lever:** plain-text tool-call promotion. It is the right response
to a server that hands over a call as prose, and no server has done that to us
yet; OpenClaw's implementation is the reference if one does. **Not taken:**
OpenClaw's per-key repairer that decides where a string ends from a list of
known argument names — it is a guess about intent, and our 2026-08-31 rule
stands: remove what provably is not the model's, never add what might be.

### Loops and repeats

- DeepSeek: advisory reminders at 3, 5 and 8 identical repeats; never blocks;
  a new user message clears the count. Excludes `todo_write`.
- Hermes: idempotent vs mutating tool sets; a threshold of 3 identical calls;
  "failure-tolerant" tools (`terminal`, `execute_code`) whose non-zero exit is
  normal work; a landed mutation between two attempts makes the retry a new
  experiment rather than a replay; result hashes; pure decisions, runtime
  chooses warning or halt.
- OpenClaw: hashes `(tool, args, result)`; ignores volatile metadata; a
  post-compaction guard that aborts when compaction did not break the loop.

**Taken:** nothing new now. Our guard — refuse the third identical *failed*
call and end the turn — exists and worked live. Hermes's distinction between a
mutating and an idempotent repeat, and DeepSeek's advisory shape, are the
options for 4.7 if the scenario suite shows the hard stop ending turns that
would have recovered. The `exit_code: 1 is content, not failure` rule in the
design is Hermes's failure-tolerant idea stated as a contract instead of a
list.

### Bounds, spill and pruning

All three bound a result before it reaches the model, and all three keep the
full text somewhere the model can ask for later: DeepSeek's `spill` store with
a locator and a `retrievalHint`, Hermes's three levels (per-tool cap, spill to
`cache/spillover/<id>.txt` with a preview, a 200k-char per-turn aggregate),
OpenClaw's per-tool caps plus an aggregate share of the context window.
DeepSeek's pruner replaces an over-budget result with head, marker and tail
only when compaction is already triggered, and the original stays in the log.

**Taken now:** the per-result backstop in `post_execute` — head, marker, tail;
image count and bytes — because it is the boundary and costs a few lines.
**Deferred to 4.6a:** the aggregate budget and spilling, because they are the
context engine's decisions and the design says so.

### Errors that reach the model

DeepSeek: `Error: <message>`, a structured code kept for callers, and a remedy
appended for the codes that have one (`— re-read the file, then retry`).
Hermes: bounded to 2,048 chars, sanitized of structural tokens, the exception
type named, the traceback to the log. OpenClaw: first line only, 400 chars,
secrets redacted, and a whole file of machinery to stop a hostile error object
escaping.

**Taken:** DeepSeek's message-plus-code, Hermes's sanitizing and the log/model
split. The fence rule is ours: a failure message never contains a fence or a
delimiter token, because a fence inside a value is exactly what broke the
served parser on 2026-08-31 and the model imitates what it reads.

### Filesystem portability

DeepSeek: one `ctx.fs` contract with typed `FsError` codes (`FS_NOT_FOUND`,
`FS_STALE_VERSION`, `FS_AMBIGUOUS_EDIT`, `FS_TOO_LARGE`), three backends
(`fs-local`, `fs-sandbox`, `fs-e2b`), the tools in a separate package, and the
read-before-edit policy in a *third* package attached by events. Hermes: one
`terminal` tool over `BaseEnvironment` with local, Docker, SSH, Modal, Daytona
and Vercel backends. Our profiles differ in nothing a filesystem tool can see
except the root.

**Taken:** DeepSeek's codes as the shape of `fs.*`, and the draft's rule: one
implementation parameterized by root, a `Protocol` at the first real second
implementation, which is the item-5 sandbox. Hermes's environments and
DeepSeek's `ctx.fs` are named in the design as the references for that step.
**Not taken:** the read-before-edit version guard; with one worker per
conversation the race it prevents does not occur here.

### Browser

OpenClaw exposes one `browser` tool with an `action` argument: `open`,
`navigate`, `tabs`, `snapshot`, `screenshot`, `act` (`click`, `type`, `press`,
`hover`, `drag`, `select`, `fill`, `scrollIntoView`, `evaluate`), `requests`,
`errors`, `text`, `emulate`, `dialog`. A **snapshot** is a stable UI tree with
element `ref`s; every action takes a ref, never a CSS selector; `navigate`
returns the new snapshot inline; `evaluate` can be disabled by configuration;
SSRF policy is checked before any navigation and during actions. Hermes's
6,600-line `browser_tool.py` arrived at the same snapshot-and-ref loop.

**Taken:** the session API — snapshot with refs, actions on refs — as the
internal boundary designed now, and observation only as the first exposed
surface, per the human's instruction. **Left to 4.5.5:** whether the exposed
surface is one tool with an action argument or several tools; the product
principle prefers distinct capabilities, and the parser evidence says fewer
nested arguments are safer, and those pull in opposite directions.

### Presentation and telemetry

DeepSeek tools declare `presentCall` / `presentResult` views in a
provider-neutral vocabulary so no UI special-cases a tool name. **Not taken:**
one interface owns a label table today and it is not drifting; the metadata
seam is recorded as the move when a second interface needs it.

DeepSeek's invariant "model-visible means logged" is worth keeping as a
question for 4.6a: our stored history is canonical and the checkpoint is
resumable, and a steered candidate is deliberately neither. The context engine
should say what its projection can and cannot be rebuilt from.

## The queue, and why 4.6 and 4.7 sit where they do

Approved 2026-09-03:

```text
4.5    Tool system                       docs/v2_tool_system.md
4.5.5  Browser capability                session designed whole; observation first
4.6a   Context engine                    unchanged
4.6b   Exact recovery from archive       unchanged
4.7    Restart, resume, scenario suite   unchanged
4.8    ask_user                          was 4.5
4.9    Saying only what was observed     was 4.5.5
```

- **4.6a after the tools**, because it shortens tool results, and shortening
  results that are prose with a prefix means parsing prose and redoing the
  engine after the migration. The draft said this; every reference's pruner
  operates on a typed result.
- **The tools do not touch the database.** The typed failure is checkpointed
  now and stored later, so 4.6a and 4.6b still share the single schema-3
  migration the roadmap holds. That is the answer to "will the tools get in
  the way of memory": no, by construction.
- **4.6b after 4.6a**, because it recovers what compaction wrote, and before
  compaction records exist there is nothing to recover.
- **4.7 after both**, because the suite asserts on harness events — the
  `tool_failed` reason, `context_folded`, the outcomes — and those are what 4.5
  and 4.6 change. Written earlier it is rewritten later. The 832 offline tests
  are the migration's safety net; the live suite fixes the harness once it is
  whole.
- **4.7 before `ask_user` and "only what was observed"**, because both change
  what the model does rather than what the harness does, and without a suite
  there is no way to accept them — which is precisely how 4.3's acceptance was
  left undemonstrated.
- **Browser between the tools and the context engine** (the human chose this
  over placing it after 4.7): it is the first second family on the new
  contract, and its snapshots are the largest results the engine will have to
  shorten, so their shape should exist before the engine is designed around
  results.

## What this changes for the open issues

Closed by construction in 4.5: ISS-0007 (the reason travels with the failure).
Made possible by 4.5.5: ISS-0008 (nothing exercises a generated page). ISS-0005
and ISS-0006 are already fixed and get their codes. **Unchanged** by any of
this: ISS-0001 stays mitigated — the parser is upstream and the runtime now
promises to survive it rather than fix it; ISS-0004 and ISS-0009 are model and
stream behaviour and belong to 4.9 and to the throttling item. Stating this so
the migration is not later judged against defects it never claimed.

## Ledger

- No code, configuration or command changed. No worker, endpoint or GPU was
  started. The reference sources were read over GitHub's public API and raw
  file endpoint; nothing was downloaded into the repository.
- Written: `docs/v2_tool_system.md`, this report, the queue in `ROADMAP.md`,
  one entry in `DECISIONS.md`, one line in `ISSUES.md` ISS-0001.
- Checks: documentation only; the offline suite was not run.
- Next gate: the human's explicit signal to start the 4.5 implementation. It
  is a large cohesive batch across `app/tools/`, `app/models/`, `app/agent/`,
  both interfaces and the tests, which under `AGENTS.md` is a Claude route
  through Orca unless the human keeps it in Codex or in a direct session.
