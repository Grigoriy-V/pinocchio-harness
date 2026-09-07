# A hosted model through CometAPI: GLM-5.3-Flash

**Date:** 2026-09-06
**Status:** the first paid check done; the scenario suite not yet run.
Roadmap: the "API-served model" experiment under Not started, and item 13.

## 1. What was built, no GPU

- `MODEL_EXTRA_BODY`: JSON merged into every request body last (commit
  710523c). GLM through CometAPI gets `{"tool_stream": true}`.
- A chosen `AGENT_CONTEXT_TOKENS` stands when the server reports no
  context length (same commit). CometAPI's `/v1/models` lists 546 models
  and no `max_model_len`, so without this a hosted model never folded.
- Model sets (commit 61f4ceb): `MODEL=comet` reads `MODEL_COMET_*` and
  `AGENT_COMET_CONTEXT_TOKENS`; the plain `MODEL_*` lines stay the unnamed
  set; the secret sync publishes every set. Switching the deployment is one
  line and a control-plane redeploy; no key overwrites another.

Four candidates named by the human: `glm-5.3-flash`, `qwen3.8-flash-next`
(CometAPI: "coming soon"), `deepseek-v4-flash-vision-exp`,
`gemini-3.1-flash-lite-preview`. GLM first.

## 2. What the documentation says

- Z.ai: GLM-5.3-Flash $0.075 / $0.015 cached / $0.25 per 1M (promotional);
  context cache automatic, reported as `prompt_tokens_details.cached_tokens`;
  **thinking cannot be disabled** (`thinking.type` only `enabled`);
  `tool_stream: true` recommended for streamed tool calls.
- CometAPI: 0.8x the official price ($0.06 / $0.20 on the model page), the
  usage object carries `cached_tokens` "served from the provider prompt
  cache"; nothing anywhere about a cached-input price. The narrative-game
  repository's two probes (Gemini, 2026-08) never saw a cached count.

## 3. Scenario B, the first paid call

Run `deployed-1094dba2-20`, 09:13 UTC, `MODEL=comet`, `AGENT_COMET_CONTEXT_TOKENS=131072`.

| call | input | cached | output | first token | duration |
|---|---|---|---|---|---|
| 1 (tool call) | 4,488 | 0 | 19 | — | 13.9 s |
| 2 (answer) | 4,513 | 4,480 | 12 | 17.0 s | 17.1 s |

B passed: `read_file` ran, the passphrase in the answer; 39.7 s for the
turn against 4.1 s on INT4 (warm). Streaming and the OpenAI tool-call
shape work through CometAPI as they are.

**Billing.** The `pinocchio` key went from $0 to **$0.000332** for the two
requests. Two arithmetics on the page prices:

| | input | cached | output | total |
|---|---|---|---|---|
| no cache discount | 9,001 × $0.06 | — | 31 × $0.20 | $0.000546 |
| cache at 0.2x ($0.012) | 4,521 × $0.06 | 4,480 × $0.012 | 31 × $0.20 | **$0.000331** |

The bill matches the second to a microdollar: **CometAPI passes GLM's
cache hits through and discounts them**, contrary to the suspicion.
Reasoning tokens were either absent or not billed: 31 output tokens billed
as 31.

**What B says about speed.** 14 s and 17 s to the first token on 4.5k-token
requests is thinking that cannot be turned off, or CometAPI's own queue;
one scenario cannot tell. The suite will: INT4's numbers for the same
scenarios are in `reports/2026-09-05_qwen38_second_model.md` §10–§13.

## 4. The rest of the suite on GLM: 13 of 15

Batch `deployed-2491a3c4-*`, 09:18–09:37 UTC, one call after B, the key
`pinocchio` from $0.000332 to **$0.008424**: $0.0081 for fifteen turns.
INT4 on 2026-09-05 (§10–§13 of the Qwen report) for comparison, warm
where it was warm.

