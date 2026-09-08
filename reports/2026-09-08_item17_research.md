# Item 17, research: where a command runs, where what it installs lands, a folder per task

Written 2026-09-08 before any change. Nothing here is approved; every option
is a draft until the human says yes. Read against commit `1442e8b`.

Sources: `app/tools/shell.py` (`command_environment`, `LocalRunner`,
`ContainerRunner`, `describe`), `deploy/modal/control_app.py` (`run_command`,
`ModalRunner.where`, `command_image`), `app/capabilities.py` (the
run_command brief line), `app/context/window.py` (`WORKING_METHOD`),
`scripts/loop_live.py` (scenario C), ISS-0053, ISS-0058, ISS-0043;
`reports/2026-09-03_v2_tool_system_references_and_queue.md` for the
references (Claude Code, Codex, Hermes, DeepSeek Harness: none activates
anything for the developer; a venv is used when a command names it).

## 1. What is there today

**Deployed.** `run_command` is a Modal Function with the Volume mounted at
`/workspaces`, no secret, `cpu=1`, 2 GiB, `scaledown_window=180`. The
worker's `ModalRunner` commits the Volume, calls it, reloads. Inside, the
Function resolves `cwd = (/workspaces/<user>).resolve()` — `resolve()`
follows the mount's symlink, so the command's working directory is the long
real path (`/__modal/volumes/vo-…/<user>`), and `command_environment` sets
`HOME`, `USERPROFILE`, `TEMP`/`TMP`/`TMPDIR` (to `<workspace>/.tmp`),
`APPDATA`, `LOCALAPPDATA` from it. Consequences seen live:

- ISS-0058: Chrome's socket path (`$TMPDIR/…`) exceeds 108 bytes; npm's
  cache (`$HOME/.npm`) on the Volume carries a uid npm refuses ("please run
  sudo chown -R 0:0"). The model found `TMPDIR=/tmp` and
  `npm_config_cache=/tmp/.npm` itself, ten commands in.
- ISS-0053: what goes to `/tmp` or `apt` to escape that is gone with the
  container (three minutes idle, or a second container in a burst). The
  first result in a fresh container carries "new environment: nothing
  installed by earlier commands is present", which the roadmap already
  decided to drop.
- `ContainerRunner.prepare` makes no venv (`venv_on_path=False`): `python3`
  and `pip` are the image's uv venv (ISS-0043), and a `pip install` lands
  in the container, gone with it. The `where` text says so and says "a venv
  there is used only when a command names its own python".

**Local.** `LocalRunner.prepare` runs `ensure_venv(cwd)` before every
command: a `.venv` is created at the workspace root on first use and put
first on `PATH` with `VIRTUAL_ENV` set. `HOME` and temp are the workspace
and `<workspace>/.tmp`. On Windows the write boundary (`shell_windows`) is
the only reason a `pip install` cannot reach the machine's Python; the venv
at the root is what makes `pip install` work at all, and it fills the root
whatever the task, and hides the machine's own packages (item 7's open
note). `LocalRunner.where` promises "`python` and `pip` there are the
workspace's own virtual environment".

**The prompt.** Nothing says where a task's files go. `WORKING_METHOD` says
"prefer what is already there over installing something new" and no more;
the brief's run_command line is one sentence with `where` in it.

## 2. What the item asks, and the one property behind it

Both defects are one thing: the harness decides where a command's *home*
and *temp* are, and today it decides "the workspace, on the Volume" for
both, which is right for files and wrong for everything a tool writes for
itself (sockets, caches, profiles). The property to state: **the workspace
is where the work lives; the container (or the machine) is where a tool
keeps its own temporary things; what must survive is in the workspace by
the model's choice, in the task's folder.** The model is told this once, in
literal words, and the suite measures whether it follows.

## 3. Options

### 3.1 Deployed: home and temp off the Volume

