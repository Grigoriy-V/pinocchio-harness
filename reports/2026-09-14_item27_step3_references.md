# Roadmap 27 step 3, research: the coding tools of the references

**Date:** 2026-09-14. **Status:** research with options; nothing here is
approved until the human says so in words. Written under the AGENTS.md rule
of 2026-09-14 (a large step starts with the references; the build cites the
report and is checked against it).

Read on 2026-09-14 by five subagents from the sources named in each
section: Claude Code (official tools reference, the extracted tool
descriptions per version), Codex CLI (`openai/codex` main, first-party
docs), DeepSeek Harness (`deepseek-ai/deepseek-harness` packages and READMEs),
Hermes Agent (`NousResearch/hermes-agent` tools sources), OpenClaw
(`openclaw/openclaw` docs). What was not found is listed at the end.

The references for the local profile are Claude Code, Codex, DeepSeek and
Hermes; OpenClaw is the deployed profile's (the human, 2026-09-14). What
the earlier report of 2026-09-03 already took from DeepSeek, Hermes and
OpenClaw (typed results, argument coercion, loop guards, spill) is not
repeated.

## 1. What each reference has, on the questions of step 3

| question | Claude Code | Codex | DeepSeek Harness | Hermes | OpenClaw | this harness today |
|---|---|---|---|---|---|---|
| read a file | `Read(path, offset, limit)` in **lines**, line-numbered output, ~2000 lines default, long lines cut, PDFs by page, notebooks, images | no tool: the shell (`sed -n`, `cat`), output budget 10k tokens | `read(file_path, offset, limit)` lines, 1-based, `N: text`, footer "Use offset=… to continue", byte and line-length caps, streaming for big files | `read_file(path, offset, limit)` lines, 1-based, line-numbered, hint "Use offset=…", binary detection, UTF-16 | `read` (exists, shape not read) | `read_file(path, offset)` in **characters**, no line numbers, 20,000 chars per page |
| search content | `Grep` on ripgrep: modes files/content/count, `glob`/`type` filters, `head_limit`, `offset`, multiline, respects .gitignore | no tool: "prefer `rg`" in the prompt | `grep` over a packaged ripgrep, fixed argument templates, caps on matches, line bytes, raw output; paths relative to the workdir | `search(pattern, target="content")` (ripgrep use not confirmed) | not found | none |
| find files | `Glob(pattern, path)`, sorted by mtime, 100 results | `rg --files` | `glob` with a results cap and sampling across top-level entries | `search(pattern, target="files")` | not found | `list_files` one level, 200 entries, no paging |
| edit | `Edit(old_string, new_string, replace_all)`, exact and unique, read-before-edit enforced by the harness, `old_string` kept minimal | `apply_patch`: V4A freeform grammar (Begin Patch / Update File / Add / Delete / Move / `@@` context hunks), fuzzy first match, result "Updated the following files: M …" | `edit(old_string, new_string, replace_all)`, unique unless replace_all; read-before-edit as a policy plugin, not prompt text; a second editor with `view/create/str_replace/insert` | `patch_replace` fuzzy unique unless replace_all, idempotent re-apply; **and** `patch_v4a` | `apply_patch` V4A, workspace-only by default | `edit_file(old_text, new_text)` exact, unique, no replace_all, no patch |
| write result | not found (a diff is not documented) | "Success. Updated the following files: A/M/D path" | not found | `WriteResult` with sha256 | not found | "created/overwrote path (N characters)" |
| shell timeout | default 2 min, max 10 min; a long command is **moved to the background** with a message | no timeout: yields after `yield_time_ms` (10 s default) with a session id, then `write_stdin` polls; 5-min background cap | `timeoutMs` per call; `run_in_background` → job id, no timeout, `job_output/list/kill` | 180 s default, 600 s foreground cap; `background` → session id; `notify` on exit or on an output pattern | `timeoutSeconds` default 1800, 0 = none; `yieldMs` auto-backgrounds; `process` tool | 120 s default, 600 s max **clamped silently**; `background=true` locally only |
| shell output | ~30,000 chars inline, then saved to a file with a preview; failures 10,000 then head+tail; benign exit-1 list (`grep`, `rg`, `find`, `diff`, `test`) | 10,000-token budget per call, 1 MiB session cap | tail kept, **full output spilled to a file whose path is reported** | auto-truncated, **full text saved to a file**; "never pipe through tail/head" | chunked to the chat | 16,000 chars, middle cut, nothing saved |
| "use this instead of" | yes, strongly: "ALWAYS use Grep … NEVER invoke grep or rg as a Bash command"; but the newer **compact** descriptions served to newer models drop the exhortation ("Prefer this over grep/rg via Bash — results integrate with the permission UI") | no dedicated tools, so the prompt says "prefer rg" | one line: "Use the read tool — not shell commands like cat" | contract style; cross-tool references are **injected at definition time, never written into a static description** | not read | four times in the filesystem descriptions and once in the shell's |
| Windows | a separate `PowerShell` tool; Bash is Git Bash when present; sandbox applies to Bash and its children | native install, restricted-token + ACL sandbox (`[windows] sandbox = elevated/unelevated`), shell defaults to **PowerShell**, a 10 s yield floor on Windows; caveat: cannot stop writes where Everyone has write | not found | native install; the terminal says "the host OS, shell and backend are stated in your environment section — write commands for THAT platform" | Docker/SSH backends | `cmd` under a restricted token; Cygwin tools die (ISS-0068) |
| approvals | rules per tool and path, "don't ask again" saved per repo, modes default/acceptEdits/plan/auto/bypass; a built-in read-only Bash set never prompts | `on-request`; sandbox read-only / workspace-write / full; prefix rules in Starlark; "don't ask again for commands starting with X" | approval seams fail closed; permission presets; sandbox and approval are two separate controls; escalation needs a prior denial | dangerous-pattern gate, once/session/always, allowlist persisted; skipped when provably isolated | `deny/allowlist/ask/auto/full`, inline Telegram buttons, standing grants with expiry | careful mode asks every mutating call; a write outside the folder asks |
| parallel calls | yes, prompt asks for it | yes, per-tool read/write lock | `maxParallelToolCalls: 10`; reads concurrent, mutations sequential | via `execute_code` | not read | sequential (step 32) |
| subagents / tool search | `Agent`, `ToolSearch` | `spawn_agent` family, `tool_search` over deferred tools (BM25) | subagent package, in-process or ACP/Codex/Claude backends | `delegate_task` | `sessions_spawn` | none (step 32) |

