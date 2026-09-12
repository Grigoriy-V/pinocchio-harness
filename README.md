# Pinocchio Harness

A harness for a personal assistant: one loop, one model behind an
OpenAI-compatible endpoint, two interfaces (Telegram, Chainlit), two
profiles (one machine, or Modal with scale-to-zero and a sandbox for
commands), and MCP in both directions. Until 2026-09-05 this repository
was `local-multimodal-agent`; the name changed when the local machine
stopped being the point.

**The model is the agent.** The harness gives it truthful capabilities,
evidence, durable state and boundaries, and does not make product decisions
for it: nothing classifies a request, nothing switches modes, nothing
decides for the model which tool a task needs. Every message enters the same
loop — the model answers, or calls a tool and goes on until it can answer —
and a turn ends in one of three ways: the model stops, the person says stop,
or the turn's budget is spent and the model is asked for the answer it has.
The contract is [`docs/PRODUCT.md`](docs/PRODUCT.md).

The model today is GLM 5.3 Flash through OpenRouter (`[model.sets.or]` in
`config.toml`); Gemma 4 12B and Qwen3.8 27B on Modal GPU Apps, and a
fine-tuned Gemma, stay as sets that switch by one line. Nothing binds to a
model: a defect that turns out to be the model's is measured by the
scenario suite and blind judges rather than patched around.

## What it does

From a conversation in Telegram, with nothing but the request:

- **Files** in the person's own workspace: list, read, write, edit; a
  picture file is shown to the model as a picture, a PDF page as a page.
- **Commands** — shell, Python, `pip`, node — through one tool, in a
  process that holds no secret: on Modal, a separate Function beside the
  renderer; locally, a process on the machine. What it installs lives in the
  workspace and survives the container.
- **Looking, and using**: a page it made, opened in a real browser it drives
  one action at a time (click, type, press, evaluate, screenshot, console);
  a document's pages; a web page.
- **The web**: search (Firecrawl), a bounded direct fetch, a browser view.
- **Handing over**: a file reaches the person only through `send_file`;
  looking is never sending.
- **Memory and history**: facts the person asked it to keep; search and
  read-back over the conversation, including results the context window no
  longer carries in full.
- **Goal and plan**: for a request with several parts the model writes them
  down once (`set_goal`) so none is lost; `/plan` adds a maintained task
  list when the person wants one.
- **Control**: `/stop` ends the running turn, `/new` starts a conversation,
  `/mode careful` makes workspace changes ask first; a turn survives a
  worker that dies mid-way and resumes where it was.

- **MCP**: servers named in `config.toml` are the assistant's tools under
  the owner's allowlist and approval rule (a time server, GitHub's remote
  server today); and the harness is itself an MCP server for its
  operator's tools (below).

What it costs is recorded per turn: model calls, tool calls, tokens,
seconds, the harness's own seconds by name, and a derived GPU cost
(`tools/show_run.py`, `tools/run_named_seconds.py`).

## Evidence

[`reports/2026-09-05_showcase.md`](reports/2026-09-05_showcase.md) is five
conversations rendered from the deployed database — request, tool calls,
results, the picture the model looked at, the file it sent, the numbers —
not screenshots of a chat:

1. a CSV summed with a command, the chart made, looked at, sent, the
   answer named;
2. a failing script repaired from its traceback, run again green;
3. a PDF made, its text checked, sent;
4. a page written and inspected, with the screenshot;
5. a value read back from history after the file it came from was gone.

The same suite records the failures, with run ids, in
[`ISSUES.md`](ISSUES.md). Regenerate the pages with `tools/showcase.py`.

