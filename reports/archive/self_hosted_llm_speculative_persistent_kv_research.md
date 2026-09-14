# Self-hosted LLM inference research
## Speculative Decoding + Persistent KV Cache

**Date:** 2026-08-28  
**Context:** `local-multimodal-agent`, Gemma 4 12B QAT, vLLM 0.26, Modal A10, aggressive scale-to-zero.

This document records two optimization directions for the self-hosted inference path:

1. **Speculative Decoding** — reduce output/decode latency.
2. **Persistent KV cache** — avoid recomputing long prefixes after a container scales to zero.

The goal is not to optimize for benchmark numbers in isolation. The useful target is a low-QPS interactive assistant where the GPU is normally off, restores quickly when needed, and should extract as much useful work as possible from each paid GPU second.

---

# 1. Current baseline

Current deployment characteristics relevant to this research:

- target model: `google/gemma-4-12B-it-qat-w4a16-ct`;
- inference server: vLLM `0.26.0`;
- GPU: Modal A10;
- `max-model-len = 16,384`;
- measured KV capacity: about **11.06 GiB / 86,664 tokens total**;
- scale-to-zero is working;
- restored cold starts are now roughly single-digit seconds in the good path;
- the workload is low-QPS and latency-sensitive rather than high-throughput batch serving.

The KV number is **total cache capacity**, not the maximum context of one request. With `max-model-len=16,384`, one sequence is still capped at 16k even though enough KV memory exists for several such sequences.

Using the measured cache capacity as a rough proportional estimate:

- ~137 KB KV per cached token;
- ~1.02 GiB for 8k tokens;
- ~2.09 GiB for 16k tokens.

These are useful only as order-of-magnitude numbers. Actual allocation and block behavior depend on model/cache configuration.

---

# 2. Speculative Decoding

## 2.1 What it is

Normal autoregressive decode is sequential:

```text
target model
→ token 1
→ token 2
→ token 3
→ token 4
```

The expensive target model has to perform another decode step before each next token is known.

Speculative decoding adds a cheaper **proposer / draft / speculator** that predicts several future tokens first:

```text
cheap proposer
→ [A, B, C, D]

target model
→ verifies A/B/C/D together

accepted:
A ✓
B ✓
C ✓
D ✗
```

If several proposals are accepted, one expensive target verification advances the sequence by several tokens instead of one.

The important point is that the **target model remains authoritative**. The draft is not allowed to simply replace the target's answer. vLLM's speculative sampling is designed to preserve the target distribution; in greedy mode it is validated against ordinary greedy decoding. Small numerical differences can still exist because GPU floating-point execution is not mathematically exact.

The optimization therefore trades:

```text
extra cheap computation
```

for:

```text
fewer expensive serial target-model decode steps
```

This is especially attractive for **low/medium QPS, latency-focused, memory-bound inference**, which matches the personal-assistant workload much better than a saturated high-throughput server.

---

## 2.2 What Speculative Decoding improves

Primary target:

- **inter-token latency / decode latency**;
- time to finish medium and long answers.

It does **not** primarily solve:

- model cold start;
- weight loading;
- long prompt prefill;
- tool latency;
- network latency.

This distinction matters for the current project:

```text
cold restore
→ prefill
→ decode
```

Memory snapshots already attacked the first part. Persistent KV can attack repeated prefill. Speculative decoding attacks the last part.

These optimizations are complementary rather than replacements for one another.

---

# 3. Speculative Decoding types

vLLM supports several proposer families. They differ in memory cost, setup complexity, draft quality, and expected latency gain.

## 3.1 N-gram speculation

No second neural model is required.

The proposer looks for token sequences already present in the prompt/history and predicts that a matching continuation will repeat.

Example:

```text
earlier context:
return response.status_code

new generation:
return response...

proposal:
status_code
```

### Strengths

- almost no additional VRAM;
- very easy first experiment;
- useful for code, structured output, repeated document text, templates;
- low risk.

