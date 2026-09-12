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

## Live checks (gates, not run yet)

1. A Claude Code session opened in this repository with `.mcp.json`
   loaded: `runs`, `run`, `scenarios` on local data. Free. Needs a new
   session (a project's MCP servers load at start and are approved by
   the person); the transcript goes here.
2. `run_turn` locally on the `or` set: one short prompt through GLM.
   Cents.
3. `run_scenarios` deployed on the second workspace, the mini set on
   `or`: the control container's minutes and GLM's tokens, under a
   dollar. The first workspace is not touched.

## Cost so far

None: no worker, no model call. The stdio check read the deployed
telemetry from this machine, which is a client read.