| Scenario | GLM, s | INT4, s | Calls | Result on GLM |
|---|---|---|---|---|
| A | 17.1 | 26.7 (restore) | 1m/0t | pass |
| B | 39.7 | 4.1 | 2m/1t | pass |
| C | 37.7 | 6.6 | 3m/2t | pass |
| D | 40.3 | 22.2 | 3m/2t | pass, stopped |
| E | 39.3 | 5.9 | 2m/1t | pass |
| F | 67.7 | 11.3 | 3m/3t | pass, `inspect_page`, a ref read |
| G | 207.8 | 347.6 (fail) | 1m/0t | **fail: one call, 8,192 output tokens, `length`, nothing said** |
| H | 29.4 | 40.8 (restore) | 2m/1t | pass |
| I | 27.7 | 4.0 | 2m/1t | pass |
| K | 141.9 | 20.7 | 5m/5t | pass, folded mid-turn |
| J | 77.8 | 3.3 | 2m/1t | pass, resumed at the model node |
| O | 187.1 | 15.3 | 3m/2t | pass |
| P | 51.0 | 21.6 | 4m/4t | **fail: the PDF was checked with `pdftotext`, not looked at** (ISS-0040's route check) |
| Q | 24.7 | 8.1 | 2m/1t | pass |
| R | 49.5 | 26.6 | 5m/5t | pass, the chart looked at and sent |
| S | 35.2 | 14.4 | 5m/5t | pass |

**What passed says.** Every loop property held through a hosted model:
the stop, the failing tool, the fold, the killed worker resumed, the
timeout, streaming, tool calls in the OpenAI shape, `send_file`, the
renderer. Nothing in the harness needed a change for CometAPI beyond
§1. Cache hits arrived on every call after a turn's first (4,416–5,632
of ~4,500–5,800 tokens).

**What the time says.** GLM is 3–20x slower per turn than a warm INT4,
without a cold start: 6–25 s to the first token on every call, and long
tails where the model reasons (O: 187 s for three calls and 264 output
tokens billed; K: 142 s). Reasoning that ends in an answer is not counted
in `output_tokens` and not billed; reasoning that spends the whole cap is
(G, below). The client does not record
`completion_tokens_details.reasoning_tokens`, so the split between
thinking and waiting is not in the telemetry yet.

**G.** One call, 4,597 in, 8,192 out at `finish_reason=length`, no tool
call, `content` empty, 204 s; the turn ended `answer_delivered` with
"(nothing said)". The same shape as the FP8 App's first G with thinking
at `low` (Qwen report §5): the reasoning spends the whole output cap
before the first tool call. GLM-5.3-Flash cannot turn thinking off, so on
this model G is a question of the cap (`MODEL_MAX_TOKENS` 8,192 — a
larger cap or a `reasoning_effort` field through `MODEL_EXTRA_BODY`, if
CometAPI passes it) and, separately, of the harness: a call that ends at
the cap with nothing said is not an answer and must not be delivered as
one (ISS-0055).