### Weaknesses

- limited benefit in free-form conversation where the next text is not repeated;
- usually a modest gain rather than the strongest speculative method.

### Project relevance

**High as a first benchmark because it is cheap to test.**  
It establishes whether the current Telegram/coding workload has enough repeated structure to justify speculation before adding another model.

---

## 3.2 Suffix decoding

Similar goal to n-gram speculation, but uses matching suffix/prefix patterns in available token sequences and can vary speculation depth dynamically.

### Strengths

- no standalone draft model;
- low memory overhead;
- relatively simple;
- can exploit repeated context/history.

### Weaknesses

- expected speedup is usually below strong model-based speculators;
- benefit depends heavily on workload structure.

### Project relevance

Same category as n-gram: a cheap A/B experiment before spending VRAM on a learned proposer.

---

## 3.3 Separate Draft Model

A smaller autoregressive language model proposes tokens for the larger target model.

```text
small draft model
→ proposes 4–8 tokens

Gemma 12B target
→ verifies them
```

### Strengths

- potentially high latency reduction;
- conceptually simple;
- many model families can use a smaller related model.

### Weaknesses

- second model consumes VRAM;
- draft generation itself costs compute;
- a poor draft model can make inference slower rather than faster;
- tokenizer/model compatibility matters;
- high concurrency can change the economics.

The central metric is **acceptance rate**: how many proposed tokens survive target verification.

A fast draft with poor acceptance is not necessarily useful.

### Project relevance

Interesting if a compatible small Gemma-family draft exists and fits comfortably after reducing unnecessary KV capacity.

---

## 3.4 MTP — Multi-Token Prediction

MTP uses a model/head designed specifically to predict future tokens for speculation rather than acting as a normal independent chatbot.

Conceptually:

```text
target state
   ↓
MTP/speculator
   ├→ t+1
   ├→ t+2
   ├→ t+3
   └→ ...
       ↓
target verification
```

vLLM describes MTP as particularly attractive when the target model has native MTP support.

### Gemma 4 relevance

vLLM 0.26 exposes `gemma4_mtp` as a speculative method, and current vLLM contains a Gemma 4 speculator implementation. Current documentation describes the Gemma 4 assistant/speculator as sharing KV cache with the target model across attention layers.

This makes **Gemma 4 MTP the most interesting model-based path to investigate first**, but support must be validated against the exact checkpoint currently deployed:

`google/gemma-4-12B-it-qat-w4a16-ct`

Do not assume that the existence of `gemma4_mtp` means this exact QAT checkpoint, current Modal snapshot lifecycle, and pinned runtime work together without changes.

---

## 3.5 EAGLE / EAGLE3

EAGLE-family methods use a learned lightweight draft model/head based on target-model internal states rather than simply running a generic small chatbot.

### Strengths

- high expected latency gain in vLLM's qualitative guidance;
- optimized specifically for speculation;
- usually better proposal quality than simple n-gram approaches.

### Weaknesses

- requires a compatible EAGLE model/head;
- extra VRAM and deployment complexity;
- may require training or an existing compatible artifact.

### Project relevance

Potentially valuable later. Not the first experiment unless there is a confirmed compatible EAGLE artifact for the exact Gemma checkpoint.

---

## 3.6 MLP speculator

A small learned MLP predicts draft tokens from target-model state.

It is lighter than running a full independent draft LLM and can provide a good latency/memory tradeoff when a compatible speculator exists.

### Project relevance

Same rule as EAGLE: attractive only if a compatible model exists or training one becomes worthwhile.

---

## 3.7 PARD — Parallel Draft Model

Parallel draft methods reduce the latency of generating the proposals themselves by producing draft candidates more efficiently/parallelly than a conventional sequential draft model.

vLLM currently classifies PARD as a high-gain low-QPS method when supported.

### Project relevance

Later optimization, not V1 priority.

---

## 3.8 Dynamic Speculative Decoding

