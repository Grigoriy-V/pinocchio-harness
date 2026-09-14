# Step 4 — дополнение к плану: Context & Memory Architecture

**Статус:** дополнение к существующему плану Step 4.  
**Не заменяет ROADMAP.md и не создаёт новый утверждённый этап.**  
Документ фиксирует архитектурные требования, которые должны быть учтены в следующих подшагах Step 4, прежде всего в 4.4–4.6.

## 1. Зачем это дополнение

После реализации 4.1 один общий agent loop теперь может выполнять длинную последовательность:

```text
load
→ model
→ tools
→ model
→ tools
→ ...
→ persist
```

Это означает, что один turn способен сам по себе набрать десятки тысяч токенов ещё до `persist`.

Существующая context policy создавалась под ранний 16k baseline:

- `keep_recent = 8`;
- `summarize_after = 16`;
- rolling summary до ~150 слов;
- fold в основном после завершения turn;
- emergency fold только после `ContextOverflowError`.

Для 64k/128k agent loop эта политика считается legacy и должна быть заменена token-pressure-based context engine.

Главный принцип:

> Память не компактируется. Компактируется только model-visible projection памяти.

---

## 2. Референсная архитектура

Базовый ориентир — **DeepSeek Harness compaction/session model**.

Из **Hermes Agent** берётся в первую очередь recovery-механика через поиск по архивированной истории (`session_search`-подобный интерфейс).

Не требуется сейчас строить собственную сложную hierarchical/vector memory system.

Целевая схема:

```text
                 RAW DURABLE HISTORY
                  never compacted
                         │
                         ▼
                 CONTEXT SURFACE
                         │
           ┌─────────────┴─────────────┐
           │                           │
  <compacted-summary>           recent verbatim
           │                           │
           └─────────────┬─────────────┘
                         │
                    current turn
                         │
                         ▼
                  PRE-STEP CHECK
                         │
                  token pressure?
                    /         \
                  no           yes
                  │             │
                  │       prune old tools
                  │             │
                  │        LLM compact
                  │             │
                  └───────→ rebuild
                              │
                              ▼
                            MODEL
                              │
                            tools
                              │
                         PRE-STEP again
```

---

## 3. Source of truth

### 3.1 Raw history

Canonical conversation history remains lossless and durable.

It includes:

- user messages;
- assistant messages;
- tool calls;
- tool results;
- attachments/references;
- stable positions/order.

Compaction must never delete or rewrite canonical raw history.

If a conversation is compacted ten times, the original messages must still be recoverable.

### 3.2 Thread identity

Compaction is always **in-place**.

```text
thread_id before compaction
=
thread_id after compaction
```

Никаких новых conversations/sessions только из-за compression.

### 3.3 Semantic summary

LLM-summary обязателен как semantic compression layer.

Но summary — **derived artifact**, а не source of truth.

Если summarizer ошибся, сменился или стал недоступен:

- raw history остаётся;
- summary можно пересоздать;
- exact evidence можно восстановить из archive/retrieval.

---

## 4. Когда compact

Убирается основная зависимость от количества сообщений.

```text
summarize_after = 16
keep_recent = 8
```

не должны определять compaction нового harness.

Основной trigger — token pressure относительно capacity backend.

Baseline, близкий к DeepSeek:

```text
compaction_threshold ≈ 0.80 × model context capacity
```

Пример:

| model context | pressure threshold |
|---:|---:|
| 64k | ~51k |
| 128k | ~102k |
| 256k | ~205k |

Capacity должна определяться backend capability/config, а не жёстко кодироваться под Gemma/Qwen.

16k считается legacy baseline первых тестов, а не архитектурным ограничением.

---

## 5. Compaction должен работать между agent steps

Сейчас один длинный turn может переполнить context до `persist`.

Поэтому проверка context pressure должна происходить **перед каждым model step**, аналогично DeepSeek `agent/pre-step`.

Целевая форма:

```text
load
↓
prepare_context
↓
model
↓
tools
↓
prepare_context
↓
model
↓
...
```

