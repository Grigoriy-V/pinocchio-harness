# Item 25: the harness as an MCP server

Approved 2026-09-12 (the human): all thirteen tools in the first version,
stdio only. Research and the draft design: `reports/2026-09-12_mcp_research.md`.

## What was built

`tools/mcp_server.py`, served on stdio by `python -m tools.mcp_server`;
`.mcp.json` at the repository root registers it for Claude Code as
`pinocchio`. Tests: `tests/test_mcp_server.py` (six, in memory over the
SDK's memory streams; the read tools run the real scripts against a
telemetry file made in the test).

| tool | wraps | annotations | gate |
|---|---|---|---|
| `runs` | `tools/show_run.py --last` | read-only | none |
| `run` | `tools/show_run.py <run_id>` | read-only | none |
| `harness_seconds` | `tools/run_named_seconds.py` | read-only | none |
| `thread` | `tools/show_thread.py` | read-only | none |
| `scenarios` | `scripts/training_scenarios.py`, in process | read-only | none |
| `export_trajectories` | `tools/export_trajectories.py` | writes files, idempotent | none |
| `judge_pack` | `tools/judge_pack.py` | writes files, idempotent | none |
| `judge_unblind` | `tools/judge_unblind.py` | read-only | none |
| `work_log_add` | `tools/work_log.py <journal> add` | appends | none |
| `work_log_search` | `tools/work_log.py <journal> search` | read-only | none |
| `doctor` | `scripts/doctor.py --json` | read-only | none |
| `run_scenarios` | `scripts/loop_live.py` with its flags | open world | asks every time |
| `run_turn` | `create_agent` in a sealed room | open world | asks every time |

Every wrapped script runs as a subprocess of the server, in the server's
environment, stdout captured into the result (`ok`, `text`, `command`).
Two reasons: a script's `print` must never land on the protocol's stdout,
and several scripts parse `sys.argv` themselves. The subprocess is a
blocking `Popen` in a thread with lines handed to the loop through a
queue, because asyncio's own subprocesses need the proactor loop on
Windows and the host's loop is whatever the host chose (the test suite's
is the selector loop).

`scenarios` is the one in-process tool: it reads the family definitions,
contacts no model, and returns them structured (letter, index, name,
thread, run-id sequence, held-out flag, prompt, interjection).

`run_turn` drives one turn the way `loop_live.Turn.ask` does, in its own
thread with its own selector loop: a temporary room (`settings_in`), a
probe user `mcp-<thread id>`, the model set from the argument through
`MODEL` for the duration of `create_agent` only, approvals inside the
turn answered no (the caller is not the person), a ceiling on tool calls.
It returns the answer, the run id, the tools called, seconds and call
counts, and the telemetry file the trace went to.

### The gate, twice

The two priced tools carry `_meta["anthropic/requiresUserInteraction"]
= true` (Claude Code asks before every call in every permission mode
except `dontAsk`) and, inside the handler, ask again through
`ctx.elicit` with a one-field boolean schema. The question names the
scope and the price: letters, model set, temperature, local or deployed
and on which Modal workspace (`modal profile current`, name only),
repeats, parallel calls, what is billed. Only `accept` with `ok = true`
runs; `decline`, `cancel`, `ok = false` and a client that cannot ask
(the elicitation request fails with `McpError`) return "not run: …" as
an ordinary result with `ok = false`, never an exception to retry. The
question is asked before anything starts.

The human's rule is above the protocol: the project agent asks in the
chat before calling either tool. The protocol gate is the backstop and
the part of this a portfolio reader can see.

### What it never returns

An environment value, a connection string, a token: the scripts open the
database through `AgentSettings`, and the server passes its environment
to them and prints nothing of it. `doctor` reports credential names only,
as it always did.

## The SDK

`mcp` 1.29 (protocol 2025-11-25), not 2.x: Chainlit 2.12 pins `mcp<2`,
and `uv sync` with `mcp>=2.2` downgraded Chainlit to 2.3 and Starlette to
0.41 to satisfy it (the lock file changed by 900 lines; reverted). The
group `mcp = ["mcp>=1.29,<2"]` declares the dependency that Chainlit was
already bringing. Claude Code and Codex accept the 2025-11-25 revision;
what the 2026-07-28 revision changed (stateless requests, elicitation as
a retried call) is the client's concern and needs nothing here.