Instead of always proposing a fixed number of tokens, the system adjusts speculation behavior based on runtime conditions or confidence/workload.

This can matter because the best speculation depth changes with:

- prompt type;
- current text;
- model confidence;
- QPS;
- hardware utilization.

### Project relevance

Interesting only after a static speculative setup proves useful. First establish a baseline acceptance-rate/latency gain.

---

# 4. Why spare VRAM can matter

**Free VRAM by itself does not make decode faster.**

Reducing KV cache from 11 GiB to 5 GiB and leaving the remaining memory unused should not materially accelerate single-request decode.

The opportunity is:

```text
large unused concurrency KV budget
↓
reduce KV allocation
↓
use reclaimed VRAM for a draft/speculator
↓
potentially reduce decode latency
```

This is a trade:

```text
more simultaneous long contexts
vs.
faster latency for each interactive request
```

For a small personal assistant, latency may be more valuable than the ability to keep five full 16k sequences resident simultaneously.

For a multi-user service, that decision changes.

---

# 5. Recommended Speculative Decoding experiment order

Do not optimize from theory. Test one variable at a time on the real assistant workload.

## Phase S0 — baseline

Record:

- TTFT;
- full request wall time;
- decode/inter-token latency;
- output tokens;
- GPU active seconds;
- peak VRAM;
- total KV capacity;
- concurrency 1 / 2 / 4;
- actual user-turn and agent-task cost.

Use representative workloads:

1. short Telegram answer;
2. long conversational answer;
3. code/task output;
4. long-context short answer;
5. multi-call agent task.

## Phase S1 — n-gram or suffix

Reason: lowest implementation/memory risk.

Accept only if real Telegram/coding workloads improve.

## Phase S2 — Gemma 4 MTP

Check exact compatibility with:

- Gemma 4 12B QAT checkpoint;
- vLLM 0.26;
- A10;
- 16k context;
- multimodal requests;
- tool calling;
- structured output;
- Modal GPU snapshot/restore.

Measure:

- acceptance rate;
- mean accepted tokens per verification;
- decode latency;
- VRAM delta;
- cold snapshot size/restore behavior;
- concurrency loss.

## Phase S3 — EAGLE / separate draft model

Only if MTP is unavailable, unstable, or clearly inferior.

---

# 6. Persistent KV cache across cold starts

## 6.1 Current behavior

The current Modal GPU snapshot restores a warmed model, not the live KV cache of every user conversation.

Conceptually:

```text
build deployment
→ start vLLM
→ compile / warm
→ sleep
→ create reusable clean snapshot

user A talks
→ user-specific KV appears

container dies
→ user-specific GPU KV disappears

next cold start
→ restore original clean model snapshot
```

So aggressive scale-to-zero currently saves GPU idle cost but sacrifices runtime prefix cache between containers.

Persistent KV is a separate mechanism.

---

# 7. vLLM OffloadingConnector

vLLM 0.26 has an `OffloadingConnector` that extends prefix caching outside GPU memory.

The storage hierarchy can be:

```text
GPU KV
↕
CPU pinned-memory tier
↕
secondary tier
```

Secondary tiers include:

- filesystem;
- S3-compatible object store;
- P2P/RDMA.

Completed KV blocks can be offloaded and promoted back to GPU when needed.

This is exactly the primitive needed to investigate cross-cold-start prefix reuse.

Important properties:

- storage is block based;
- blocks are keyed from content/configuration hashes;
- identical prefixes can therefore reuse identical blocks;
- runs with incompatible model/cache configurations do not silently collide;
- for filesystem and shared object-store use, deterministic cross-process hashing is required; vLLM documents using the same fixed `PYTHONHASHSEED` across instances.

---

# 8. Option A — Persistent KV on Modal Volume v2

## Shape

```text
vLLM
↓
CPU offload tier
↓
OffloadingConnector FS tier
↓
/mnt/kv-cache
↓
Modal Volume v2
```

