"""Running a command in the workspace: one tool, one runner behind it.

`run_command` is the one tool; Python, `pip`, `node`, `git` are commands it
runs, not tools of their own. Where a command runs is the `Runner`'s business
and differs by profile: on the person's own machine it is a process in the
workspace with a reduced environment (`LocalRunner`); deployed it is a Modal
Function beside the renderer that holds no secret (`ModalRunner` in
`deploy/modal/control_app.py`, which runs a `LocalRunner` inside that
container). The tool, the result the model reads and the codes are the same
in both.

What a command gets: the workspace as its working directory, the person's
own home (the container's, deployed), a private temp directory that is not
the workspace, and an environment reduced to what a shell needs. Nothing from
the process that started the agent — no `.env` value, no token, not the
agent's own virtual environment — is passed on. Nothing is activated for the
model and no venv is made for it: `python` is the machine's (the image's,
deployed), and a venv is used when a command names it, as in Claude Code,
Codex, Hermes and DeepSeek Harness (roadmap 17, 2026-09-08). Home, temp and
the workspace are three different places on purpose: home is what a tool
reads as the person's, temp is what a tool writes for itself, and the
workspace is the work (ISS-0053, ISS-0058: home and temp on the deployed
Volume broke Chrome's socket path and npm's cache ownership). The runner says
in `where` what survives between turns, because that differs: on the
person's machine everything, in the deployed container only the workspace.

What a command may change is, by the references' one shared property, the
workspace and nothing else (Codex's writable roots, Claude Code's sandbox on
macOS and Linux). On native Windows that boundary is a write-restricted
token, the way DeepSeek Harness does it (`app/tools/shell_windows.py`): the
operating system refuses a write anywhere the workspace's ACL does not allow,
whatever the command — a `pip install` into the machine's Python included,
which is the case the human ruled out on 2026-09-04. No rule about any
installer. Elsewhere (a Linux box running the local profile) there is no
boundary yet, and the brief says so.

A non-zero exit is a result, not a failure (`docs/v2_tool_system.md`, "Failure
is not the same as an unwanted result"). The failures are the runner's own: the
command did not finish in time and was killed, or could not be started at all.
`DECISIONS.md` 2026-09-04.
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .base import Tool, ToolError

try:  # the write boundary exists on Windows only; see shell_windows.py
    from . import shell_windows
except ImportError:  # pragma: no cover - not Windows, or pywin32 missing
    shell_windows = None  # type: ignore[assignment]

# The family's own codes.
COMMAND_TIMEOUT = "shell.timeout"
COMMAND_NOT_STARTED = "shell.not_started"

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
# What the model reads back of a command's output: below the executor's own
# 32k backstop, with the tail kept because a build says what failed at the end.
MAX_OUTPUT_CHARS = 16_000
TAIL_CHARS = 4_000

# What a shell needs and nothing else. `SYSTEMROOT` and `COMSPEC` are what
# `cmd` itself needs to start on Windows. Home and temp are set by the runner,
# never passed through.
_PASSED = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")


def own_venv_bin() -> Path | None:
    """The scripts directory of the venv this agent runs in, or None.

    Hermes strips its own venv's markers from the child's environment for the
    same reason: an agent launched from its activated `.venv` would otherwise
    hand that venv's `python` to the model as the machine's.
    """

    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        return None
    return Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")


def _without_own_venv(path: str) -> str:
    own = own_venv_bin()
    if own is None:
        return path
    mine = os.path.normcase(os.path.abspath(own))
    return os.pathsep.join(
        entry for entry in path.split(os.pathsep)
        if entry and os.path.normcase(os.path.abspath(entry)) != mine
    )


# CPython on Windows gives a directory made with mode 0o700 — which is what
# `tempfile.mkdtemp` asks for, and through it pip's build and download
# directories — an explicit owner-only ACL (SYSTEM, Administrators, OWNER
# RIGHTS) in place of the inherited one. Under the write-restricted token that
# directory is unusable, and `tempfile` then tries thousands of names before
# giving up, which read as a hang (P, 2026-09-04: three `pip install` at 120 s
# each). The token's owner cannot be changed to an identity the ACL admits,
# and admitting OWNER RIGHTS to the restricting list opens every file the
# person owns (measured). So every Python a command runs under the boundary
# — the machine's, a venv the model made — gets this `sitecustomize` through
# `PYTHONPATH`, from the private temp directory: a 0o700 directory is made
# like any other and inherits the ACL of wherever it is. One interpreter
# behaviour, accommodated once; nothing else is patched.
SITECUSTOMIZE = """\
# Written by the assistant's command runner (app/tools/shell.py): under the
# write boundary a directory made with mode 0o700 would get an owner-only ACL
# the command itself cannot use. Made like any other directory instead.
import os as _os
import sys as _sys

