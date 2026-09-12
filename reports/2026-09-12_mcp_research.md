# MCP research: the harness as a server (25) and MCP servers as tools (26)

Research for roadmap items 25 and 26, approved 2026-09-12. Everything
below the facts is a draft; nothing here is approved until the human says
so in the chat. Sources were read on 2026-09-12; facts marked *unverified*
come from third-party pages only.

## 1. The protocol today

- **Current revision 2026-07-28** (previous 2025-11-25).
  https://modelcontextprotocol.io/specification/2026-07-28/changelog
- **Stateless.** No `initialize` handshake, no session id; every request
  carries its protocol version and client capabilities in `_meta`. A new
  required RPC `server/discover` advertises versions and capabilities.
- **Multi Round-Trip Requests (MRTR)** replace server-initiated requests.
  A tool that needs the person's answer returns
  `resultType: "input_required"` with an `inputRequests` map (an
  `elicitation/create` request inside) and an opaque `requestState`; the
  client asks the person and **retries the same call** with
  `inputResponses` and the echoed `requestState`. The handler runs again
  from the start, so any side effect must come after the answer, and the
  state must be integrity-protected (the SDK does this).
- **Elicitation** in form mode takes flat primitives only (string,
  number, boolean, enum); a yes/no is a boolean property. Answers are
  `accept | decline | cancel`. Servers must not ask for secrets this way.
- **Tool annotations**: `readOnlyHint` (default false), `destructiveHint`
  (default true), `idempotentHint` (default false), `openWorldHint`
  (default true). The spec says clients MUST treat them as untrusted
  unless the server is trusted.
- **Transports**: stdio (subprocess, JSON-RPC lines; stdout is the
  protocol, logs to stderr) and Streamable HTTP (one POST endpoint,
  response as JSON or an SSE stream). The old HTTP+SSE transport is
  deprecated. HTTP servers must validate `Origin`; authorization is OAuth
  2.1, optional; a static bearer header is common client practice, not
  spec. stdio servers take credentials from the environment.
- **Deprecated** in this revision: roots, sampling, logging (12-month
  window). Tasks moved to an extension (`io.modelcontextprotocol/tasks`,
  polling). Progress notifications and cancellation stay.
- `tools/list` results carry `ttlMs` and `cacheScope`; input and output
  schemas may use any JSON Schema 2020-12 keyword.

## 2. The SDK and the clients we use

- **`mcp` 2.2.0** (2026-09-07, Python ≥3.10) implements 2026-07-28 and
  serves earlier clients. Renames from 1.x: `FastMCP` → `MCPServer`
  (`mcp.server`), `ClientSession` → `Client`, `McpError` → `MCPError`.
  https://pypi.org/project/mcp/ , https://py.sdk.modelcontextprotocol.io/
  - Server: `@mcp.tool(annotations=ToolAnnotations(...))`; a pydantic
    return type becomes `outputSchema` and `structuredContent`;
    `ctx.elicit(message, schema)` for the person's answer;
    `ctx.report_progress(n, total, message)`; `mcp.run()` for stdio,
    `mcp.run("streamable-http", host, port)` or `mcp.streamable_http_app()`
    for HTTP.
  - Client: `Client(StdioServerParameters(command, args, env))` or
    `Client(streamable_http_client(url, http_client=httpx_with_headers))`;
    `list_tools()`, `call_tool(name, args, progress_callback)`; a result
    has `content` (text, image, audio, resource link, embedded resource),
    `structured_content`, `is_error`. `Client(server)` runs a server
    in-memory, which is how the offline tests can drive one.