`prepare_context`:

```text
measure current surface
↓
pressure?
├─ no → continue
│
└─ yes
    ↓
 prune/shadow old tool results
    ↓
 remeasure
    ↓
 still pressure?
    ├─ no → continue
    │
    └─ yes
         ↓
       LLM compact
         ↓
       rebuild surface
```

Emergency path `ContextOverflowError → compact → retry` остаётся как страховка, но не как нормальный trigger.

---

## 6. Cheap pruning до LLM-summary

Перед дорогим semantic compaction сначала уменьшаются старые тяжёлые tool results.

Типичные кандидаты:

- большие `read_file` outputs;
- browser/page dumps;
- test logs;
- document extracts;
- повторные observations;
- старые multimodal payloads.

Пример:

```text
tool: read_file(src/foo.py)
result: 14,800 tokens
```

после shadowing на model surface:

```text
tool: read_file(src/foo.py)
result archived: observation obs_183
status: success
path: src/foo.py
```

Полный результат остаётся в raw history.

Если после pruning context снова помещается — LLM-summary не вызывается.

---

## 7. LLM-summary

Если pruning недостаточно, создаётся semantic checkpoint.

Summary должен быть handoff для дальнейшего reasoning, а не короткий литературный пересказ.

Рекомендуемый baseline schema:

```text
## User Goal
## Constraints and Preferences
## Important Decisions
## Progress
## Tool / Artifact State
## Errors and Findings
## Open Questions
## Current Work
## Next Step
## Critical Context
```

При повторном compaction предыдущий summary входит в материал нового compaction.

Summarizer должен:

- сохранять всё ещё актуальное;
- объединять это с новой историей;
- удалять явно устаревшее;
- сохранять current work и next step;
- не заменять raw history.

На первом этапе не требуется отдельная hierarchy вида `S1 + S2 + S3 → meta-summary`.

---

## 8. Что хранить о compaction

Нужен durable compaction record.

Минимально:

```text
context_compactions

id
thread_id
start_position
end_position
summary
source_tokens
summary_tokens
provider
model
prompt_version
created_at
```

Желательно также иметь:

- shadowed message/event IDs;
- usage;
- raw summarizer output или reference на него;
- reason/trigger (`pressure`, `overflow`, manual/debug);
- compaction version.

Это позволяет понимать, каким образом был построен model surface.

---

## 9. Model-visible surface

После compaction модель должна видеть примерно:

```text
STABLE PREFIX
- system
- stable policies
- tool schemas

COMPACTED CONTEXT
- semantic summary/checkpoint

RECENT VERBATIM
- retained exact recent history

DYNAMIC STATE
- retrieved facts
- recovered history snippets
- todo/current state

CURRENT TURN
- current user/model/tool trajectory
```

Volatile retrieval не должен стоять перед большой стабильной conversation prefix, иначе он разрушает prefix-cache reuse.

Это требование остаётся частью будущего 4.6 cache-friendly assembly.

---

## 10. Exact recovery из архивированной истории

Из Hermes берётся принцип `session_search`.

Нужен интерфейс приблизительно:

```text
search_history(query, thread_id, limit)
```

который возвращает **реальные raw messages/tool results**, а не новый summary.

Первая версия может использовать обычный full-text search:

- SQLite FTS5 локально;
- PostgreSQL full-text search в deployed profile.

Vector DB / embeddings не являются prerequisite.

Связка:

```text
LLM summary
→ semantic continuity

search_history
→ exact recovery
```

Если summary потерял конкретную ошибку, имя файла, решение или старую формулировку пользователя, агент может восстановить её из raw archive.

---

## 11. Structured agent state

Некоторые вещи не должны существовать только в тексте history/summary.

В частности будущие:

- `todo`;
- pending `ask_user`;
- current goal/task state;
- budget counters;
- cancellation state;
- side-effect receipts;
- restart/resume state.

должны быть structured state.

Compaction не должен уничтожать их и не должен требовать, чтобы summarizer "догадался" восстановить их.