**Option D1 — home and temp are the container's.** `HOME=/root` (the image
runs as root), `TMPDIR=/tmp`, and the caches named explicitly so they do not
follow `HOME` back onto a Volume path if a tool reads `$HOME` from the
workspace: `XDG_CACHE_HOME=/tmp/.cache`, `npm_config_cache=/tmp/.npm`,
`PIP_CACHE_DIR=/tmp/.pip`. The working directory stays the workspace, but
the *unresolved* `/workspaces/<user>` rather than `.resolve()`'s long real
path, so a socket a tool binds relative to cwd has a short path too. Chrome
launched from the workspace then binds under `/tmp`. npm's cache is a
fresh directory in a fresh container: no uid to argue with. Cost: the cache
is lost with the container, so `npm i puppeteer` in a new container
downloads again; three minutes of idle keep it for a burst, which is the
same today.

**Option D2 — home stays on the Volume, only temp and caches move.** Keeps
dotfiles a tool writes to `$HOME` (an `.npmrc`, a `.gitconfig` the model
wrote) across containers. Costs the uid problem for anything that keeps a
cache under `$HOME` without honouring `XDG_CACHE_HOME` (npm honours
`npm_config_cache`; pip honours `PIP_CACHE_DIR`; Chrome honours `TMPDIR`
for the socket and `XDG_CONFIG_HOME` for its profile). More names to know;
the failure returns with the next tool that keeps its own cache path.

Recommendation: **D1.** The workspace is the person's, not the tool's, and
a dotfile the work needs is written in the task's folder like any file.

