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