A new Modal container mounts the same persistent filesystem and can discover blocks created by an earlier vLLM process.

## Why Volume v2 rather than v1

vLLM's FS offload creates many hashed block files under sharded directories.

Modal Volume v1:

- is optimized primarily for write-once/read-many workloads;
- recommends keeping file count below ~50k;
- has a 500k inode limit;
- is less attractive for irregular/random access.

Modal Volume v2:

- is designed for many more files;
- improves random access;
- improves concurrent writes to distinct files;
- supports faster commit/reload behavior;
- is therefore much closer to the access pattern of a block KV cache.

### Important limitation

Volume v2 is currently **Beta**. Modal explicitly says it should not be treated as mission-critical durable storage because data loss is still possible.

For KV cache this is acceptable:

> the KV cache is an optimization, not the source of truth.

Conversation text remains in Postgres/storage. If the cache disappears, the system recomputes prefill.

## Advantages

- simplest architecture inside Modal;
- no separate cloud service;
- normal filesystem API;
- natural first persistent-KV experiment;
- cache lives close to Modal compute.

## Risks / unknowns

- actual random-read latency for many KV blocks;
- commit visibility between containers;
- effective bandwidth for this pattern, not sequential benchmarks;
- storage/file churn;
- cleanup/eviction policy;
- cache growth across users and changing model configs.

## Verdict

**Best first experiment for persistent KV.**

It has the fewest moving pieces and failure is harmless because the cache is rebuildable.

---

# 9. Option B — Persistent KV on S3 / object storage

vLLM 0.26 also exposes an object-store secondary tier through the NIXL OBJ backend.

Shape:

```text
GPU
↕
CPU tier
↕
OffloadingConnector OBJ
↕
S3-compatible bucket
```

The object backend supports shared keys across vLLM instances, again with consistent hashing/configuration.

## Advantages

- genuinely external to the lifecycle of Modal containers;
- easy capacity scaling;
- familiar lifecycle/TTL policies;
- can be shared by many workers/regions if architecture expands;
- does not depend on Modal Volume v2 Beta semantics.

## Weaknesses

KV caching is a latency optimization, while ordinary object storage is optimized more for durability/capacity than very low-latency block retrieval.

Potential costs:

- network round trip;
- many small object requests;
- request-operation charges;
- data-transfer cost;
- potentially slower restore than simply recomputing a short prefix.

The right comparison is not:

```text
S3 is persistent → therefore better
```

but:

```text
time + cost to load needed KV blocks
<
time + cost to recompute the prefix
```

## Best use case

- large expensive prefixes;
- many workers sharing the same prefixes;
- document/system prompts repeatedly reused;
- architecture that must outlive or span Modal-specific storage.

## Verdict

**Second experiment after FS/Volume v2.**  
Potentially more robust as shared infrastructure, but less obviously good for latency.

---

# 10. Option C — Mooncake / dedicated KV store

There are two related but different Mooncake concepts in vLLM.

## 10.1 MooncakeConnector

`MooncakeConnector` is primarily about direct high-speed KV transfer between inference nodes, especially disaggregated prefill/decode:

```text
prefill node
   ↓ KV over high-speed transport/RDMA
decode node
```

This is useful when prefill and decode run on separate workers.

It is **not the simplest answer to "keep one user's cache while my single A10 scales to zero."**

## 10.2 MooncakeStoreConnector

`MooncakeStoreConnector` uses `MooncakeDistributedStore` as a shared KV pool.

It supports:

- CPU/disk offloading;
- cross-instance prefix cache sharing;
- hash-based KV block deduplication;
- single-node and multi-node layouts;
- use alongside disaggregated prefill/decode.

This is much closer to the persistent/shared-KV problem.

Conceptually:

```text
Modal worker A ─┐
                ├→ Mooncake distributed KV pool
Modal worker B ─┤
                └→ CPU / SSD / distributed storage tiers
```

## Advantages

