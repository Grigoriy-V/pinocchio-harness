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

## 4. Open

- The plain `MODEL_ENDPOINT` and `MODEL_NAME` lines are no longer in
  `.env` (the sync reported them absent), so `MODEL=` empty would now point
  the deployment at the client's defaults, not at INT4; the INT4 set should
  be written as `MODEL_INT4_*` before switching back.
- Next, each its own gate: the remaining scenarios on GLM; then the same
  for the other three candidates when CometAPI lists them.