**Cost.** $0.0084 for sixteen turns against roughly $0.35 of derived
A100 time for the same sixteen on INT4 (the suite's own upper bound), a
factor of ~40; and no restore, no idle window, no ISS-0044/ISS-0054.

## 5. Thinking off, by probe and by scenario

Three direct streamed calls (one-word question, 25 tokens in):

| body | total | reasoning tokens | output | answer |
|---|---|---|---|---|
| as is | 2.6 s | 22 | 25 | Yellow |
| `thinking: {"type": "disabled"}` | 3.0 s | 0 | 5 | Yellow |
| `reasoning_effort: "low"` | 1.8 s | 0 | 3 | Yellow |

Both flags reach the model through CometAPI and switch reasoning off,
against Z.ai's own note that GLM-5.3-Flash cannot; CometAPI streams
`reasoning_content` and reports `completion_tokens_details.reasoning_tokens`,
and `completion_tokens` includes the reasoning. The set's extra body is
now `{"tool_stream": true, "thinking": {"type": "disabled"}}`.

B, G, K, O again with it (`deployed-553dcc1e-*`, $0.0043 for four turns):

| Scenario | thinking on | thinking off | Calls | Result |
|---|---|---|---|---|
| B | 39.7 | 111.3 | 2m/1t | pass; call 2: 90 s to the first token for 15 output tokens |
| G | 207.8 | 224.7 | 1m/0t | **fail again: 8,192 out at `length`, nothing said** |
| K | 141.9 | 135.4 | 5m/5t | pass; call 1: 1,141 output tokens in 71 s |
| O | 187.1 | 100.2 | 3m/2t | pass; call 1: 104 output tokens in 59 s |

Two readings, kept apart:

- **The time is CometAPI's, not the thinking's.** 90 s to the first
  token for a 15-token answer, 59 s for 104 tokens: with reasoning off
  the calls are as slow as before, and the delay is before the first
  token. Wall time on this route is the provider's queue, and it varies
  by a factor of five between runs of the same scenario.
- **G's 8,192 tokens are still unaccounted for.** No text, no tool call
  visible to the parser, not a cut call (that would be a `tool_failed`
  with `output_cut`), so either the flag is ignored when tools are
  offered and the tokens were reasoning again, or the model wrote 8k
  tokens of something the stream parser did not keep. The client did not
  record `reasoning_tokens` at the time; it does now (§6), so the next G
  answers this from telemetry instead of a guess.

## 6. Harness changes from this day's evidence

- `Usage.reasoning_tokens` read from `completion_tokens_details`, carried
  into `model_finished` and the run inspector ("reasoning N" beside the
  call), so thinking time and queue time are told apart.
- ISS-0055: an empty completion at `finish_reason=length` now ends the
  turn with a message that the cap was spent before a visible word,
  instead of "(nothing said)" recorded as a delivered answer.

## 7. G with the raw stream kept: what the model actually does

`MODEL_DUMP_DIR` on (`/workspaces/.dumps`), the redeploy carrying ISS-0055
and `reasoning_tokens`; run `deployed-90000c35-70`, 393.6 s, 8 model
calls, 6 tool calls, ended by the seconds budget. The route this time was
the one the brief names — `set_goal`, three `write_file`, `inspect_page`,
`read_file` of the screenshot — and then the budget, before `send_file`.
The 8,192-token silence of the two earlier runs did not recur; the dumps
are on for when it does.

What the raw `.sse` files say that the telemetry could not:

- **CometAPI does not stream GLM.** Every chunk of a call carries the same
  `created` second, and the first arrives 96 s (call 6), 75 s (call 7),
  13 s (call 8) after the request: the whole response is generated
  server-side and delivered at once. "Time to first token" on this route
  is the whole call; our stream retry logic and the person's streamed
  answer gain nothing.
- **Call 6: 96 s for 21 output tokens, 0 reasoning**, a `read_file` of the
  screenshot on a 10.5k-token prompt with 4.5k cached. That is the
  provider's latency, nothing the model did.
- **`thinking: disabled` is not fully honoured with tools offered.**
  Call 7 carried 2,717 characters of `reasoning_content` (596 reasoning
  tokens), call 8 858 characters; the probe without tools had none.
- **Why the model reaches for a browser of its own** (call 7's reasoning,
  verbatim): "The inspect_page tool doesn't let me click. Hmm —
  inspect_page returns refs but I have no click tool. I can't interact
  with the page directly via tools." Then `run_command` with `node --check`
  and `which chromium`. This is the same route INT4 took on 2026-09-05
  (`npm install puppeteer`, `apt-get`): a model asked to make a working
  app wants to press its buttons, the toolbox returns refs it cannot
  use, and the shell is the only way left. The references offer a click
  and a type on the same page the screenshot came from; ours offers the
  refs and no verb. Belongs with items 11–12, as the reason the route
  exists rather than a case to patch.
- **Call 8, after the budget** (reasoning, verbatim): "I should send the
  screenshot and files — but I can't call send_file anymore." The model
  answered honestly with what it had. The turn's 300 s went 381 s to
  CometAPI's queue and 2 s to tools; on this route the seconds budget
  measures the provider, not the work.

## 8. Gemini 3.1 Flash-Lite, the same set with one line changed

`MODEL_COMET_NAME=gemini-3.1-flash-lite` (the GLM extra body left in
place; Gemini ignores it), the whole suite in one batch
(`deployed-cac14ccc-*`, 11:29–11:37 UTC): **14 of 16 pass**, $0.0585
for sixteen turns.

| Scenario | Gemini, s | GLM, s | INT4 warm, s | Result |
|---|---|---|---|---|
| A | 7.5 | 17.1 | (26.7) | pass |
| B | 8.3 | 39.7 | 4.1 | pass |
| C | 10.8 | 37.7 | 6.6 | pass |
| D | 8.4 | 40.3 | 22.2 | pass |
| E | 12.9 | 39.3 | 5.9 | **fail: the repeat guard ended the turn** (the edit retried unchanged before the read) |
| F | 12.0 | 67.7 | 11.3 | pass |
| G | 39.5 | fail | fail | **pass, all eight checks**: three files, `inspect_page`, files and screenshot sent, 7 calls |
| H | 10.3 | 29.4 | (40.8) | pass |
| I | 9.0 | 27.7 | 4.0 | pass |
| K | 27.0 | 141.9 | 20.7 | pass |
| J | 6.8 | 77.8 | 3.3 | pass |
| O | 14.3 | 187.1 | 15.3 | pass |
| P | 45.7 | 51.0 | 21.6 | **fail: not looked at** (ISS-0040, same as GLM) |
| Q | 22.5 | 24.7 | 8.1 | pass |
| R | 36.6 | 49.5 | 26.6 | pass |
| S | 36.0 | 35.2 | 14.4 | pass |

Per call 1.5–3 s, streamed; the turn times are close to a warm INT4's
and there is no cold start. G passed on the first try with the route the
brief names, which neither INT4 nor GLM managed in five runs between them.

**Cache and bill.** 60 calls, 276k input tokens, 2 cache hits (8k tokens)
— Gemini's implicit cache is best-effort and through CometAPI it rarely
lands, as the narrative-game probes found. The bill, $0.0585, equals the
page prices with no cache discount ($0.0593 at $0.20/$1.20): what little
was cached was not discounted, or the rounding ate it. Seven times GLM's
suite ($0.0081) and a sixth of INT4's derived A100 time.

## 9. Neighbours of Gemini 3.1 Flash-Lite on price, speed and quality

The human's reading after §8: Gemini 3.1 Flash-Lite leads on
price/quality/speed, speed above all. What else sits near it (prices
per 1M in/out; "on CometAPI" means listed in `/v1/models` on 2026-09-06):

| Model | In / out | Vision | Tools | Speed (published) | Where | Note |
|---|---|---|---|---|---|---|
| Gemini 3.5 Flash-Lite | $0.24 / $2.02 | yes | yes | same family; TTFT not published | CometAPI | Terminal-Bench 2.1 54%; the one-line upgrade to try |
| Mistral Small 4 (2026-03) | $0.075 / $0.20 (provider-dependent) | yes | yes | "40% faster than Small 3", 262k | not on CometAPI; Mistral direct, OpenAI-compatible | cheapest credible neighbour; needs its own set and key |
| Grok 4 Fast non-reasoning | $0.20 / $0.50 | yes | yes | 2M context; AA index 17 | not on CometAPI (only grok-4.20 non-reasoning, $1/$2, text) | xAI direct |
| GPT-5.4 mini | $0.60 / $3.60 | yes | yes, `reasoning_effort: none` | ~180–190 tok/s | CometAPI | 3x the price |
| GPT-5.4 nano | $0.16 / $1.00 | **no** | basic | ~1.6 s avg | CometAPI | text-only, out |
| Doubao Seed 2.0 Lite | $0.08 / $0.48 | yes | yes | not published | CometAPI | Chinese route through CometAPI: expect GLM/Qwen's queue |
| Claude Haiku 4.5 | $0.80 / $4.00 | yes | yes, computer use | "200+ tok/s" | CometAPI | best tool discipline in the class, 4x the price |
| Llama 4 Scout on Groq | $0.11 / $0.34 | yes | yes | 448 tok/s | Groq direct | open model; tool-calling quality unmeasured here |

Published numbers for the leader itself: Gemini 3.1 Flash-Lite ~362
tok/s, BFCL v3 76.5%, MMLU-Pro 78%.

What would change the picture, each one set and one key, B and G to
compare: Gemini 3.5 Flash-Lite (one line on CometAPI); Mistral Small 4
direct; Grok 4 Fast direct. Google AI Studio direct instead of CometAPI
is worth one comparison too: same price plus 25%, but the implicit cache
lands there and the latency has no reseller in front.

Sources: CometAPI model pages; artificialanalysis.ai (Gemini 3.1
Flash-Lite providers, Grok 4 Fast); openrouter.ai (Mistral Small 4);
layerlens.ai (Gemini 3.1 Flash-Lite benchmarks); cloudzero.com and
artificialanalysis.ai (Llama 4 Scout on Groq).

## 10. The human's tiers, and where GLM could be faster

Decided 2026-09-06 (DECISIONS): default Gemini 3.1 Flash-Lite, stronger
Gemini 3.5 Flash-Lite, low-price GLM 5.3 Flash; a native-format adapter
for Gemini's cache; GLM from another provider.

GLM 5.3 Flash by provider, OpenRouter's own measurements (input/output
per 1M, latency to first token, throughput, cached input):