- infrastructure designed specifically for LLM KV rather than generic files/objects;
- cross-instance cache sharing is a first-class use case;
- memory + SSD tiering;
- better future fit for many inference workers;
- can evolve toward disaggregated prefill/decode.

## Weaknesses

- much more infrastructure;
- additional service/control-plane lifecycle;
- deployment and observability complexity;
- RDMA/high-speed networking features matter most at a scale far beyond the current private assistant;
- another distributed system to operate and debug.

## Verdict

**Probably overkill for the current one-A10 / few-user deployment.**

Keep it as the scale-up direction if persistent KV proves valuable and simple FS/object storage becomes the bottleneck.

A dedicated KV layer becomes more rational when there are:

- multiple GPU workers;
- shared heavy prefixes;
- high cache-hit value;
- disaggregated prefill/decode;
- enough traffic that generic storage latency materially limits inference.

---

# 11. A / B / C comparison

| Option | Complexity | Cross-container | Cross-worker | Latency potential | Durability role | Current fit |
|---|---:|---:|---:|---:|---|---|
| **A. Modal Volume v2 + FS** | Low | Yes | Yes, same shared FS | Potentially good | Cache only; Beta | **Best first test** |
| **B. S3 / object store** | Medium | Yes | Yes | Unknown / network-bound | Strong external storage | Second test |
| **C. MooncakeStore** | High | Yes | Yes | Designed for KV workloads | Dedicated cache tier | Later / scale-up |

Do not choose from architecture elegance. Benchmark cache-hit latency against recompute latency.

---

# 12. What should be cached

Persistent KV is most valuable for **large, stable prefixes**.

Good candidates:

```text
system prompt
+ capability/tool descriptions
+ stable user memory
+ large document prefix
+ long conversation prefix
```

Especially:

```text
same PDF
→ question 1
→ question 2
→ question 3
```

Less useful:

- tiny prompts;
- rapidly changing prefixes;
- one-off conversations;
- prompts where storage lookup is slower than prefill.

KV should remain disposable. Canonical state is still:

```text
messages / documents / memory
→ Postgres + file storage
```

not:

```text
KV cache
```

---

# 13. Persistent KV and aggressive scale-to-zero

These two features complement each other.

Without persistent KV:

```text
user pauses
→ GPU dies
→ next request restores model
→ long history prefilled again
```

With persistent KV:

```text
user pauses
→ GPU dies

next request
→ restore model snapshot
→ recover matching KV prefix
→ prefill only uncached tail
→ decode
```

This could make a 2-second or similarly aggressive scaledown policy much more attractive for long conversations.

The actual break-even depends on:

```text
KV lookup + transfer time
vs.
prefix recomputation time
```

For the current model, an 8k prefix is roughly ~1 GiB of KV by the measured cache ratio. That is large enough that storage transfer performance matters. Benchmark actual block loading; do not infer performance from nominal storage bandwidth.

---

# 14. Interaction between Persistent KV and Speculative Decoding

These optimize different parts of a turn:

```text
restored cold start
      ↓
persistent KV hit
      ↓
less prefill work
      ↓
speculative decoding
      ↓
fewer serial decode steps
```

Ideal eventual low-QPS path:

```text
GPU mostly off
↓
snapshot restore
↓
load reusable conversation/document KV
↓
small new prefill
↓
speculative decode
↓
answer
↓
adaptive short scaledown
```

This is more interesting than treating self-hosting as merely a cheaper replacement for a token-priced API: it uses control over the inference stack to optimize the actual assistant workload.

---

# 15. Recommended research sequence

## Experiment 1 — Speculative Decode, cheap path

Baseline vs:

1. n-gram;
2. suffix.

No other variables changed.

Success metric: lower user-visible decode/request latency without worse GPU-seconds per useful turn.

## Experiment 2 — Gemma 4 MTP

Validate exact checkpoint compatibility first.

If it runs, measure:

- VRAM delta;
- acceptance rate;
- output latency;
- GPU seconds;
- behavior after Modal snapshot restore;
- multimodal/tool-call regressions.

## Experiment 3 — Persistent KV on Modal Volume v2

Use vLLM `OffloadingConnector` with:

```text
GPU
→ CPU primary
→ FS secondary on Volume v2
```

Set deterministic cross-process hashing.

Test:

1. 8k stable prefix, first request;
2. scale to zero;
3. restore;
4. same prefix + short new tail;
5. compare with no persistent KV.

Measure:

- cache hit ratio;
- bytes read/written;
- lookup latency;
- KV promotion latency;
- total prefill latency;
- total request latency;
- GPU active seconds;
- storage growth.

## Experiment 4 — S3/object store

Only if persistent KV produced a useful win.

Compare the same test against S3-compatible storage.

## Experiment 5 — Mooncake

Only when there is evidence that a dedicated KV service solves a measured limitation:

- multiple workers;
- storage latency;
- high cache reuse;
- prefill/decode separation.

---

# 16. Decision criteria

### Speculative decoding is worth keeping if:

- TTFT is not materially worse;
- inter-token/output latency falls;
- GPU-seconds per successful task do not increase badly;
- tool/multimodal behavior remains correct;
- snapshot restore remains stable;
- concurrency remains sufficient for expected users.

### Persistent KV is worth keeping if:

- hit latency is consistently below recompute latency for real prefixes;
- cache write overhead does not dominate first turns;
- storage growth/cleanup stays manageable;
- cross-container hits are reproducible;
- failure of the cache degrades only performance, never correctness.

---

# 17. Current recommendation

For `local-multimodal-agent`, prioritize:

```text
1. n-gram / suffix speculation benchmark
2. Gemma 4 MTP compatibility benchmark
3. persistent KV using Modal Volume v2 + OffloadingConnector FS
4. S3/object-store comparison
5. Mooncake only after scale or evidence requires it
```

The two highest-value questions are:

> **Can we materially reduce decode latency with Gemma 4 speculation without sacrificing the current simple single-GPU deployment?**

and:

> **Can a restored server reuse an 8k–16k conversation/document prefix faster than recomputing it?**

Those answers determine whether spare KV/VRAM and persistent storage become real product advantages rather than theoretical infrastructure.

---

# Sources

## vLLM

- Speculative Decoding — current docs  
  https://docs.vllm.ai/en/latest/features/speculative_decoding/

- vLLM 0.26 `serve` CLI / speculative methods  
  https://docs.vllm.ai/en/v0.26.0/cli/serve/

- Speculators project  
  https://docs.vllm.ai/projects/speculators/en/latest/

- Gemma 4 speculative decoding implementation  
  https://docs.vllm.ai/en/latest/api/vllm/v1/worker/gpu/spec_decode/gemma4/speculator/

- KV Offloading Usage Guide — vLLM 0.26  
  https://docs.vllm.ai/en/v0.26.0/features/kv_offloading_usage/

- MooncakeConnector Usage Guide  
  https://docs.vllm.ai/en/latest/features/mooncake_connector_usage/

- MooncakeStoreConnector Usage Guide  
  https://docs.vllm.ai/en/latest/features/mooncake_store_connector_usage/

## Modal

- Modal Volumes / Volumes v2  
  https://modal.com/docs/guide/volumes

## Mooncake

- Mooncake Store design  
  https://kvcache-ai.github.io/Mooncake/design/store/mooncake-store.html

- Mooncake Store deployment/tuning  
  https://kvcache-ai.github.io/Mooncake/deployment/mooncake-store-deployment-guide.html

---

## Notes on versioning

This research mixes two kinds of sources intentionally:

- **vLLM 0.26 documentation** where compatibility with the project's pinned runtime matters;
- **current vLLM documentation** for newer speculative/Mooncake capabilities and direction.

Anything present only in newer docs must not be assumed available in the pinned deployment until verified experimentally.
