# Item 14: the turn's bounds, the context budget and the provider — research before code

2026-09-07. No paid call was made for this report: the OpenRouter endpoint
list is public, and the deployed call dumps (`MODEL_OR_DUMP_DIR`,
`/workspaces/.dumps`) were read from the Volume with `modal volume get`,
which starts nothing.

## 1. The context ceiling: 256k fits

`GET /api/v1/models/z-ai/glm-5.3-flash/endpoints` (no key):

| Provider | context | max output | quant |
|---|---|---|---|
| Novita `novita/fp8` | 1,048,576 | 131,072 | fp8 |
| Z.AI `z-ai/fp8` | 1,048,576 | 131,072 | fp8 |
| DeepInfra `deepinfra/fp4` | 1,048,576 | 131,072 | fp4 |
| GMICloud, Modal, BaseTen, … | 1,048,576 | 131k–943k | fp8 |
| Reka, Io Net | 262,144 | | fp8 |

So `AGENT_OR_CONTEXT_TOKENS=262144` is a quarter of what both chosen
providers serve; nothing has to change on their side. The endpoint list
reports no model-level `context_length`, which is why `/v1/models` gives
the client nothing and the set's own budget stands (`Agent.budget`).

Two costs of a larger budget, both the provider's, not ours: a request that
is not cached is paid per token (a 100k prompt at $0.06/M is $0.006, ten
times today's typical call), and the prefix cache decides most of the
price, see §2.

## 2. The provider: what the deployed dumps say

89 deployed calls of 2026-09-06 with `order: ["novita/fp8", "z-ai/fp8"],
allow_fallbacks: false`:

- 78 answered by Novita, 11 by Z.AI. No error, no non-200 response in any
  dump, so every switch happened inside OpenRouter before the first byte;
  the dump cannot say why (a 429 or 5xx from Novita that OpenRouter ate, or
  a slow first byte). OpenRouter's documentation says only that with an
  order it "proceeds to other providers if none are operational" and does
  not define operational (`docs/features/provider-routing`).
- **Every switch to Z.AI lost the cache**: `cached 0` on all 11 Z.AI calls
  whose prefix Novita had cached the call before. The calls cost
  $0.0007–$0.0015 against $0.0002–$0.0004 for the cached Novita call around
  them, four to five times. The Novita call after a Z.AI call sometimes
  paid again too (`171247`: cached 0 after two Novita calls at 10,688).
- Novita's cache also drops on its own after a pause or a change ahead of
  the history (`170522`: 24,984 in, cached 0, nine seconds after a call
  cached at 24,000). That is not the provider switch; it is for item 18
  and item 13 (a stable prefix), recorded here so it is not blamed on Z.AI.

**Shape that matches the human's rule** ("a fallback only after a retry
shows the provider is really down, never because it answered slowly"):

1. `MODEL_OR_EXTRA_BODY` carries `order: ["novita/fp8"]`,
   `allow_fallbacks: false`. OpenRouter then never moves the call.
2. Our client already retries a refused connection and a transient status
   (408, 409, 425, 429, 5xx) before the first chunk, `MODEL_RETRIES` 2 with
   backoff, and never a timeout (ISS-0044). A provider that is down comes
   back as one of those.
3. New, small: when every retry on the first set of providers fails, the
   client sends the request once more with the fallback order
   (`MODEL_OR_FALLBACK_PROVIDERS`, or a second `order` list inside the same
   extra body, whichever reads better in `.env`), and the trace records
   `provider_fallback`. A slow provider is never a reason: only the same
   failures that already trigger a retry.

Cost of the shape: a Novita outage costs the retries' backoff (about 1.5 s)
before Z.AI is tried, once per call. Benefit: the cache is lost only when
Novita is actually gone.

## 3. The turn's bounds: from a ceiling to a watchdog

Today (`TurnBudget` in `app/agent/graph.py`): `max_steps` 12,
`max_tool_calls` 24, `max_seconds` 300, all read in the `tools` node before
a batch runs; a crossed limit halts the batch, offers no tools, and asks the
model once more for the answer. `spent_seconds` is accumulated by the nodes:
the model node adds its own call, the tools node adds the batch, so tool
time is spent as if it were the model's (ISS-0057).

The human's rule: no step or call ceiling (it ends work that is going
well); time is a watchdog, not a cutter: past a set time the harness asks
the model whether all is well and it should go on; an answer means the
model decides; no answer means the system hung and the turn ends with a
message to the person.

Where this fits the loop, with what already exists:

- **The question is a steering.** `Steering(instruction, source)` in
  `app/agent/stopping.py` already carries an instruction into one more
  model step, framed as `Turn control (not from the user): …`. Today it is
  asked only for a result that would end the turn. The watchdog asks it in
  the other place, before a batch of tools, once `spent_seconds` passes
  `AGENT_TURN_CHECK_SECONDS`: the batch is held, the model gets the frame
  "this turn has been running for N minutes; say in one line whether you
  are making progress and what is left, then continue or stop" together
  with the tools still offered. The model's next completion is the answer:
  a tool call or text means go on; the check is asked again after another
  interval, not on every step.
