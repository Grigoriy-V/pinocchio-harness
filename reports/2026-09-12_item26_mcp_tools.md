# Item 26: MCP servers as the assistant's tools

Approved 2026-09-12 (the human): first servers `time` (stdio) and GitHub's
remote server (HTTP); both live checks ("оба"). Research and the draft:
`reports/2026-09-12_mcp_research.md` §6.

## What was built

- **Configuration** (`app/config.py`): `McpSettings` reads
  `[mcp.servers.<name>]` from `config.toml` into `McpServerConfig`
  (`transport` stdio|http, `command` or `url`, `token` = the `.env` key's
  name, `tools` allowlist, `read_only`, `timeout`). `secret_named(key)`
  reads the value, the environment first, then `.env`; nothing else in
  the code reads either.
- **Sessions** (`app/tools/mcp.py`, `McpSessions`): one loop in a thread
  for every configured server, opened on first use, closed with the
  agent. Each server has a keeper task that enters the transport's
  context and leaves it itself, because the SDK's transports are anyio
  task groups and may only be exited by the task that entered them (the
  first version closed from another task and anyio refused). Listing is
  synchronous for the toolbox build (the registry builds per thread,
  synchronously); a call awaits a future handed across loops.
- **Tools** (`mcp_tools`): each allowed tool becomes `<server>_<tool>`
  with the contract the model reads — the server's description; returns
  from its output schema when it declares one, else "the server's text
  result"; leaves "nothing" when the owner listed it read-only, else
  "whatever the server changes on its side". `requires_approval = not
  read_only`, `replay_safe = read_only`, `mutates = False`, the server's
  `inputSchema` passed through (the toolbox's validator checks the
  keywords it knows and ignores the rest). Results are the text parts; the
  structured content as JSON when there is no text; a non-text part is
  named, not shown; `isError` is a `mcp.error` tool error, a transport
  failure `mcp.unreachable`. A server that cannot be reached contributes
  no tools and a log line.
- **Wiring**: `CapabilityRegistry(mcp=...)` registers `mcp.<name>` per
  server; `Agent` grants them with the defaults; `create_agent` opens the
  sessions when the config names a server and closes them in `aclose`.
  The capability brief already lists every wired tool and every tool
  that asks, so nothing was added to it.
- **Deployment**: the control image syncs the `mcp` group (the SDK and
  `mcp-server-time`; `python -m mcp_server_time` needs no `uvx`);
  `sync_control_secret` publishes `MCP_<NAME>_TOKEN`; a new Function
  `ask(text, model, probe)` on `assistant-control` runs one turn on a free
  text the way `scenarios` runs a case, and the MCP server's `run_turn`
  takes `deployed=True` to call it.
- **Configured**: `time` with `get_current_time`, `convert_time`;
  `github` at `https://api.githubcopilot.com/mcp/` with
  `get_file_contents`, `list_issues`, `get_issue`, `search_code`,
  `list_commits`; all read-only. A fine-grained PAT, public repositories,
  made by the human; the key `MCP_GITHUB_TOKEN`.

## Checks

- `tests/test_mcp_tools.py` (six, a `FastMCP` in memory on the sessions'
  loop): the section from a file; the capability, the contract and the
  allowlist order; approval and replay by the owner's list; a call and
  its text; an error result as `mcp.error`; an unreachable server leaving
  the toolbox intact; the server's schema validated like the harness's
  own. `tests/test_sync_control_secret.py`: the token key is published by
  its name. `tests/test_mcp_server.py`: a deployed `run_turn` goes
  through `ask` and its question names the workspace and the container.
  Full suite 1213 passed, 33 skipped.
- **Live, local** (`run_turn` on `or`, the MCP server asked, I answered
  yes): "What time is it in Vienna? What is the latest commit on main of
  Grigoriy-V/pinocchio-harness?" → `time_get_current_time`,
  `github_list_commits`; "12:30 CEST" and "`6351375` — Item 26: the maps
  describe MCP servers as tools", both right; 2 model calls, 19.5 s, run
  `mcp-209046`. The `time` server started as a subprocess of the sessions'
  loop, GitHub answered over HTTP with the bearer token.
- **Live, deployed** on the second workspace: `sync_control_secret
  --suffix _2` published 13 keys including `MCP_GITHUB_TOKEN` (names
  only), `modal deploy` rebuilt the image with the `mcp` group (85 s;
  the first attempt died printing an arrow to a cp1251 console, the build
  itself continued), then `ask(...)` with the same two questions →
  the same two tools, "12:34", the same commit, 2 model calls, 17 s, run
  `deployed-ask-cc06e4b9-1`, no failures. The first workspace was not
  touched.

## Cost

Two turns of GLM (cents), one image build and one container of about a
minute on the second workspace. No GPU.

## Left open

- Images and resources from a server: text only in this version.
- A server's question mid-call (`input_required`) is declined; the
  assistant has no path to ask the person from inside a tool.
- Adding a server is a `config.toml` section and, for HTTP, one `.env`
  line published by the secret sync; no code.