| Provider | In / out | Latency | tok/s | Cached in |
|---|---|---|---|---|
| Baseten | $0.15 / $0.50 | 0.53 s | 150 | $0.03 |
| Modal | $0.15 / $0.50 | 0.47 s | 118 | $0.03 |
| Makora | $0.14 / $0.47 | 0.46 s | 112 | $0.024 |
| Fireworks | $0.15 / $0.50 | 1.80 s | 71 | $0.03 |
| DeepInfra | $0.075 / $0.25 | 1.78 s | 41 | $0.015 |
| Z.ai direct | $0.075 / $0.25 | 4.59 s | 28 | $0.015 |
| CometAPI, measured here | $0.06 / $0.20 | 13–100 s, whole | — | discounted |

CometAPI's price is the lowest and its delivery the slowest by an order
of magnitude. OpenRouter is one OpenAI-compatible key over all of these,
with the provider chosen per request (`provider` in the body, which
`MODEL_EXTRA_BODY` can carry), so the low-price tier can be one set
pointed at OpenRouter with Baseten or DeepInfra named.

## 11. OpenRouter: the same two models, B and C

The `OR` set (`https://openrouter.ai/api/v1`), provider named in the
extra body. Four turns, the raw dumps say which host served each call and
what OpenRouter charged (`usage.cost`).

