"""Running a command in the workspace: one tool, one runner behind it.

`run_command` is the one tool; Python, `pip`, `node`, `git` are commands it
runs, not tools of their own. Where a command runs is the `Runner`'s business
and differs by profile: on the person's own machine it is a process in the
workspace with a reduced environment (`LocalRunner`); deployed it is a Modal
Function beside the renderer that holds no secret (`ModalRunner` in
`deploy/modal/control_app.py`, which runs a `LocalRunner` inside that
container). The tool, the result the model reads and the codes are the same
in both.

What a command gets: the workspace as its working directory, its home and its
temp, an environment reduced to what a shell needs, and — when the workspace
has a `.venv` — that as `python` and `pip`, the project's environment,
activated, as it would be in a developer's own shell. Nothing from the process
that started the agent — no `.env` value, no token — is passed on. The runner
says in `where` what survives between turns, because that differs: on the
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
import time
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

# What a shell needs and nothing else. `HOME` and its Windows twin point into
# the workspace so that what a command installs "for the user" lands where the
# workspace keeps it. `SYSTEMROOT` and `COMSPEC` are what `cmd` itself needs to
# start on Windows; `TEMP`/`TMP` so tools that write temporary files work.
_PASSED = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")

# The workspace's own Python, and where a command's temp and profile files go.
# Relative to the workspace, so the same layout is on a person's disk and on
# the deployed Volume.
VENV = ".venv"
TMP = ".tmp"


def venv_bin(workspace: Path) -> Path:
    return workspace / VENV / ("Scripts" if sys.platform == "win32" else "bin")


def venv_python(workspace: Path) -> Path:
    return venv_bin(workspace) / ("python.exe" if sys.platform == "win32" else "python")


# CPython on Windows gives a directory made with mode 0o700 — which is what
# `tempfile.mkdtemp` asks for, and through it pip's build and download
# directories — an explicit owner-only ACL (SYSTEM, Administrators, OWNER
# RIGHTS) in place of the inherited one. Under the write-restricted token that
# directory is unusable, and `tempfile` then tries thousands of names before
# giving up, which read as a hang (P, 2026-09-04: three `pip install` at 120 s
# each). The token's owner cannot be changed to an identity the ACL admits,
# and admitting OWNER RIGHTS to the restricting list opens every file the
# person owns (measured). So the workspace's own Python, and only it, gets
# this `sitecustomize`: a 0o700 directory is made like any other and inherits
# the workspace's ACL. One interpreter behaviour, accommodated where that
# interpreter lives; nothing else is patched.
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
"""


def ensure_tmp(workspace: Path) -> None:
    """Where a command's temporary and profile files go, inside the workspace."""

    (workspace / TMP / "appdata").mkdir(parents=True, exist_ok=True)
    (workspace / TMP / "local").mkdir(parents=True, exist_ok=True)


def ensure_venv(workspace: Path) -> bool:
    """The workspace's virtual environment, made on first use. Returns whether it was made now."""

    ensure_tmp(workspace)
    made = False
    if not venv_python(workspace).exists():
        subprocess.run(
            [sys.executable, "-m", "venv", str(workspace / VENV)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        made = True
    if sys.platform == "win32":
        site = workspace / VENV / "Lib" / "site-packages" / "sitecustomize.py"
        if not site.exists():
            site.write_text(SITECUSTOMIZE, encoding="utf-8")
    return made


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
    workspace: Path, source: dict[str, str] | None = None, venv: bool = True
) -> dict[str, str]:
    """The environment a command gets: what a shell needs, home in the workspace.

    `venv` says whether a `.venv` in the workspace goes first on `PATH`. On the
    person's machine it does — the local runner made it, it is the project's
    environment. In the deployed container it does not: the image carries the
    libraries and the brief names them, and a venv left on the Volume by an
    earlier session, first on `PATH`, made `python3` find fpdf 1.7 where the
    brief said fpdf2 (run `a7d5c61c`, 2026-09-04). There, as in Claude Code
    and Codex, nothing is activated for the developer: a venv is used when a
    command names it.
    """

    source = os.environ if source is None else source
    env = {name: source[name] for name in _PASSED if name in source}
    env["HOME"] = str(workspace)
    env["USERPROFILE"] = str(workspace)
    # A command's temp and profile directories inside the workspace, so what
    # tools write "for the user" lands where a command may write.
    tmp = workspace / TMP
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(tmp)
    env["APPDATA"] = str(tmp / "appdata")
    env["LOCALAPPDATA"] = str(tmp / "local")
    # The workspace's own Python first, when there is one: `python` and `pip`
    # are the project's, as in a developer's activated shell. A workspace
    # without a venv keeps the machine's `python`.
    if venv and venv_bin(workspace).is_dir():
        env["PATH"] = os.pathsep.join([str(venv_bin(workspace)), *filter(None, [env.get("PATH", "")])])
        env["VIRTUAL_ENV"] = str(workspace / VENV)
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

    The same class runs the command inside the deployed container, where the
    container is the boundary and `prepare` makes no venv: what is installed
    there must land in the workspace by the model's own choice, and the
    `ModalRunner` says so in its `where`.
    """

    def __init__(self) -> None:
        system = platform.system() or "this machine"
        shell = "cmd" if sys.platform == "win32" else "sh"
        self.bounded = sys.platform == "win32" and shell_windows is not None
        # Honest either way: the model should know whether a command can change
        # the machine before it writes one.
        boundary = (
            "; a command can write only inside your workspace, the operating system "
            "refuses everything else"
            if self.bounded
            else "; there is no write boundary here, so keep every change inside your "
            "workspace"
        )
        self.where = (
            f"on this machine ({system}), through {shell}{boundary}. `python` and `pip` "
            "there are the workspace's own virtual environment, so `pip install` lands "
            "in the workspace and nowhere else; node packages go in the workspace too. "
            "Everything in the workspace survives between turns"
        )
        self._runs = 0

    # Whether a `.venv` in the workspace is put first on `PATH`; see
    # `command_environment`.
    venv_on_path = True

    def prepare(self, cwd: Path) -> None:
        """What the workspace needs before a command runs here: its temp, its venv."""

        ensure_venv(cwd)

    def _start(self, command: str, cwd: Path):
        env = command_environment(cwd, venv=self.venv_on_path)
        if self.bounded:
            shell_windows.grant_workspace(cwd)
            self._runs += 1
            output = cwd / TMP / f"run-{os.getpid()}-{self._runs}.out"
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
                f"the workspace's Python environment could not be made: {error}",
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
    """The command's side inside the deployed container: a plain process, no venv made.

    The container is the boundary, so nothing is restricted here, and nothing is
    installed for the model either: a venv in the workspace is its own choice,
    as it is for a developer, and the `ModalRunner` beside the worker tells it
    so. `where` is never read here — the worker's runner is the one the brief
    quotes.
    """

    venv_on_path = False

    def prepare(self, cwd: Path) -> None:
        ensure_tmp(cwd)


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

    lines = [f"exit code: {finished.exit_code}   ({finished.seconds:.1f} s)"]
    if finished.fresh:
        lines.append(
            "new environment: nothing installed by earlier commands is present; "
            "what is in the workspace is."
        )
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
