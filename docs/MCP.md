# MCP, both ways

The harness speaks the Model Context Protocol in both directions: it is an
MCP **server** for the operator's tools, and its assistant is an MCP
**client** of servers named in `config.toml`. One page for the whole of it;
the maps stay the source (`docs/OPERATIONS_MAP.md` for running it,
`docs/PROJECT_MAP.md` for the system shape, `docs/CODEMAP.md` for the code),
and the two reports carry the evidence.

```text
Claude Code / Codex ──MCP──▶ tools/mcp_server.py  (runs, run, scenarios, judge_pack, run_turn …)
                                     │
                                     ▼
                              the harness's own scripts and telemetry

the assistant's toolbox ──MCP──▶ time server (stdio)      ──▶ get_current_time, convert_time
                        ──MCP──▶ GitHub remote (HTTP)     ──▶ get_file_contents, list_issues, list_commits …
```

## 1. The harness as an MCP server (roadmap 25)

`python -m tools.mcp_server` serves thirteen tools on stdio. `.mcp.json`
at the repository root registers it for Claude Code as `pinocchio`; Codex
takes the same command in its own configuration. Tools appear to the
client as `mcp__pinocchio__<tool>`.

| tool | what it does |
|---|---|
| `runs`, `run` | the last turns; one turn's trace with timings, calls, tokens, cost |
| `harness_seconds` | the harness's own seconds by name, per run |
| `thread` | a conversation's stored rows, media as kind and size |
| `scenarios` | the scenario families and cases, held-out flags, no model contacted |
| `export_trajectories`, `judge_pack`, `judge_unblind` | the data and blind-judging pipeline |
| `work_log_add`, `work_log_search` | the two journals |
| `doctor` | the environment report |
| `run_scenarios` | scenario letters on a model set, local or deployed — **priced** |
| `run_turn` | one turn of the assistant on a text, local or deployed — **priced** |

Each read tool runs the operator's script as a subprocess and returns what
it printed, so the MCP result is exactly what the operator would have seen
in a terminal. `scenarios` is in-process. The protocol owns the process's
stdout; everything the harness prints goes to stderr.

**The gate, twice.** The two priced tools carry
`_meta["anthropic/requiresUserInteraction"] = true`, which makes Claude
Code ask the person before every call in every permission mode but
`dontAsk`; and inside the handler they ask again through elicitation, with
the scope and the price in the question ("Run scenarios DVX on model set
base, deployed on Modal workspace grigoriy98smile. Price: … Run exactly
this?"). Anything but an accepted yes, and a client that cannot ask, gets
"not run: …" as an ordinary result. Both layers, because a client may
ignore annotations, and the rule of this project is that a worker starts
on the human's word only. Nothing here returns an environment value; the
database is reached by the scripts through the settings class.

Evidence: `reports/2026-09-12_item25_mcp_server.md` — Claude Code headless
used the read tools and was stopped at the priced one; a local turn on GLM;
the mini set deployed through `run_scenarios` with 44 progress notifications
streamed back.

## 2. MCP servers as the assistant's tools (roadmap 26)

A section in `config.toml` is a server; a server is a capability
`mcp.<name>`, granted with the defaults:

```toml
[mcp.servers.github]
transport = "http"                          # or "stdio" with command = ["python", "-m", "…"]
url = "https://api.githubcopilot.com/mcp/"
token = "MCP_GITHUB_TOKEN"                  # the .env key's name; the value never leaves .env
tools = ["get_file_contents", "list_issues", "get_issue", "search_code", "list_commits"]
read_only = ["get_file_contents", "list_issues", "get_issue", "search_code", "list_commits"]
```

- **Allowlist.** A tool the server offers that `tools` does not name does
  not exist to the model.
- **Approval by the owner, not the server.** A tool in `read_only` runs
  without asking and may be replayed after a worker dies; every other
  allowed tool asks first, whatever the server's own annotations say — the
  spec itself says a client must not trust them.
- **The contract the model reads** is rendered from what the server
  declared: its description; "returns" from its output schema or "the
  server's text result"; "leaves" nothing or "whatever the server changes
  on its side". The same three-part contract every native tool has.
- **Sessions** live on a loop of their own in a thread (`McpSessions`,
  `app/tools/mcp.py`), one keeper task per server, opened on first use,
  closed with the agent. A server that cannot be reached contributes no
  tools and a log line; the rest of the toolbox stands.
- **Results** are the text parts; structured content as JSON when there is
  no text; a non-text part is named, not shown. An error result is a tool
  error `mcp.error`; a transport failure `mcp.unreachable`; a server that
  asks the person something mid-call is declined.
- **Deployed** the same way: the control image carries the SDK and the
  time server, the secret carries `MCP_<NAME>_TOKEN`, published by
  `tools/sync_control_secret.py` like every other key.

Adding a server later is a section and, for HTTP, one `.env` line. No code.

Evidence: `reports/2026-09-12_item26_mcp_tools.md` — one local and one
deployed turn asked the time in Vienna and the latest commit of this
repository; both turns used `time_get_current_time` and
`github_list_commits` and answered right.

## 3. Connecting a client to the harness

Claude Code, in this repository: `.mcp.json` is already there; a new
session offers the `pinocchio` server for approval. Elsewhere:

```bash
claude mcp add --transport stdio pinocchio -- D:/ML/local-multimodal-agent/.venv/Scripts/python.exe -m tools.mcp_server
```

Any client with a stdio transport works the same way; the server needs the
repository's virtual environment and its `.env`, which it reads only
through the settings class.

## 4. What the SDK is, and why

`mcp` 1.x (protocol 2025-11-25), held below 2 because Chainlit pins it. The
2026-07-28 revision (stateless requests, elicitation as a retried call,
`FastMCP` renamed `MCPServer`) changes nothing in the tools' contract; the
move is a version bump when the pin lifts. The whole research, with the
spec's own words on annotations, elicitation and consent, is
`reports/2026-09-12_mcp_research.md`.