## Checks

- `tests/test_mcp_server.py`: the tool list carries the contract
  (`Returns:` and `Leaves:` in every description), annotations, and the
  ask-every-time flag on exactly the two priced tools; `scenarios` lists
  38 cases with D1 held out at sequence 210 and an interjection in X;
  `runs` and `run` read a telemetry file made in the test through the
  real scripts and do not echo its path; a priced tool does not run on
  decline, cancel, `ok = false`, or a client without elicitation (a
  script call would fail the test); an accepted yes runs exactly
  `scripts/loop_live.py D V X --model base --deployed --parallel 2`; the
  question names the letters, the set, the workspace and the price.
- Real stdio: a client process (`stdio_client` + `ClientSession`)
  started `.venv/Scripts/python.exe -m tools.mcp_server`, initialised
  (server `pinocchio`, protocol 2025-11-25), listed 13 tools, called
  `scenarios` (38 cases) and `runs` (5 lines from the telemetry the
  `.env` names, a client read), and got the refusal from
  `run_scenarios` without an elicitation callback.
- Full offline suite: 1203 passed, 33 skipped after fixing a leak of my
  own from 2026-09-12 — `tests/test_loop_live_set.py` deleted `MODEL`
  with `delenv` on a name that was absent, which records nothing, so the
  `tuned` value `apply_model` wrote outlived the test and two
  `ModelSettings` tests read `MODEL_TUNED_*` instead of `MODEL_*`. Present
  at HEAD before this item, order-dependent, not seen when the two files
  ran alone.

## Live checks (approved 2026-09-12, "делай все")

1. **Claude Code as the client.** `claude -p` (2.1.251, Sonnet) with
   `--mcp-config .mcp.json --strict-mcp-config` and the three tools in
   `--allowedTools`. It called `runs` (three deployed runs listed with
   their ids) and `scenarios` (seven families, 38 cases, counted per
   family), then `run_scenarios D` and reported: "`MCPTool requires
   permission.` It never got to its own 'asks the person before
   starting' step; my permission mode blocked the call outright, so
   nothing ran and nothing was priced." The ask-every-time flag held in a
   headless session even with the tool allowed — the first layer of the
   gate, before the server's own question.
2. **`run_turn` locally on `or`.** A stdio client that answers the
   question yes; prompt: create `notes/hello.txt` with one line and give
   its size. Answer "16 bytes", tools `write_file`, `run_command`, 3
   model calls, 15–17 s, trace in the room's telemetry file. The first
   run showed the trace's JSON lines inside the protocol stream (the
   client discarded them as invalid messages); fixed by `claim_stdout`,
   the second run was clean. Two runs, cents of GLM.
3. **`run_scenarios` deployed on the second workspace**, the mini set on
   `or`. The question read: "Run scenarios the mini set on model set or,
   deployed on Modal workspace grigoriy98smile. Price: model tokens for
   every case; the control container while it runs; a GPU App wakes if
   the set is one. Run exactly this?" Answered yes. 44 progress
   notifications reached the client (one per check line and the
   verdict), `all scenarios passed`: 43 checks over A B C F W H E M, run
   ids `deployed-or-7270963e-10 … -190`, eleven turns between 10:06:06
   and 10:09:15 UTC, 4.75–43.6 s each, 164k input tokens in total
   (`tools/show_run.py --last 12`). The client script then crashed
   printing the result to a cp1251 console (an emoji in an answer); the
   server and the run were unaffected. The first workspace was not
   touched.

## Cost

Two local turns and eleven deployed turns of GLM 5.3 Flash (about 200k
input tokens, cents), about four minutes of the control container on the
second workspace. No GPU.

## Left open

- Streamable HTTP for this server (a remote client without the
  repository): not in this version, by the human's word.
- The 2.x SDK when Chainlit lifts its pin: `FastMCP` → `MCPServer`,
  elicitation as a retried call; nothing in the tools' contract changes.