## 2. What follows for step 3

The four local references agree on more than they differ:

1. **Reading is by lines, numbered, with `offset`/`limit`.** Three of four
   have exactly this tool; the fourth (Codex) gets it from `sed -n`. Line
   numbers are what makes an edit target cheap to name and a search hit
   cheap to open. Our character pages are the odd one out.
2. **Search across a tree and file discovery are tools, on ripgrep where
   one exists.** Claude Code, DeepSeek and Hermes have them; Codex says
   "prefer rg". Results are `path:line: text`, capped, paged, and respect
   `.gitignore`.
3. **Exact-replace with `replace_all` is the small edit; a V4A patch is
   the big one.** Codex, Hermes and OpenClaw share the V4A grammar; Claude
   Code removed its `MultiEdit` and keeps the single replace. Hermes has
   both, which is the shape that covers everything.
4. **A cut output is spilled to a file whose path is returned.** DeepSeek,
   Hermes and Claude Code all do this; none cuts the middle and drops it.
5. **A long command is backgrounded, not killed.** Claude Code moves it
   at the timeout, Codex yields after seconds, DeepSeek and Hermes return
   a job or session id. Nobody clamps a requested timeout silently.
6. **The platform is stated to the model, and the shell is the
   platform's.** Codex and Claude Code on Windows default to PowerShell;
   Hermes says "write commands for THAT platform".
7. **Descriptions are drifting from exhortation to contract.** Claude
   Code's compact variants and Hermes's injected cross-references are the
   current shape; our AGENTS.md rule of 2026-09-14 is the same choice.

## 3. Options, with a recommendation

### 3.1 `read_file` by lines (recommended)

`read_file(path, offset=1, limit=<default>)` in lines, 1-based; each line
as `N: text` (Claude Code's `cat -n` shape; DeepSeek's `N: text`); a line
longer than a set width cut with `… (line truncated)`; the footer
`(showing lines a–b of N; read_file again with offset=b+1)` or `(end of
file, N lines)`. The default `limit` and the byte cap derived from the
request budget (AGENTS.md, derived limits), not constants. Images as now.
Documents keep `read_document`'s sections.

Alternative: keep character offsets and add line numbers only. Rejected:
every reference pages by lines, and an `edit_file` target or a search
hit is a line.

### 3.2 `search_files` and `find_files` (recommended)

Two tools, as Claude Code and DeepSeek, not one with a `target` switch
(Hermes): the arguments differ.

- `search_files(pattern, path=".", glob=None, mode="content"|"files"|"count", limit, offset, ignore_case, multiline)`:
  regex over the tree; `path:line: text` lines for content, paths for
  files, `path: n` for count; capped and paged with the same footer as
  `read_file`; `.gitignore` respected, hidden and `.git` skipped.
- `find_files(pattern, path=".", limit, offset)`: glob (`**/*.py`),
  newest first, paged.

Engine: ripgrep when a binary is on the machine (`rg` on PATH, or the one
VS Code and Git Bash sometimes carry), else a Python walker with the same
output shape (`re` over files, `.gitignore` honoured through `git ls-files
--cached --others --exclude-standard` when inside a git repository, a plain
walk otherwise). The model sees one contract either way. DeepSeek ships
its own ripgrep binary; that is the alternative if the fallback proves
too slow on a large tree, measured, not assumed.

Deployed: the same two tools inside the root; the container has no
ripgrep, the Python engine runs. Telegram: nothing changes, two more tools
in the toolbox.

### 3.3 `edit_file` gains `replace_all`; `apply_patch` (V4A) added (recommended)

- `edit_file(path, old_text, new_text, replace_all=False)`: unique unless
  `replace_all`; result names the line(s) changed and the count.