- **No answer** is a model call that does not return. That is not a case
  the loop can observe from inside: the model call has `MODEL_TIMEOUT` 600
  s for its first byte and the httpx read timeout after it, and a timeout
  raises `BackendError`, which already fails the turn with a message. So
  "no answer, the system hung" is covered by the existing timeout on the
  check's own call; the watchdog adds nothing there except that the check
  is a small call rather than a working one.
- **A hung tool** is the case the model check cannot reach: the tools node
  is inside `execute` and the graph is not at a step boundary. Nothing
  today bounds a tool (`Tool.timeout_seconds` exists, no tool sets it,
  ISS-0033); a hung tool holds the worker until Modal kills the container
  at 600 s and the checkpoint resumes the turn. **Draft, for discussion,
  not approved:** a default deadline per tool family (`run_command` has
  the command's own 600 s; documents, browser, history, files a few
  minutes), reported to the model as `timeout` with the tool's name, the
  turn going on. Until decided, the container timeout is the deadline.
- **What goes:** `max_steps`, `max_tool_calls`, the `seconds` limit in
  `exceeded`, `BUDGET_EXHAUSTED` as an ending, `AGENT_TURN_MAX_STEPS`,
  `AGENT_TURN_MAX_TOOL_CALLS`, `AGENT_TURN_MAX_SECONDS`, the `delivers`
  exemption (nothing to be exempt from), the budget answer wording, the
  `turn_budget_exhausted` event and the inspector's "ended by the limit"
  line for it. LangGraph's `recursion_limit` stays as the guard against a
  graph that cannot terminate; it must be raised from its default (25
  nodes) to something a long turn will not meet, say 1,000, since it was
  never the ceiling.
- **What stays:** the repeat guards (identical failure thrice, identical
  success thrice), the stop, the steering seam and its one extension.
- **What is worth keeping about time:** `spent_seconds` and the per-step
  pricing, because the watchdog reads them and telemetry shows them.

Setting: `AGENT_TURN_CHECK_SECONDS`, default 600 (ten minutes of work
before the first question; the Blender turn ran 349 s and was fine), then
every further 600 s. Zero disables the check.

## 4. The fold rule: only when the request would not fit

`fold_older_messages` (`app/context/summary.py`) folds when
`len(pending) > summarize_after` **or** the last request's reported size is
over `max_input_tokens` **or** it is forced. `fitted` in the graph already
folds before the request when the estimate is over budget, oldest exchanges
one at a time; `persist` folds after the turn on the count rule. With the
count gone, the size rule in `fitted` and the exact-but-late size rule in
`persist` remain, and the overflow recovery under both.

Change: `summarize_after` goes from `ContextPolicy`, `AgentSettings`,
`env.example`, `OPERATIONS_MAP.md`; `fold_older_messages` keeps `oversized`
and `force` as its two triggers; `/compact` is `force`. The record's trigger
values become `size`, `forced`, `asked`. Tests that assert a count fold
(`tests/test_context_summary.py` and the graph tests around
`context_folded`) change to size.

At 256k with 0.8 fraction the first fold of a Blender-sized thread (25k per
request) is a long way off; the trade is the per-token price of an uncached
long prompt, §1.

## 5. What to build, in order, and how it is checked

1. `TurnBudget` → `TurnWatch(check_seconds)`; remove the three ceilings and
   their settings; raise `recursion_limit`; the watchdog steering before a
   tool batch; inspector and telemetry wording. Offline tests:
   `tests/test_turn_bounds.py` rewritten around the check (asked after the
   interval, not before; asked again after another interval; a stop still
   wins; a hung check's `BackendError` ends the turn with the message).
2. `summarize_after` removed; fold tests moved to size.
3. `AGENT_OR_CONTEXT_TOKENS=262144` in `.env` (the human edits), published.
4. Client-side provider fallback after retries; `MODEL_OR_EXTRA_BODY` with
   Novita alone; a test with a scripted transport (Novita 503 ×3 → Z.AI
   200; Novita slow → no switch).
5. Deploy `assistant-control`; one live turn with a long command (the
   Blender render), one long thread without a fold, one turn with the
   watchdog interval set low to see the question and the answer in the
   trace. Each is a paid turn; the human names the size.

Records after: ISS-0057 and ISS-0032 closed, ISS-0033 stays open with the
draft, `DECISIONS.md` gets one entry (the turn is bounded by a health
check, not a ceiling; one provider, fallback after failure), the four maps
lose the ceilings.

## 6. Open, for the human

- The watchdog wording and interval (600 s?), and whether the question
  also goes to the person as a status line ("still working, 10 min").
- The tool deadline draft (§3), unresolved on purpose.
- Whether the fallback order is a second list in `EXTRA_BODY` or its own
  setting; I would keep it in the set's lines so one set stays one block.