- **Claude Code** (https://code.claude.com/docs/en/mcp): servers from
  `.mcp.json` at the project root (`{"mcpServers": {name: {"type":
  "stdio", "command", "args", "env"} | {"type": "http", "url",
  "headers"}}}`, `${VAR}` expansion, per-server `timeout` in ms); tools
  appear as `mcp__<server>__<tool>`; the person approves calls under the
  permission mode; **`_meta["anthropic/requiresUserInteraction"] = true`
  on a tool makes Claude Code ask on every call, in every mode except
  `dontAsk`**; elicitation dialogs are supported (form; URL mode
  *unverified*). Claude Desktop's elicitation support is *unverified*
  (one closed issue says no).
- **Reference servers still maintained**: `uvx mcp-server-fetch`,
  `npx -y @modelcontextprotocol/server-filesystem <dirs>`,
  `uvx mcp-server-git --repository <path>` (still on SDK 1.x),
  `npx -y @modelcontextprotocol/server-memory`, `uvx mcp-server-time`.
  The sqlite and github reference servers are archived. GitHub's own
  remote server: `https://api.githubcopilot.com/mcp/` (OAuth or a PAT
  header).

## 3. Security, as the spec puts it

Clients should show a call's inputs and ask before sensitive operations,
validate results before they reach the model, set timeouts, and keep a
human in the loop; annotations and descriptions from a server are data.
A local one-click install must show the exact command. Local HTTP
servers should require a token. This matches the harness's own rules
(tool output is untrusted; a worker starts only on the human's word).

## 4. What the harness does by hand today (the raw material for 25)

Offline, read-only, importable as `main(argv)` or a function:
`tools/show_run.py` (a turn by run id, or the last N; `--summary`,
`--failed`, `--user`), `tools/run_named_seconds.py` (the harness's
seconds by name per run id), `tools/show_thread.py`,
`tools/export_trajectories.py` (Volume read from the client, no worker),
`tools/judge_pack.py`, `tools/judge_unblind.py`, `tools/work_log.py`
(`agent|ml add|list|show|search`), `scripts/doctor.py`,
`scripts/training_scenarios.py` (the families, cases, held-out set;
contacts no model). `app/telemetry/inspect.py` renders a run.

Paid, each a gate: `scripts/loop_live.py` (letters, `--deployed`,
`--model`, `--temperature`, `--repeat`, `--parallel`; deployed it calls
the Modal Function `scenarios` of `assistant-control`), a single turn
through `create_agent` + `agent.events` (the model API), and the
measurement scripts that wake a GPU.

A single turn is invoked as `scripts/loop_live.py` does it
(`Turn.ask`, `runtime.create_agent`, `text_message`, a sealed room of
temporary SQLite files and a workspace, a probe user). Settings are read
when `AgentSettings()` is instantiated, not on import, so a server
process can choose its room per call.

## 5. Draft design for 25: the harness as an MCP server

**Where.** `tools/mcp_server.py` (operational tooling, imports `app/` the
way `show_run` does; `app/` never imports it) and `tests/test_mcp_server.py`
driving it in-memory. A `.mcp.json` at the repo root registers it for
Claude Code under the project's venv:
`{"mcpServers": {"pinocchio": {"type": "stdio", "command": ".venv/Scripts/python", "args": ["-m", "tools.mcp_server"], "timeout": 3600000}}}`.
Codex reads the same shape from its own config.

**Transport.** stdio first: it is what Claude Code and Codex start, needs
no port, no token, and the process inherits `.env` through
`AgentSettings` like every tool. Streamable HTTP is a later option (a
Modal web endpoint beside `assistant-control`), not needed for the first
version.

**Tools, first version** (names are the roadmap's vocabulary, not the
scripts'):

| tool | does | annotations | gate |
|---|---|---|---|
| `runs` | the last N turns, optionally one user or failed only | read-only | none |
| `run` | one turn by run id: timings, timeline, model and tool sections | read-only | none |
| `harness_seconds` | the harness's own seconds by name for run ids | read-only | none |
| `thread` | a thread's rows | read-only | none |
| `scenarios` | the families and cases, which are held out | read-only | none |
| `export_trajectories` | export to a folder, by prefix or from a dir | writes files, idempotent | none |
| `judge_pack` | anonymised pack + key from an export | writes files | none |
| `judge_unblind` | the votes against the key | read-only | none |
| `work_log` | add or search a journal record | appends | none |
| `doctor` | the environment report | read-only | none |
| `run_scenarios` | `loop_live` letters on a model set, local or deployed | not read-only, open world | **asks every time** |
| `run_turn` | one turn of the assistant on a text, probe user, sealed room | not read-only, open world | **asks every time** |

**The gate in the protocol.** A paid tool carries
`_meta["anthropic/requiresUserInteraction"] = true` (Claude Code asks
before the call in every mode) **and** asks again itself through
`ctx.elicit` with the exact scope and price in the message ("D V X on
`base`, deployed on workspace grigoriy98smile, ~$0.40 model + the worker;
run?"). It runs only on `accept` with `ok = true`; a decline, a cancel
or a client without elicitation means it does not run and says so. Both
layers, because annotations are advisory and a client may ignore them.
The elicitation happens before any side effect, so the MRTR re-execution
is harmless. The human's rule stays above both: the project agent still
asks in the chat; the protocol gate is the backstop, and what the
portfolio shows.

**Results.** Structured (`pydantic` models → `structuredContent`) plus
the rendered text the operator reads today (`inspect.render_run`), so a
client without structured support sees the same table. `run_scenarios`
reports progress per case through `report_progress`. Output is capped
(a run's rendering, not a dump of message text).

**What it never does.** Return an environment value, a connection string
or a secret; read a database except through `AgentSettings`; start a
worker without the two-layer yes. `.env` is read by the settings class
only.

**`run_turn`.** The assistant as a tool: `create_agent` in a sealed room
(temporary SQLite, a workspace under the room, probe user
`mcp-<client>`), the model set from the argument (default the product's
`or`, GLM through OpenRouter), an approval inside the turn answered "no"
(a caller cannot approve on the person's behalf), the answer and the run
id returned, the trace readable through `run`. Its own budget: the turn's
`turn_check_seconds` and a ceiling on model calls set in the tool.
Deployed `run_turn` (the second workspace's control plane) is a later
option.

**Tests (offline).** In-memory `Client` against the server: tool list and
annotations, `run` on a fixture telemetry store, `scenarios` shape,
`run_scenarios` refuses without an accepted elicitation and never
imports `modal` on the read-only path, no tool returns an env key.

**Live checks (gates).** (1) Claude Code with `.mcp.json`: `runs`, `run`,
`scenarios` on the local telemetry, zero cost. (2) `run_turn` locally on
GLM, one short prompt, cents. (3) `run_scenarios` deployed on the second
workspace with the mini set on `or`: the control container's CPU
minutes and GLM's tokens, well under a dollar. The first workspace is
not touched.

**Effort.** One to two days: the server file, the tests, `.mcp.json`,
an OPERATIONS_MAP section, a report with the transcript of the Claude
Code session that used it.

## 6. Draft design for 26: MCP servers as the assistant's tools

**Configuration** (`config.toml`, read by `app/config.py` only):

```toml
[mcp.servers.time]
transport = "stdio"
command = ["uvx", "mcp-server-time"]
tools = ["get_current_time", "convert_time"]   # allowlist; empty = none
read_only = ["get_current_time", "convert_time"]  # the owner vouches; else asks

[mcp.servers.github]
transport = "http"
url = "https://api.githubcopilot.com/mcp/"
token = "MCP_GITHUB_TOKEN"                     # the .env key, never the value
tools = ["get_issue", "list_issues", "search_code"]
read_only = ["get_issue", "list_issues", "search_code"]
```

Adding a server later is a section plus, for HTTP, one `.env` key
published to the control secret by `sync_control_secret` (its allowlist
regex gains `MCP_*`).

**Wiring.** A capability per server, `mcp.<name>`, in
`CapabilityRegistry`; the grant says whether the server's tools are
there. The registry builds toolboxes synchronously per call, so the
tools' list is fetched once by an `McpSessions` object owned by the
runtime (like `runner`), lazily at first use, cached for the container's
life or the server's `ttlMs`; a `Tool.run` awaits the session's
`call_tool`. Locally the sessions live with the Chainlit process;
deployed with the worker container (stdio servers need their runtime in
the control image, `uvx`/`npx`; HTTP servers need nothing).

**The contract the model reads.** Name `<server>_<tool>`; description
from the server; `returns` from `outputSchema` when present, otherwise
"the server's text result"; `leaves` from the annotations ("nothing"
when read-only, "a change on the <server> side" otherwise).
`requires_approval = name not in read_only`; `replay_safe = read_only`;
`mutates = False` (nothing in the workspace). `timeout_seconds` from
config, default 60. The contract test passes because all three parts are
filled.

**Schemas.** `inputSchema` passed through to the model; the toolbox's
subset validator must treat keywords it does not know (`anyOf`, `$ref`)
as "not checked" rather than an error (to verify in `validation_error`
before building).

**Results.** Text parts joined; `structuredContent` as JSON when there is
no text; an image or resource part becomes a line naming what was
dropped (first version, text only). `is_error` → a tool error to the
model. An `input_required` result → declined and reported as a tool
error: the assistant has no path to ask the person mid-tool in this
version.

**Trust.** Server descriptions and results are untrusted model input
(existing rule). Tools not in the allowlist do not exist to the model.
Nothing from the server reaches the brief except its tool contracts.

**First servers.** `time` (stdio, read-only, trivial: proves the
mechanism locally and deployed) and GitHub's remote server (HTTP, a PAT
in `.env`, read-only tools allowlisted: real value in the product, the
second transport, a secret on the usual route). Both cover what the
item promises; a third would be the owner's choice.

**Tests (offline).** An in-memory MCP server from the SDK stands in for
a real one: capability build, the contract of a wired tool, approval for
a non-read-only tool, allowlist filtering, result rendering, the
`input_required` decline.

**Live checks (gates).** A local turn that asks the time; a deployed
turn on the second workspace's `assistant-control` that reads an issue
of `pinocchio-harness` through GitHub's server.

**Effort.** Two to three days, the deployed image change included.

## 7. Open questions for the human

1. The first-version tool list of the server (§5 table): keep all twelve
   or start with the read-only ten plus `run_scenarios`?
2. `run_turn` in the first version, or after 26?
3. First servers for 26: `time` + GitHub remote, as drafted?
4. Streamable HTTP for the harness's own server: not in the first version.

## 8. Costs and gates in one line

Everything up to the live checks is offline and free. Live checks: a
Claude Code session on local data (free), one local GLM turn (cents),
one deployed mini set on the second workspace (under a dollar, a gate),
later one deployed turn through GitHub's server (cents, a gate). The
first workspace is not touched.