| | Gemini 3.1 Flash-Lite, OR | Gemini, CometAPI | GLM 5.3 Flash, OR (Novita fp8) | GLM, CometAPI |
|---|---|---|---|---|
| B | 7.0 s, 3 calls | 8.3 s | 14.7 s, 2 calls | 39.7 / 111 s |
| C | 5.5 s, 3 calls | 10.8 s | 22.6 s, 3 calls | 37.7 s |
| per call | 0.5–1.0 s, streamed | 1.5–3 s | 4.5–7.7 s, mostly whole | 13–100 s |
| cache | 0 of 6 | 2 of 60 | 3 of 3 after the first, discounted (call cost $0.000076 against $0.00035 uncached) | discounted |
| served by | "Google AI Studio", billed at the Flex rate ($0.128/M observed) | — | Novita | — |
| thinking | none | none | 5–75 reasoning tokens with `thinking: disabled` | 0–596 |

Gemini through OpenRouter is 2–3x faster per call than through CometAPI
and half the price on the Flex rate; the implicit cache still did not
land in six calls (explicit `cache_control` breakpoints are the next
lever, item 13b). GLM through Novita is 3–8x faster than through CometAPI
and caches every call after the first, at $0.075 in. The `provider.order`
slugs are `google-ai-studio/flex`, `google-ai-studio`, `novita/fp8`,
`modal/fp8` (the endpoints API); `.env` still says
`google-ai-studio-flex`, which matches nothing — the published secret
carries the corrected line until `.env` is fixed.

## 12. GLM 5.3 Flash at the four $0.075 hosts, B's real first request twice each

`reasoning_effort: low` with `thinking: disabled` (together they leave no
reasoning tokens with tools, where `disabled` alone left 5–75); the
worker's own B request (system, tools, the question) sent twice to each
host with `allow_fallbacks: false`, the second to see the cache.

| Host | Quant | 1st call | 2nd call | Cached on 2nd | Cost per call |
|---|---|---|---|---|---|
| Z.ai | fp8 | 4.8 s | 4.9 s | 4,480 of 4,504 | $0.000072 |
| Novita | fp8 | 4.9 s | 5.7 s | 4,480 of 4,488 | $0.000071 |
| GMICloud | fp8 | 6.3 s | 6.1 s | hit on the 1st, miss on the 2nd | $0.00007 / $0.00034 |
| DeepInfra | fp4 | 1.3 s | 1.1 s | 0, both | $0.00034 |