Это особенно важно для 4.4 `todo`, 4.5 `ask_user` и 4.7 restart/resume.

---

## 12. Thinking/reasoning

Внутренний reasoning модели не считается durable semantic memory.

Если backend сохраняет thinking/reasoning для replay/debug:

- raw provider data может храниться отдельно;
- recent reasoning может временно находиться в active surface;
- старый reasoning — один из первых кандидатов на shadowing/compaction.

Durable continuity должна опираться на:

- user-visible history;
- tool evidence;
- structured state;
- semantic summaries;
- exact recoverable archive.

---

## 13. Связь с уже реализованным 4.1

4.1 не требуется откатывать или переделывать.

Новый one-loop уже предоставляет подходящую основу:

```text
AgentState
- messages       current turn
- context        model-visible projection
- steps
- tool_calls
- spent_seconds
- stopping
```

Контекст является отдельной projection и не является canonical history.

Изменение памяти касается в основном:

- context preparation;
- pre-step pressure check;
- compaction;
- recovery/retrieval;
- persistence of compaction records.

Сам `model ↔ tools` loop остаётся тем же.

---

## 14. Связь с текущим Step 4 plan

Этот документ является **дополнением**, а не заменой roadmap.

Предлагаемая привязка:

```text
4.0 Conversation serialization
    DONE

4.1 One loop
    IMPLEMENTED offline

4.2 Tool execution seam
    без изменения scope

4.3 Turn stopping / proportional validation
    без изменения основного scope

4.4 todo
    todo обязан переживать compaction

4.5 ask_user
    pending decision обязан переживать compaction

4.6 Context Engine
    scope расширяется:
    - model capacity / token pressure
    - pre-step context preparation
    - cheap tool-result pruning
    - LLM semantic compaction
    - durable compaction records
    - raw history remains canonical
    - exact history recovery/search
    - cache-friendly assembly
    - overflow → compact → retry

4.7 Restart / resume / scenarios
    дополнительно проверяет continuation после compaction
```

До 4.6 достаточно соблюдать контракт при разработке 4.2–4.5; полную реализацию compaction не обязательно тащить вперёд.

---

## 15. Acceptance для будущего 4.6

Минимальный acceptance:

1. Raw messages не удаляются после compaction.
2. Compaction trigger зависит от token pressure, а не количества сообщений.
3. Один длинный turn может compact между steps и продолжить тот же turn.
4. Сначала pruning old tool results, затем LLM-summary только при необходимости.
5. После compaction текущий goal/current work сохраняется.
6. Exact detail из shadowed history восстанавливается через `search_history`.
7. `todo` и pending `ask_user` не теряются.
8. `thread_id` не меняется.
9. Prefix-cache layout остаётся стабильным между compaction epochs.
10. `ContextOverflowError` может инициировать bounded compact+retry вместо немедленного отказа.
11. Всё работает при backend capacity 64k и 128k без model-specific memory architecture.
12. Один и тот же raw conversation можно re-project/recompact другой summarizer-моделью без потери canonical history.

---

## 16. Не входит сейчас

Не требуется на этом этапе:

- vector DB;
- mandatory embeddings;
- complex hierarchical summary tree;
- separate "memory agent";
- autonomous long-term fact extraction from every turn;
- cross-user/shared memory;
- multi-agent memory;
- full event-sourcing rewrite всего приложения;
- специальный workflow под Qwen/Gemma;
- перенос compaction в отдельный operating mode.

---

## 17. Короткая формула

```text
DeepSeek-style:
raw session log
+ pre-step pressure compaction
+ semantic checkpoint
+ recent verbatim tail

Hermes-style:
exact search/recovery from archived history

Our constraints:
one loop
+ structured todo/ask state
+ cache-friendly surface
+ backend-discovered 64k/128k context
```

**Итог:** память остаётся durable и model-independent как source of truth, но LLM-summary остаётся обязательным semantic compression layer. Compaction работает как часть context preparation между steps, а не как отдельный режим или lifecycle.