The fine-tune experiment (roadmap 24, closed 2026-09-12): a LoRA of Gemma 4
12B on 782 of this harness's own GLM turns, trained, merged and served
from [pinocchio-finetune](https://github.com/Grigoriy-V/pinocchio-finetune),
measured blind beside the untuned model on Linux workers — GLM 9.81,
untuned bf16 9.25, tuned 8.67, int4 8.15 out of 10. It did not help; what
it left here is the trajectory capture, seven scenario families with
held-out cases, the export, and the blind-judge tools
([`reports/2026-09-11_gemma_finetune_experiment.md`](reports/2026-09-11_gemma_finetune_experiment.md)).

## MCP, both ways

The harness speaks the Model Context Protocol in both directions. As a
**server** (`python -m tools.mcp_server`, registered by `.mcp.json`) it
hands its operator's tools to Claude Code or Codex: the last turns and one
turn's trace, the harness's own seconds, the scenario suite, the export
and blind-judge pipeline, the journals — and two priced tools,
`run_scenarios` and `run_turn`, that ask the person in the protocol
before they start, twice: Claude Code's ask-every-time flag and the
server's own question with the scope and the price. As a **client**, a
section in `config.toml` makes any MCP server the assistant's tools, under
an allowlist and an approval rule the owner sets rather than the server:
today a time server over stdio and GitHub's remote server over HTTP,
locally and deployed. One page: [`docs/MCP.md`](docs/MCP.md); the two
reports with the live transcripts:
[`reports/2026-09-12_item25_mcp_server.md`](reports/2026-09-12_item25_mcp_server.md),
[`reports/2026-09-12_item26_mcp_tools.md`](reports/2026-09-12_item26_mcp_tools.md).

## Two profiles, one product

```text
Telegram ─ webhook ─┐                                  ┌─ run_command   (commands, no secret)
                    ├─ worker (the loop) ─ model ──────┤
Chainlit ───────────┘        │                         └─ render_web_page (browser, no secret)
                             ├─ PostgreSQL (Neon): conversations, memory, telemetry
                             └─ Volume: one workspace per person
```

- **Deployed**: Modal. `assistant-control` holds the webhook, the worker,
  the command runner, the renderer, a `scenarios` Function that runs the
  live suite in the worker's own environment and an `ask` Function for one
  turn on a free text; the model is the hosted set by default, the GPU Apps
  (`assistant-llm-v2`, the Qwen pair) stay deployed and scaled to zero.
  Secrets live in one Modal secret, published from the owner's `.env`.
- **Local**: the same code on one machine, the same hosted model (or any
  OpenAI-compatible endpoint, vLLM included); Chainlit or Telegram long
  polling is the interface; SQLite holds the state; commands run on the
  machine, in the workspace, on Windows under a write-restricted token.

How each is configured, deployed and read is
[`docs/OPERATIONS_MAP.md`](docs/OPERATIONS_MAP.md); the shape of the system
is [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md).

## How it is checked

- `pytest -q` — the offline suite, no model, no network, no credential.
- `scripts/loop_live.py` — the mini set of eight live scenarios, nine
  wider ones, and seven families (D L N T U V X: a wrong turn on the way, a
  tool that lies back, long tasks, an interjection mid-turn …) with
  held-out cases, each asserting on files, tool calls and the answer,
  never on the route the model took; `--deployed` runs them inside the
  deployed worker from a clean thread and workspace, `--model <set>`
  against another model, `--parallel N` for data.
- `tools/judge_pack.py` → three blind Sonnet judges → `tools/judge_unblind.py`
  — anonymised, shuffled transcripts scored on a fixed rubric, for what a
  check cannot assert.
- `tools/prompt_scenarios.py` — one prompt or setting against another on
  fixed requests, with cost.

Every live run is a paid model call and a worker, and a human gate during
development ([`AGENTS.md`](AGENTS.md)).

## Quick start

Local, Windows with Python 3.12 and `uv`:

```powershell
git clone https://github.com/Grigoriy-V/pinocchio-harness.git
cd pinocchio-harness
uv sync --all-groups
Copy-Item env.example .env
.venv\Scripts\python.exe -m scripts.doctor
```

Put the chosen set's key in `.env` (`MODEL_OR_API_KEY` for the OpenRouter
default named in `config.toml`; or point the plain `[model]` section at any
OpenAI-compatible server, vLLM 0.26 with Gemma 4 12B being the tested one),
then:

```powershell
.venv\Scripts\python.exe -m chainlit run ui/chainlit_app.py --port 8100 --headless
```

or, with `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USERS` set:

```powershell
.venv\Scripts\python.exe -m ui.telegram.run
```

Deployed: `modal deploy deploy/modal/control_app.py` after the secret is
published; the model apps, the database and the webhook are in the
operations map. To use the harness from Claude Code, open the repository:
`.mcp.json` offers the `pinocchio` server ([`docs/MCP.md`](docs/MCP.md)).

## Layout

```text
app/        agent (the loop), context, memory, models, tools, telemetry
ui/         Chainlit and Telegram adapters
deploy/     Modal apps: control plane, model, autoscale
scripts/    live scenarios and their families, doctor, measurements
tools/      show_run, run_named_seconds, showcase, export_trajectories, judge_pack, judge_unblind, work_log, mcp_server
tests/      offline suite
docs/       PRODUCT, PROJECT_MAP, CODEMAP, OPERATIONS_MAP, MCP
reports/    evidence, dated, with the two JSONL journals
```

## Where things are decided

[`ROADMAP.md`](ROADMAP.md) is the only plan. [`DECISIONS.md`](DECISIONS.md)
holds the durable choices and why. [`ISSUES.md`](ISSUES.md) holds the
defects, observed, whether or not anyone means to fix them.
[`AGENTS.md`](AGENTS.md) is how work is done here, by a person or an agent.
