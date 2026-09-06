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

## 7. Open

- The plain `MODEL_ENDPOINT` and `MODEL_NAME` lines are no longer in
  `.env` (the sync reported them absent), so `MODEL=` empty would now point
  the deployment at the client's defaults, not at INT4; the INT4 set should
  be written as `MODEL_INT4_*` before switching back.
- G on GLM: the cap or the effort, one run each, its own gate; and
  ISS-0055 in the harness first.
- The other three candidates when CometAPI lists them; DeepSeek needs
  `{"thinking": {"type": "disabled"}}` in the set's extra body.
- `reasoning_tokens` into the telemetry, so a slow call can be read.