- `apply_patch(patch)`: the V4A grammar as Codex, Hermes and OpenClaw
  have it (`*** Begin Patch`, `*** Update File: p`, `*** Add File: p`,
  `*** Delete File: p`, `*** Move to: p`, `@@ context`, `-`/`+` lines,
  `*** End Patch`), several files and several hunks in one call; exact
  match first, then whitespace-trimmed (Codex's `seek_sequence`); a hunk
  that does not match fails the whole patch with the unmatched context
  quoted, nothing written (all-or-nothing, as Claude's `MultiEdit` was).
  Result: `Updated: M path (+a −b), A path, D path`. Mutating; asks
  outside the folder like `write_file`.

Alternative: `edit_file` with an `edits[]` array (the removed
`MultiEdit`). Rejected: three references converged on V4A, and it is one
string argument, which the served-parser history of this project favours.

### 3.4 What a write returns (recommended)

`write_file` on an existing file returns `overwrote path (+a −b lines of
N)` from a line diff, and `created path (N lines)` for a new one;
`edit_file` returns the line numbers touched. The full diff for the person
is the adapter's (step 34: a diff on approval in Chainlit), as the
references show it in the UI, not in the tool result. None of the
references returns the full diff to the model.

### 3.5 Descriptions and the brief (recommended)

The five "use this instead of …" sentences go; each filesystem tool says
what it returns (a numbered page; matches with line numbers; the files
changed), and the shell says what it is (a command on this machine, in
the folder). The `write_file` fence and argument-order coaching (ISS-0001)
goes too; the parser seam already handles it (`base.py`). The brief names
the platform: the operating system, the shell (`cmd`/PowerShell on
Windows, `sh` elsewhere), the Python executable's name (`python` on
Windows, `python3` in the container), as Hermes does. Tests pinning the old
wording (audit §4) are changed with it.

### 3.6 `fetch_page` under the local `open` flag (recommended)

Locally, `open=True` lifts the public-address and 80/443 rule for
`fetch_page` the way it already does for `use_page` (ISS-0074): any host,
any port on the person's machine, the scheme rule stands. Deployed
unchanged. This is the asymmetry PROJECT_MAP now records as a gap.

### 3.7 ISS-0068 and the Windows shell: two options

Under the restricted token every Cygwin/MSYS process (`sh`, `ls`, `find`,
`grep` from Git Bash) dies at start on `CreateFileMapping … error 5`:
Cygwin creates a named shared section under the user's SID, and the
token our boundary builds denies it. Codex has the same class of sandbox
and answers by defaulting the shell to PowerShell; Claude Code gives a
separate PowerShell tool and runs Bash as Git Bash **outside** any token
restriction (its sandbox is a different mechanism).

- **Option A, PowerShell as the Windows shell.** `run_command` runs
  `powershell -NoProfile -Command` under the same token; the brief says
  so. Cygwin tools are simply not on the route; `ls`, `cat`, `grep`
  become `Get-ChildItem`, `Get-Content`, `Select-String`, which GLM knows.
  With `search_files`/`find_files`/`read_file` as tools (3.1–3.2) the
  model rarely needs them anyway. Cost: a day, including the quoting seam
  (`shell_windows.py:157` mis-quotes `"` today) and the tests.
- **Option B, make Cygwin run under the token.** A time-boxed try: the
  restricted token keeps the user's SID enabled but adds a restricting
  SID list; Cygwin's shared section needs the user SID's own DACL. If
  `SetTokenInformation`/`CreateRestrictedToken` flags can leave that
  path open without opening writes outside the folder, Git Bash tools
  work as they are. Unknown outcome; half a day to find out, and the
  boundary's tests must still hold.
- **Recommended:** A now, B recorded as a Not-started item. The
  references chose A's shape; the boundary stays untouched; the model is
  told the truth about its shell.

### 3.8 Out of this step, noted for the queue

Line-based paging and spill files for `run_command` (step 31, with the
timeout clamp), parallel calls and `find_tools` (32), approvals with a
diff and "don't ask again" (34), subagents (Not started). Hermes's
`notify` on a background command (exit or an output pattern) and Codex's
`get_context_remaining` tool are two small ideas worth a line in Not
started.

## 4. Acceptance for the step

A local coding mini set, beside the deployed numbers: a task that needs a
tree search, a multi-file patch, a long file read by lines, a dev server
started and fetched on localhost, and a plain PowerShell command on
Windows; checks read outcomes (files, exit codes, printed values), never a
tool route. The offline suite covers each tool's contract, the V4A parser
against Codex's own test shapes, and `tests/test_profiles.py` still shut.

## 5. Not found, in the sources read

Claude Code: the Edit/Write result body; binary-file reading; whether the
approval prompt shows a diff. Codex: the head/tail split of output
truncation; a chunked-reading instruction; Cygwin/Git Bash issues.
DeepSeek: Windows support; the numeric defaults of its read and search
caps. Hermes: plan mode; the `search` tool's engine and limits. OpenClaw:
a glob/tree-search tool; the `read`/`write`/`edit` result shapes.