DeepInfra is four times faster and, with no cache landing, five times
the price per call; Z.ai and Novita cache every repeat at ~5 s per call;
GMICloud caches unreliably. The earlier "DeepInfra" B (`c67da908`) was
in fact served by GMICloud through the fallback: `allow_fallbacks: true`
falls to any host, not the next in `order`.

## 13. After the API model: what changed, and a proposal for the order of work

Written 2026-09-07 on the human's word, from the day's live sessions
(calculator, racing game and its two fixes, Blender) and the suite. The
model is now hosted, cheap and reasonably quick; what was built and
tuned for a GPU App billed by the second either no longer applies or
now gets in the way. A proposal, not a plan, until approved.

**What loses priority (built for the GPU App):**

- The cold start and the idle window: ISS-0044, ISS-0054, item 6's
  adaptive window and keep-warm. No restore exists on this route.
- The model Apps' boots and snapshots: ISS-0047, ISS-0049, ISS-0050, MTP,
  the wake probe, `dry_run`. The Apps stay deployed as sets; nothing
  about them is scheduled.
- The GPU-derived cost in `show_run` and the suite ("gpu derived …"):
  now a number that means nothing; OpenRouter reports `usage.cost` per
  call, which is the real figure.
- Model latency work: the suite showed the harness's own seconds now
  outweigh the model's on a short turn.

**What now gets in the way (built for a GPU minute, wrong for a cheap
call):**

- `turn_max_seconds` 300 counts tool time and the provider's queue and
  ends working turns (ISS-0057: Blender, the racing test). The budget's
  currency must change (calls or tokens, or the model's own seconds).
- Folding by message count: `summarize_after` 60 messages folds a thread
  every ~15 tool-heavy turns while requests sit at 13–25k of a 131k
  budget. On a server that reports no window the count rule is the one
  that binds; with `AGENT_<SET>_CONTEXT_TOKENS` set, the size rule can be
  the only one and the count raised or dropped.
- The seconds-based stream retry and redirect handling for Modal's
  edge: harmless here, but not the path that matters any more.

**What rises (seen this day, every one the harness's):**

1. The tools' contracts and the brief's wording (items 11–12): "fixed"
   claimed from a screenshot (ISS-0008 again), "screenshot above"
   without a send (ISS-0010 again), a plan skipped because the brief said
   "when you can hold the whole of it in your head" (the human: no such
   wording; a brief is a literal instruction).
2. A browser the model can act in: click, type, press and evaluate on the
   page it made, through the renderer that already exists (the human's
   choice over a browser in the command image). The model found and
   fixed the racing bug only once it had those verbs through puppeteer.
3. The command environment: temp and caches on the Volume (ISS-0058),
   what persists between commands (ISS-0053).
4. The harness's own seconds inside a turn (ISS-0056): name every gap in
   the timeline, then remove the Volume commits that change nothing.
5. The suite's reporting (item 10), so all of the above is measured by
   outcome and by the split of time.
6. Item 13's remainder: the model chosen from Telegram, Gemini's
   `cache_control`.

**Proposed order:** the two configuration changes first (the budget's
currency, the fold rule), one day, because they break live turns today;
then 1 and 2 together (the contract, the browser verbs, the brief's
literal wording); then 3 and 4; then 5; 6 as it comes. Items 8 (plan
and goal) and the GPU Apps wait.

## 14. Open

- The plain `MODEL_ENDPOINT` and `MODEL_NAME` lines are no longer in
  `.env` (the sync reported them absent), so `MODEL=` empty would now point
  the deployment at the client's defaults, not at INT4; the INT4 set should
  be written as `MODEL_INT4_*` before switching back.
- G on GLM: the cap or the effort, one run each, its own gate; and
  ISS-0055 in the harness first.
- The other three candidates when CometAPI lists them; DeepSeek needs
  `{"thinking": {"type": "disabled"}}` in the set's extra body.
- `reasoning_tokens` into the telemetry, so a slow call can be read.
