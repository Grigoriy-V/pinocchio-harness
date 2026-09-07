# Pinocchio Harness

A harness for a personal assistant: one loop, one model behind an
OpenAI-compatible endpoint, two interfaces (Telegram, Chainlit), two
profiles (a GPU on your own machine, or Modal with scale-to-zero and a
sandbox for commands). Until 2026-09-05 this repository was
`local-multimodal-agent`; the name changed when the local machine stopped
being the point.

**The model is the agent.** The harness gives it truthful capabilities,
evidence, durable state and boundaries, and does not make product decisions
for it: nothing classifies a request, nothing switches modes, nothing
decides for the model which tool a task needs. Every message enters the same
loop — the model answers, or calls a tool and goes on until it can answer —
and a turn ends in one of three ways: the model stops, the person says stop,
or the turn's budget is spent and the model is asked for the answer it has.
The contract is [`docs/PRODUCT.md`](docs/PRODUCT.md).

The model today is Gemma 4 12B through vLLM. Nothing binds to it: swapping
the model is configuration, and a defect that turns out to be the model's
is measured by the scenario suite rather than patched around.

## What it does

From a conversation in Telegram, with nothing but the request:

- **Files** in the person's own workspace: list, read, write, edit; a
  picture file is shown to the model as a picture, a PDF page as a page.
- **Commands** — shell, Python, `pip`, node — through one tool, in a
  process that holds no secret: on Modal, a separate Function beside the
  renderer; locally, a process on the machine. What it installs lives in the
  workspace and survives the container.
- **Looking**: a page it made, opened offline in a real browser with a
  screenshot and console errors; a document's pages; a web page.
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

What it costs is recorded per turn: model calls, tool calls, tokens,
seconds, and a derived GPU cost (`tools/show_run.py`).

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

## Two profiles, one product

```text
Telegram ─ webhook ─┐                                  ┌─ run_command   (commands, no secret)
                    ├─ worker (the loop) ─ model ──────┤
Chainlit ───────────┘        │                         └─ render_web_page (browser, no secret)
                             ├─ PostgreSQL (Neon): conversations, memory, telemetry
                             └─ Volume: one workspace per person
```

- **Deployed**: Modal. `assistant-control` holds the webhook, the worker,
  the command runner, the renderer and a `scenarios` Function that runs the
  live suite in the worker's own environment; `assistant-llm-v2` serves the
  model on an A10 and scales to zero. Secrets live in one Modal secret,
  published from the owner's `.env`.
- **Local**: the same code on one machine. vLLM in WSL2 serves the model;
  Chainlit or Telegram long polling is the interface; SQLite holds the
  state; commands run on the machine, in the workspace.

How each is configured, deployed and read is
[`docs/OPERATIONS_MAP.md`](docs/OPERATIONS_MAP.md); the shape of the system
is [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md).

## How it is checked

- `pytest -q` — the offline suite, no model, no network, no credential.
- `scripts/loop_live.py` — the mini set of eight live scenarios (and nine
  wider ones by letter), each asserting on
  files, tool calls and the answer, never on the route the model took;
  `--deployed` runs them inside the deployed worker from a clean thread and
  workspace.
- `tools/prompt_scenarios.py` — one prompt or setting against another on
  fixed requests, with cost.

Every live run wakes a GPU and is a human gate during development
([`AGENTS.md`](AGENTS.md)).

## Quick start

Local, Windows with Python 3.12 and `uv`:

```powershell
git clone https://github.com/Grigoriy-V/pinocchio-harness.git
cd pinocchio-harness
uv sync --all-groups
Copy-Item env.example .env
.venv\Scripts\python.exe -m scripts.doctor
```

Point `MODEL_ENDPOINT` at an OpenAI-compatible server (the tested one is
vLLM 0.26 with Gemma 4 12B; the command is in `docs/OPERATIONS_MAP.md`),
then:

```powershell
.venv\Scripts\python.exe -m chainlit run ui/chainlit_app.py --port 8100 --headless
```

or, with `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USERS` set:

```powershell
.venv\Scripts\python.exe -m ui.telegram.run
```

Deployed: `modal deploy deploy/modal/control_app.py` after the secret is
published; the model app, the database and the webhook are in the
operations map.

## Layout

```text
app/        agent (the loop), context, memory, models, tools, telemetry
ui/         Chainlit and Telegram adapters
deploy/     Modal apps: control plane, model, autoscale
scripts/    live scenarios, doctor, measurements
tools/      show_run, showcase, prompt_scenarios, work_log
tests/      offline suite
docs/       PRODUCT, PROJECT_MAP, CODEMAP, OPERATIONS_MAP
reports/    evidence, dated, with the two JSONL journals
```

## Where things are decided

[`ROADMAP.md`](ROADMAP.md) is the only plan. [`DECISIONS.md`](DECISIONS.md)
holds the durable choices and why. [`ISSUES.md`](ISSUES.md) holds the
defects, observed, whether or not anyone means to fix them.
[`AGENTS.md`](AGENTS.md) is how work is done here, by a person or an agent.
