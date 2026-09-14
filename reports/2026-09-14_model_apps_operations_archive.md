# Model Apps: the operational detail, moved out of OPERATIONS_MAP (2026-09-14)

**Owners:** `deploy/modal/model_app.py` (`assistant-llm-v2`: Gemma 4 12B
QAT, A10, vLLM 0.26.0, ceiling 65,536, utilization 0.80, image=4 audio=1,
snapshot around a slept vLLM, proxy auth), `model_app_qwen.py`
(`assistant-llm-qwen`: Qwen3.8-27B FP8, L40S, 131,072, 0.90, `max_num_seqs`
16, vLLM 0.28.0 / transformers 5.15.0, prefix caching on, thinking off by
default, `qwen3_xml`/`qwen3` parsers), `model_app_qwen_int4.py`
(`assistant-llm-qwen-int4`: RedHatAI INT4, A100-40GB, the rest by import;
restore 20–31 s). Volumes `assistant-hf-cache` (weights) and
`assistant-vllm-cache` (compile cache; the Qwen Apps do not mount it on the
server, ISS-0047; `VLLM_USE_AOT_COMPILE=0`, ISS-0050).

The order for a Qwen App, each step a gate: `fetch_weights` (CPU) →
`preflight` (CPU, `fits` checks the ceiling against the pool) → optional
`dry_run` (one GPU boot, `retries=0`, ISS-0049) → `modal deploy` → the first
request creates the snapshot. `MAX_MODEL_LEN` is set once, high; the dial is
the application's context budget. `deploy/modal/autoscale.py --window N`
changes the running idle window (12 s default) without a deploy; a deploy
resets it. Wake measurement: `scripts/measure_endpoint_wake.py --url … --model
<served name>`. Engine baseline: `tools/vllm_baseline.py --run`. All of these
start a GPU container.