if _sys.platform == "win32":
    _mkdir = _os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if mode == 0o700:
            mode = 0o777
        return _mkdir(path, mode, dir_fd=dir_fd)

    _os.mkdir = mkdir

    # A venv made here runs `ensurepip` in isolated mode (`-I`), which reads no
    # PYTHONPATH, so this file would not be there for it: the bundled wheel is
    # copied into a 0o700 temp directory and refused (roadmap 17, 2026-09-08).
    # A venv's own site-packages is on its path in every mode, so every venv
    # made under the boundary gets this file there, before pip is set up.
    try:
        import pathlib as _pathlib
        import venv as _venv

        _setup_python = _venv.EnvBuilder.setup_python

        def setup_python(self, context):
            _setup_python(self, context)
            site = _pathlib.Path(context.env_dir) / "Lib" / "site-packages" / "sitecustomize.py"
            if not site.exists():
                site.parent.mkdir(parents=True, exist_ok=True)
                site.write_text(_pathlib.Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")

        _venv.EnvBuilder.setup_python = setup_python
    except Exception:  # noqa: BLE001 - a venv that cannot be helped is still a venv
        pass
"""


def ensure_tmp(tmp: Path) -> None:
    """A command's temp and profile directories, and the `sitecustomize` on Windows."""

    (tmp / "appdata").mkdir(parents=True, exist_ok=True)
    (tmp / "local").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        site = tmp / "python" / "sitecustomize.py"
        site.parent.mkdir(parents=True, exist_ok=True)
        if not site.exists():
            site.write_text(SITECUSTOMIZE, encoding="utf-8")


@dataclass(frozen=True)
class Finished:
    """What one command came to."""

    exit_code: int
    output: str
    cut: bool
    seconds: float
    # A container that was created for this command, so nothing installed by
    # an earlier one is present. Never true on the person's own machine.
    fresh: bool = False


class Runner(Protocol):
    """Where a command runs. One method; the profile chooses the implementation."""

    # The runner's own account of itself, said once in the brief: which shell a
    # command line is written for, whether a write boundary holds, and what
    # survives between turns. Not guessed by the brief, because it differs.
    where: str

    async def run(self, command: str, cwd: Path, timeout: float) -> Finished: ...


def command_environment(
    workspace: Path, source: dict[str, str] | None = None, *, home: Path, tmp: Path
) -> dict[str, str]:
    """The environment a command gets: what a shell needs, home and temp as told.

    `home` is the person's (the container's, deployed): what a tool reads as
    the user's — a `.gitconfig`, an `.npmrc` — is theirs, and what it writes
    there on Windows is refused by the boundary, so the profile directories
    (`APPDATA`, `LOCALAPPDATA`) point under `tmp` instead, as DeepSeek's
    Windows runner gives a private temp beside the workspace. Nothing is
    activated: the agent's own venv is taken off `PATH`, and a `.venv` in the
    workspace is used when a command names it (run `a7d5c61c`, 2026-09-04: a
    venv left on the Volume, first on `PATH`, made `python3` find fpdf 1.7
    where the brief said fpdf2).
    """

    source = os.environ if source is None else source
    env = {name: source[name] for name in _PASSED if name in source}
    if "PATH" in env:
        env["PATH"] = _without_own_venv(env["PATH"])
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(tmp)
    env["APPDATA"] = str(tmp / "appdata")
    env["LOCALAPPDATA"] = str(tmp / "local")
    # pip finds its cache through the shell folder API, not `LOCALAPPDATA`, so
    # under the boundary it wrote into the real profile, was refused, and
    # `tempfile` then tried ten thousand names (2026-09-08: every `pip
    # install` 240 s). Told where directly.
    env["PIP_CACHE_DIR"] = str(tmp / "pip-cache")
    if sys.platform == "win32":
        env["PYTHONPATH"] = str(tmp / "python")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    env.setdefault("LANG", "C.UTF-8")
    return env


def decoded(raw: bytes) -> str:
    """A command's bytes as text: UTF-8 when it is, else the console's own code page.

    `cmd` and the tools it ships speak the OEM code page (cp866 here), Python
    and node speak UTF-8; reading everything as UTF-8 turned a Russian
    "python3 is not recognized" into mojibake the model could not act on
    (P, 2026-09-04).
    """

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        codec = "oem" if sys.platform == "win32" else "utf-8"
        return raw.decode(codec, errors="replace")


def bounded(text: str, limit: int = MAX_OUTPUT_CHARS, tail: int = TAIL_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = text[: limit - tail]
    return f"{head}\n… [{len(text) - limit} characters left out] …\n{text[-tail:]}", True


def _kill_tree(process: subprocess.Popen) -> None:
    """Stop the command and everything it started."""

    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


class LocalRunner:
    """A process on this machine, in the workspace, with a reduced environment.

    The person's own machine is where Claude Code and Codex run their commands
    on Windows too, with the person as the boundary; this does the same and
    withholds the agent's own environment. The shell is the platform's: `cmd`
    on Windows, `/bin/sh` elsewhere, which is what `shell=True` means.

    Home is the person's; temp is a directory of this runner's own under the
    machine's temp, made on first use and granted to the restricted identity
    beside the workspace (DeepSeek's shape). Nothing is made in the workspace
    but what a command makes there.

    The same class runs the command inside the deployed container, where the
    container is the boundary and home and temp are the container's
    (`ContainerRunner`).
    """

    home: Path = Path.home()

    def __init__(self) -> None:
        system = platform.system() or "this machine"
        shell = "cmd" if sys.platform == "win32" else "sh"
        self.bounded = sys.platform == "win32" and shell_windows is not None
        # Honest either way: the model should know whether a command can change
        # the machine before it writes one.
        boundary = (
            "; a command can write only inside your workspace, the operating system "
            "refuses everything else, a `pip install` into this machine's Python "
            "included"
            if self.bounded
            else "; there is no write boundary here, so keep every change inside your "
            "workspace"
        )
        self.where = (
            f"on this machine ({system}), through {shell}{boundary}. Your home directory "
            f"is {self.home}. `python` and `pip` are this machine's own and nothing is "
            "activated for you: to install a package, make a virtual environment in "
            "the task's folder and run its python. Everything in the workspace "
            "survives between turns"
        )
        self._runs = 0
        self._tmp: Path | None = None

    @property
    def tmp(self) -> Path:
        """This runner's private temp, made on first use; never the workspace."""

        if self._tmp is None:
            # Made with the default mode, not `mkdtemp`'s 0o700, which on
            # Windows would give it the owner-only ACL the boundary cannot use.
            tmp = Path(tempfile.gettempdir()) / f"assistant-command-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            tmp.mkdir(parents=True, exist_ok=True)
            self._tmp = tmp
        return self._tmp

    def prepare(self, cwd: Path) -> None:
        """What a command needs before it runs here: its temp."""

        ensure_tmp(self.tmp)

    def environment(self, cwd: Path) -> dict[str, str]:
        return command_environment(cwd, home=self.home, tmp=self.tmp)

    def _start(self, command: str, cwd: Path):
        env = self.environment(cwd)
        if self.bounded:
            shell_windows.grant_workspace(cwd)
            shell_windows.grant_workspace(self.tmp)
            self._runs += 1
            output = self.tmp / f"run-{os.getpid()}-{self._runs}.out"
            return shell_windows.RestrictedProcess(command, cwd, env, output)
        return subprocess.Popen(  # noqa: S602 - the command is the point
            command,
            shell=True,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
        )

    async def run(self, command: str, cwd: Path, timeout: float) -> Finished:
        started = time.monotonic()
        try:
            await asyncio.to_thread(self.prepare, cwd)
        except (OSError, subprocess.SubprocessError) as error:
            raise ToolError(
                f"the command's temp directory could not be made: {error}",
                code=COMMAND_NOT_STARTED,
            ) from error
        try:
            process = await asyncio.to_thread(self._start, command, cwd)
        except (OSError, subprocess.SubprocessError) as error:
            raise ToolError(
                f"the command could not be started: {getattr(error, 'strerror', None) or error}",
                code=COMMAND_NOT_STARTED,
            ) from error
        except Exception as error:  # noqa: BLE001 - a pywin32 error is not an OSError
            # Never silently unbounded: a boundary that could not be made is a
            # command that did not run.
            raise ToolError(
                f"the command could not be started: {error}", code=COMMAND_NOT_STARTED
            ) from error
        try:
            raw, _ = await asyncio.to_thread(process.communicate, None, timeout)
        except subprocess.TimeoutExpired as error:
            _kill_tree(process)
            partial = decoded(error.output or b"")
            tail, _ = bounded(partial, TAIL_CHARS, TAIL_CHARS // 2)
            raise ToolError(
                f"the command did not finish within {timeout:g} seconds and was killed",
                code=COMMAND_TIMEOUT,
                detail=tail.strip() or None,
            ) from None
        except BaseException:
            # A stop, or the interpreter going down: the command must not
            # outlive the turn that asked for it.
            _kill_tree(process)
            raise
        output, cut = bounded(decoded(raw or b""))
        return Finished(
            exit_code=process.returncode,
            output=output,
            cut=cut,
            seconds=time.monotonic() - started,
        )


class ContainerRunner(LocalRunner):
    """The command's side inside the deployed container: home and temp are the container's.

    OpenClaw's shape: the working directory is the mounted workspace, home is
    the image's, temp is the container's own `/tmp`, gone with it. So a
    socket a tool binds under `$TMPDIR` has a short path, and a cache a tool
    keeps under `$HOME` has the container's uid (ISS-0058). Nothing is
    installed for the model: a venv in the task's folder is its own choice,
    and the `ModalRunner` beside the worker tells it so. `where` is never
    read here — the worker's runner is the one the brief quotes.
    """

    home = Path.home()

    @property
    def tmp(self) -> Path:
        return Path(tempfile.gettempdir())


# What a non-zero exit carries with it, at the moment it happens: DeepSeek's
# remedy on a typed failure, applied to the one result that is not a failure by
# design and is read as one by the model. About every command, not about any
# one error (the human's rule, and their ask, 2026-09-04: "the harness should
# say why not — look at what is there").
UNWANTED_EXIT = (
    "The command did not do what you meant. Read the output above before your "
    "next step: a traceback names the file, the line and the cause, and what it "
    "says to do is the fix, not a reason to start over or give up. Before you "
    "decide something is missing here, check with a command (ls, find, pip show)."
)


def describe(finished: Finished) -> str:
    """What the model reads: the exit code first, then what the command said.

    A non-zero exit ends with the harness's own line about reading it
    (`UNWANTED_EXIT`): the result is not a failure of the tool, and until
    2026-09-04 nothing said what it was.
    """

    # `fresh` is not said: what the container does and does not keep is in
    # the brief once (roadmap 17); a line per fresh container read as the
    # model's own work being gone (ISS-0053).
    lines = [f"exit code: {finished.exit_code}   ({finished.seconds:.1f} s)"]
    if finished.cut:
        lines.append("output (cut in the middle; the beginning and the end are kept):")
    else:
        lines.append("output:")
    lines.append(finished.output.strip() or "(no output)")
    if finished.exit_code != 0:
        lines.append("")
        lines.append(UNWANTED_EXIT)
    return "\n".join(lines)


def shell_tools(root: Path, runner: Runner) -> list[Tool]:
    resolved = Path(root).resolve()

    async def run_command(command: str, timeout_seconds: int = DEFAULT_TIMEOUT) -> str:
        limit = max(1, min(int(timeout_seconds), MAX_TIMEOUT))
        return describe(await runner.run(str(command), resolved, float(limit)))

    return [
        Tool(
            name="run_command",
            description=(
                "Run one shell command in your workspace: python, pip, node, npm, git, a "
                "build, a test, an install. Use it to run, test and check what you make "
                "and to install what that needs; use read_file, write_file, edit_file and "
                "list_files for files, not cat, echo, sed or ls. A non-zero exit code "
                "means the command did not do what you meant: read the whole output "
                "before your next step; a traceback names the file, the line and the "
                "cause, and what it tells you to do is the fix. Before you say something "
                "is missing here, check with a command. The command cannot read from the "
                "terminal: pass answers on the command line or with flags. "
                f"`timeout_seconds` (default {DEFAULT_TIMEOUT}, at most {MAX_TIMEOUT}) "
                "kills it if it runs longer."
            ),
            returns=(
                "the exit code and the output (stdout and stderr), cut with a note when "
                "it is very long; `new environment` when the container is fresh."
            ),
            leaves=(
                "whatever the command wrote in your workspace, which stays; what it "
                "installed outside it may not survive to the next command; the brief "
                "says what does."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command line, as for the shell."},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": f"Seconds before the command is killed; default {DEFAULT_TIMEOUT}.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            run=run_command,
            mutates=True,
            # The executor's own deadline, above the longest the tool allows, so
            # a runner that hangs is still stopped.
            timeout_seconds=MAX_TIMEOUT + 30,
        ),
    ]