**The "new environment" line** goes (the roadmap's word). What replaces it
in `where`: "the container is disposable: what a command installs into it
is gone by the next turn; a virtual environment and node packages made in
the task's folder in your workspace stay". `describe()` no longer prints the
line, and `fresh` stays in the result for telemetry.

### 3.2 Local: no venv made for the model

**Option L1 — nothing activated, as deployed.** `LocalRunner.prepare` stops
calling `ensure_venv`; `venv_on_path` is false in both runners and the
parameter goes; `ensure_tmp` stays (Windows needs a writable temp inside
the ACL). `python` and `pip` are the machine's. On Windows the boundary
refuses `pip install` into the machine's Python, so the model's only way to
install is a venv it makes itself — which is what the prompt line tells it.
`LocalRunner.where` says it literally: "`python` and `pip` are this
machine's; a `pip install` outside a virtual environment is refused by the
write boundary, so make a venv in the task's folder and run its python".
A `.venv` already at the root of an existing workspace is left there and no
longer put on `PATH`; `scripts/migrate_workspace.py` is where a clean-up
would go if the human wants one.

Detail: `APPDATA` is redirected into `<workspace>/.tmp/appdata`, so
`pip install --user` would land in the workspace's temp. Not a path the
prompt names; the brief does not mention it.

**Option L2 — keep a venv, but make it per task folder automatically.** The
harness cannot know the task's folder; this is the hard-coded workflow the
primary principle forbids. Rejected.

Recommendation: **L1.**

### 3.3 The prompt line, both profiles

One paragraph, in the brief beside the run_command line because it is about
the workspace and every profile with a workspace gets it (a grant without
`run_command` still has files):

> Each piece of work gets its own folder in your workspace, named for the
> task. Its files, its virtual environment and the packages it installs go
> in that folder and nowhere else. When the person continues the same
> work, use that folder again.

Literal conditions and actions, no figure of speech. Whether the model
follows it is the suite's measurement.

### 3.4 Acceptance: scenario C extended with an install

Scenario C today: write `primes.py`, run it, report the output. Extended:

> In my workspace, in a folder for this task, make a virtual environment,
> install the package `tabulate` into it, write primes.py that prints the
> prime numbers below 50 as a one-column table with tabulate, run it, and
> tell me exactly what it printed.

Checks added: the root holds no `.venv` and no `node_modules`; exactly one
first-level folder contains a venv (`pyvenv.cfg` somewhere below it) and
`primes.py`; the command's output reached the answer. `tabulate` is pure
Python, 30 KB, installs in seconds on both sides. Local run is free;
deployed is the `scenarios` Function — a worker and a command container,
the human's gate, about a minute of CPU.

What the extended C does not measure: whether the model reuses the folder
on a second message ("continue the same work"). A second turn in the same
chat, "now print them below 100", with the check that no second folder
appeared, is a cheap addition if wanted.

## 4. Unknowns that need a live run

- Whether Chrome's socket path is short enough after D1 when launched from
  the workspace — `TMPDIR=/tmp` should be decisive (ISS-0058 saw the model
  fix it with exactly that), and the unresolved cwd is a second measure.
  One deployed command (`node -e` with puppeteer, or `chromium --headless
  --dump-dom about:blank`) says yes or no.
- The uid on Volume files for a cache *inside the task's folder*
  (`node_modules` written by npm into the workspace): npm writes them,
  the next container reads them; the 2026-09-06 failure was the cache
  directory's ownership check, which `node_modules` does not have.

## 5. Work, if approved

- `app/tools/shell.py`: `command_environment(workspace, *, home, tmp)` —
  the runner says where home and temp are; `LocalRunner.prepare` without
  the venv; `venv_on_path` and `VENV` gone; `describe()` without the line;
  `LocalRunner.where` rewritten.
- `deploy/modal/control_app.py`: the Function passes the unresolved cwd;
  `ContainerRunner` gives `home=/root`, `tmp=/tmp`, the three cache
  variables; `ModalRunner.where` rewritten.
- `app/capabilities.py`: the folder paragraph.
- `scripts/loop_live.py`: scenario C extended.
- Tests: `tests/test_shell*.py` (environment, no venv made, no line), the
  brief test, the deployed `where` text test if there is one.
- Docs: `docs/OPERATIONS_MAP.md` run_command row, `docs/PROJECT_MAP.md`
  "Commands", `DECISIONS.md` (amends "the workspace's venv, activated" of
  2026-09-04), ISS-0053 and ISS-0058 status, roadmap 17.
- Then: local scenario C; deploy (gate); deployed scenario C (gate); the
  Chrome probe (gate).

## 6. The references, read on the human's word (2026-09-08)

Read from source through the GitHub API: Hermes Agent
`tools/environments/local.py`, `base.py`, `local_pythonpath.py`,
`tools/terminal_tool.py`, `agent/prompt_builder.py`, `agent/runtime_cwd.py`,
`hermes_constants.py`; DeepSeek Harness `docs/subsystems/sandbox.md`,
`packages/shell/tool-bash`, `bash-sandbox`, `shell-env`,
`packages/sandbox/sandbox-windows-acl` READMEs; OpenClaw
`docs/gateway/sandboxing.md`, `src/agents/sandbox/{docker,docker-backend,
config,constants,workspace}.ts`. Copies in the session scratchpad.

### 6.1 Local: Hermes and DeepSeek

| Concern | Hermes (local backend) | DeepSeek Harness (local / sandboxed bash) |
|---|---|---|
| Where a command runs | The host shell, `bash -c` per call over a session snapshot (env, functions, cwd persist). cwd is the configured one or the launch dir; a deleted cwd falls back to the nearest usable ancestor. | A fresh `bash -c` per call; nothing persists; `workdir` per call, resolved against the session's immutable cwd, which is the workspace-write boundary. |
| `HOME` | The person's **real home** (`TERMINAL_HOME_MODE=auto`; a container gets `{HERMES_HOME}/home`). Never the workspace. | Untouched: the real home. The harness's own facts travel as a `DSH_*` namespace rebuilt per call (`DSH_HOME`, `DSH_SESSION_ID`), not as `HOME`. |
| Temp | Not redirected. Its own artifacts under `{HERMES_HOME}/cache/terminal`, pruned after 72 h. | **A private random temp directory per live session/workspace pair**, `TMP`/`TEMP` rewritten to it, writable under the ACL, revoked on dispose; on Linux `/tmp`. Never the workspace, never the ambient temp root. |
| The agent's own Python | **Stripped**: Hermes-owned `PYTHONPATH` entries and the active-venv marker variables (`VIRTUAL_ENV` and kin) are removed from the child env, so the model's `python` is the machine's, never the agent's venv. `PATH` repaired to a sane list when the launcher's was minimal. | Not addressed; the `DSH_*` namespace is the only managed thing. |
| A venv for the model | None made. The tool text: "Environment state persists: activate a virtualenv or export variables once per session, not before every command." | None made; no install rule. |
| Boundary | None on the host. | `workspace-write`: the workspace root plus the temp area; a denial is a result fact and may be escalated once with a justification through approval. |
| What the prompt states | `Host: <OS>`, `User home directory: <real home>`, `Current working directory: <cwd>`, plus a Windows note that the shell is bash. | The bash guidance section, plus the sandbox state from the policy owner. |

What follows for us, beyond §3.2:

- **`HOME` is the person's real home, not the workspace** (both). What a
  tool reads from `$HOME` — a `.gitconfig`, an `.npmrc`, `.ssh` for git —
  is the person's, which is the point of the local profile. Today we set
  `HOME` to the workspace, which hides all of it.
- **Temp is a private directory outside the workspace** (DeepSeek), granted
  by the write boundary like the workspace, and `APPDATA`/`LOCALAPPDATA`
  under it so a tool that writes "for the user" on Windows lands there
  rather than in the real profile, which the boundary would refuse. The
  workspace stops carrying `.tmp`, so it holds work and nothing else.
- **The agent's own venv is stripped from the child's environment**
  (Hermes). `command_environment` passes `PATH` through untouched, so an
  agent launched from its activated `.venv` hands that venv's `Scripts` to
  the model as `python`. Remove our own venv's directory from `PATH` and
  drop `VIRTUAL_ENV`, `PYTHONPATH`, `PYTHONHOME` when they name it.
- **Nothing activated, nothing made** (both), as §3.2 L1 already says.
- **The brief states host, home, cwd** (Hermes), which our `where` does in
  its own words; add the home.

### 6.2 Deployed: OpenClaw

| Concern | OpenClaw (Docker sandbox) |
|---|---|
| A container per | session (`scope: session`), or per agent, or one shared; kept and reused, pruned after 24 h idle or 7 days. Nobody starts one per command. |
| Working directory | `/workspace` — the **sandbox workspace**, a directory of the session's own under `~/.openclaw/sandboxes`, seeded from the agent workspace's bootstrap files, deleted with the container. |
| The person's workspace | Mounted apart: read-only at `/agent` (default `ro`), read-write at `/workspace` when allowed, hidden with `none`. Credential roots (`~/.npm`, `~/.ssh`, `~/.config`, …) refused as bind sources. |
| `HOME` | The image's: a non-root `sandbox` user, `WORKDIR /home/sandbox`. Never the mounted workspace. Chromium's profile is `${HOME}/.chrome`. |
| Temp | `readOnlyRoot: true` with **tmpfs at `/tmp`, `/var/tmp`, `/run`** — the container's own, gone with it. |
| Environment | Not inherited from the host; only `sandbox.docker.env`, sanitized, through an env file. |
| Installs | "System package installation … is image provisioning, not normal sandbox-turn behavior." A custom image, or `setupCommand` once per container (`sh -lc`, needs network, a writable root and root). "Project-local dependencies can be installed in a writable workspace when the operator enables network egress." Network `none` by default. |

What follows for us: **§3.1 D1 is OpenClaw's shape** — home and temp are
the container's, the working directory is the mounted workspace, the
environment is only what the harness sets. Two things OpenClaw does that
we do not: a non-root user (ours runs as root; a change to the image, worth
its own line, not this item), and a container kept per session for a day
(ours dies after three idle minutes; the folder-per-task venv is our answer
to that, and OpenClaw's "project-local dependencies in a writable
workspace" is the same answer).

## 7. Recommendation, revised

Deployed, D1 as in §3.1, which is OpenClaw's shape. Local, L1 as in §3.2
with the three refinements of §6.1: `HOME` is the real home, temp is a
private per-session directory outside the workspace under the boundary,
and the agent's own venv is stripped from the child's `PATH` and markers.
The prompt paragraph of §3.3 unchanged. The acceptance of §3.4, plus one
check on the local side: the workspace root holds no `.tmp` and no `.venv`
after scenario C.

## 8. Built 2026-09-08, and what the local check found

Built as §7 says (commit `e8a052f` and the two fixes below). The first
local scenario C ran out of its 400 s: the model's `python -m venv` failed
and its `pip install` hung. Two defects of the boundary, found by hand with
`LocalRunner` (scratchpad probes), both general and both fixed:

1. **`python -m venv` could not bootstrap pip.** `venv` runs `ensurepip`
   with `-I`, which reads no `PYTHONPATH`, so the runner's `sitecustomize`
   (the 0o700 accommodation) was not there; `ensurepip` copies its bundled
   wheel into a `mkdtemp` directory and was refused. Until this item the
   agent made the venv outside the boundary, so it never showed. Fix: the
   `sitecustomize` also wraps `venv.EnvBuilder.setup_python` and writes
   itself into every new venv's `Lib/site-packages`, which is on that
   venv's path in every mode, before pip is set up. Test:
   `test_a_venv_the_model_makes_under_the_boundary_has_pip`.
2. **`pip install` took 240 s and was killed.** pip's self-version check
   writes its HTTP cache under `platformdirs.user_cache_dir`, which on
   Windows reads the shell folder through ctypes and not `LOCALAPPDATA`;
   the real profile is refused by the boundary, and `tempfile` on Windows
   retries a `PermissionError` ten thousand times. Fix: `PIP_CACHE_DIR`
   under the runner's temp, both profiles. Probably the mechanism behind
   the three 120 s `pip install` of 2026-09-04 as well.

With both: `python -m venv` 2.2 s, `pip install tabulate` 3.2 s under the
boundary. Scenario C then passed 7/7 locally in 56 s (run `live-40`, 7
model calls, 38,466 tokens in): `set_goal`, three commands, `write_file`, a
command; `primes_task/` holds `venv` and `primes.py`, the root holds
nothing else. Deployed C and the Chrome probe wait for the deploy gate.

## 9. Deployed 2026-09-08: scenario C and the Chrome probe

Deployed at 09:1x UTC (34 s). Scenario C deployed, run
`deployed-181da5a2-40`: 7/7, 62.7 s, 4 model calls, 21,531 tokens in;
`run_command`, `write_file`, `run_command` — the venv and `primes.py` in one
task folder, the root empty.

The probe, four calls of the `run_command` Function in one container
(scratchpad `probe_chrome*.py`, workspace `loop-live-check`):

- `HOME=/root TMPDIR=/tmp`; a Unix socket binds under `$TMPDIR`
  (`/tmp/probe.sock`, 15 bytes). A socket under the workspace fails with
  `Errno 95 Operation not supported`: the Volume cannot hold a socket at
  all, whatever the path's length — so `$TMPDIR` is the whole answer to
  ISS-0058's socket, and the unresolved cwd is moot: the shell's `$PWD` is
  the real `/__modal/volumes/vo-…` path regardless, because the kernel
  resolves the mount's symlink. The code keeps the mount path (harmless).
- `npm config get cache` → `/root/.npm`; `npm cache verify` clean. No uid
  complaint.
- Chrome (puppeteer's `chrome` 152, installed into `/root/.cache/puppeteer`
  by `npx puppeteer browsers install chrome`; its shared libraries by
  `apt-get install` — root in the container, ~15 s) launched from the
  workspace with `--headless=new --no-sandbox --dump-dom` and printed the
  page: `<html><head><title>probe</title></head><body><h1>ok</h1></body></html>`.
  "Socket path too long" (ISS-0058) is gone. dbus and crashpad errors on
  stderr are noise. The container was reused across the four calls
  (`fresh False` after the first), so what the first call installed served
  the rest — three minutes of idle, as before.

What stays true and is said in the brief: the libraries Chrome needs are
not in the command image, and the install lives only as long as the
container. A browser for the model is item 21's renderer, not this.
