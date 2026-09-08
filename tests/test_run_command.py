"""`run_command` and the two modes: roadmap 5b, `DECISIONS.md` 2026-09-04.

The local runner is exercised for real on this machine — a process in a
temporary workspace — because what it withholds and what it kills are the
contract. A fake runner covers the tool's projection through the executor.
No model, no network.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.agent.mode import CAREFUL_SWITCH, careful_enabled, current_mode, set_mode
from app.capabilities import capability_brief, capability_report, needs_approval
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ToolCall
from app.tools import (
    DEFAULT_CAPABILITIES,
    SHELL_RUN,
    CapabilityRegistry,
    Finished,
    LocalRunner,
    ToolError,
    ToolExecutor,
    Toolbox,
    shell_tools,
)
from app.tools.shell import (
    COMMAND_TIMEOUT,
    ContainerRunner,
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT,
    bounded,
    command_environment,
    describe,
)

PY = f'"{sys.executable}"'


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


def run(coro):
    return asyncio.run(coro)


# --- the local runner, for real ---------------------------------------------------


def test_a_command_runs_in_the_workspace_and_its_output_comes_back(workspace: Path) -> None:
    finished = run(LocalRunner().run(f"{PY} -c \"import os; print(os.getcwd())\"", workspace, 30))

    assert finished.exit_code == 0
    assert Path(finished.output.strip()).resolve() == workspace.resolve()
    assert not finished.cut and not finished.fresh


def test_a_non_zero_exit_is_a_result_not_a_failure(workspace: Path) -> None:
    finished = run(LocalRunner().run(f"{PY} -c \"import sys; print('bad'); sys.exit(3)\"", workspace, 30))

    assert finished.exit_code == 3
    assert "bad" in finished.output
    assert "exit code: 3" in describe(finished)


def test_the_agents_own_environment_is_withheld(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret-value")
    monkeypatch.setenv("MODEL_API_KEY", "secret-too")

    finished = run(
        LocalRunner().run(
            f"{PY} -c \"import os; print(os.environ.get('TELEGRAM_TOKEN'), "
            "os.environ.get('MODEL_API_KEY'), os.path.expanduser('~'))\"",
            workspace,
            30,
        )
    )

    assert finished.output.split()[:2] == ["None", "None"]
    # Home is the person's (roadmap 17), never the workspace.
    assert Path(finished.output.split()[-1]).resolve() == Path.home().resolve()


def test_the_environment_is_what_a_shell_needs_with_home_and_temp_as_told(
    workspace: Path, tmp_path: Path
) -> None:
    """Roadmap 17: home is the person's, temp is private and not the workspace."""

    home, tmp = tmp_path / "home", tmp_path / "tmp"
    env = command_environment(
        workspace,
        {"PATH": "/bin", "TELEGRAM_TOKEN": "x", "AGENT_DATABASE_URL": "y", "SYSTEMROOT": "C:\\W", "TEMP": "/elsewhere"},
        home=home,
        tmp=tmp,
    )

    assert env["PATH"].endswith("/bin") and env["SYSTEMROOT"] == "C:\\W"
    assert "TELEGRAM_TOKEN" not in env and "AGENT_DATABASE_URL" not in env
    assert env["HOME"] == str(home) == env["USERPROFILE"]
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == str(tmp)
    assert env["APPDATA"].startswith(str(tmp)) and env["LOCALAPPDATA"].startswith(str(tmp))
    assert env["PIP_CACHE_DIR"].startswith(str(tmp))
    assert not env["TEMP"].startswith(str(workspace))


def test_the_agents_own_venv_is_not_handed_to_the_command(workspace: Path, tmp_path: Path) -> None:
    """Hermes strips its venv's markers for the same reason: an agent launched
    from its activated `.venv` must not make that venv the model's `python`."""

    from app.tools.shell import own_venv_bin

    own = own_venv_bin()
    if own is None:
        pytest.skip("the test process runs outside a venv")
    path = os.pathsep.join([str(own), "/usr/bin"])

    env = command_environment(workspace, {"PATH": path}, home=tmp_path, tmp=tmp_path)

    assert env["PATH"] == "/usr/bin"
    assert "VIRTUAL_ENV" not in env


def test_a_command_past_its_timeout_is_killed_and_reported(workspace: Path) -> None:
    with pytest.raises(ToolError) as caught:
        run(LocalRunner().run(f"{PY} -c \"import time; print('started', flush=True); time.sleep(60)\"", workspace, 1))

    assert caught.value.code == COMMAND_TIMEOUT
    assert "1 seconds" in str(caught.value)


def test_a_cancelled_turn_kills_the_command(workspace: Path) -> None:
    marker = workspace / "still-running"

    async def scenario() -> None:
        task = asyncio.create_task(
            LocalRunner().run(
                f"{PY} -c \"import time, pathlib; time.sleep(3); pathlib.Path('still-running').write_text('x')\"",
                workspace,
                30,
            )
        )
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    # The process was killed before it could write; give a survivor time to prove us wrong.
    import time

    time.sleep(3.5)
    assert not marker.exists()


def test_long_output_is_cut_in_the_middle_keeping_both_ends() -> None:
    text = "".join(f"line {i}\n" for i in range(20_000))

    kept, cut = bounded(text)

    assert cut and len(kept) < MAX_OUTPUT_CHARS + 200
    assert kept.startswith("line 0\n") and kept.rstrip().endswith("line 19999")
    assert "characters left out" in kept


def test_a_command_that_cannot_start_is_a_typed_failure(workspace: Path, monkeypatch) -> None:
    import subprocess

    def boom(*args, **kwargs):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with pytest.raises(ToolError) as caught:
        run(LocalRunner().run("anything", workspace, 5))

    assert caught.value.code == "shell.not_started"


# --- the tool through the executor, with a fake runner --------------------------------


class Scripted:
    where = "in a test"

    def __init__(self, finished: Finished | Exception) -> None:
        self.finished = finished
        self.calls: list[tuple[str, Path, float]] = []

    async def run(self, command: str, cwd: Path, timeout: float) -> Finished:
        self.calls.append((command, cwd, timeout))
        if isinstance(self.finished, Exception):
            raise self.finished
        return self.finished


def executed(tools: Toolbox, name: str, **arguments):
    executor = ToolExecutor(tools)
    call = ToolCall(id="c1", name=name, arguments=arguments)
    prepared = executor.pre_execute(call)
    return run(executor.execute(prepared)), prepared


def test_the_model_reads_the_exit_code_and_output(workspace: Path) -> None:
    runner = Scripted(Finished(exit_code=0, output="hello\n", cut=False, seconds=0.2))
    tools = Toolbox(shell_tools(workspace, runner))

    outcome, _ = executed(tools, "run_command", command="echo hello")

    assert outcome.failure is None
    text = outcome.content[0].text
    assert "exit code: 0" in text and "hello" in text
    assert runner.calls[0][0] == "echo hello" and runner.calls[0][1] == workspace.resolve()
    assert runner.calls[0][2] == 120.0


def test_a_fresh_environment_is_not_said_and_the_timeout_is_clamped(workspace: Path) -> None:
    """Roadmap 17: what a container keeps is in the brief once; the line per
    fresh container read as the model's own work being gone (ISS-0053)."""

    runner = Scripted(Finished(exit_code=0, output="", cut=False, seconds=0.1, fresh=True))
    tools = Toolbox(shell_tools(workspace, runner))

    outcome, _ = executed(tools, "run_command", command="ls", timeout_seconds=10_000)

    assert "new environment" not in outcome.content[0].text
    assert "(no output)" in outcome.content[0].text
    assert runner.calls[0][2] == float(MAX_TIMEOUT)


def test_the_runners_failure_reaches_the_model_typed(workspace: Path) -> None:
    runner = Scripted(ToolError("the command did not finish within 3 seconds and was killed", code=COMMAND_TIMEOUT, detail="partial"))
    tools = Toolbox(shell_tools(workspace, runner))

    outcome, _ = executed(tools, "run_command", command="sleep 60", timeout_seconds=3)

    assert outcome.failure is not None
    assert outcome.failure.code == COMMAND_TIMEOUT
    assert outcome.failure.detail == "partial"


def test_run_command_is_declared_as_it_is(workspace: Path) -> None:
    (tool,) = shell_tools(workspace, Scripted(Finished(0, "", False, 0.0)))

    assert tool.mutates and not tool.replay_safe and not tool.requires_approval
    assert tool.timeout_seconds is not None and tool.timeout_seconds > MAX_TIMEOUT


# --- the registry and the brief ---------------------------------------------------


def test_the_default_grant_carries_the_shell_and_the_registry_owns_the_runner(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    tools = registry.toolbox(registry.grant())

    assert SHELL_RUN in DEFAULT_CAPABILITIES
    assert "run_command" in tools.names
    assert isinstance(registry.runner, LocalRunner)
    assert needs_approval(tools) == ()


def test_the_brief_says_where_commands_run_and_what_survives(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    tools = registry.toolbox(registry.grant())

    brief = capability_brief(tools, where_commands_run=registry.runner.where)

    assert "run_command runs a shell command on this machine" in brief
    # The line about reading a result is the tool's own since roadmap 16
    # (2026-09-07); the brief keeps only what differs per profile.
    described = tools.get("run_command").schema()["function"]["description"]
    assert "read the whole output" in described and "traceback names" in described
    assert "read the whole output" not in brief
    assert "virtual" in brief and "workspace" in brief
    without = registry.toolbox(registry.grant(capabilities=[name for name in DEFAULT_CAPABILITIES if name != SHELL_RUN]))
    assert "run_command" not in capability_brief(without)


# --- the two modes ---------------------------------------------------------------------


def test_careful_mode_makes_the_changing_tools_ask(workspace: Path) -> None:
    registry = CapabilityRegistry(workspace)
    full = registry.toolbox(registry.grant())
    careful = registry.toolbox(registry.grant(), ask_for_changes=True)

    assert needs_approval(full) == ()
    assert set(needs_approval(careful)) == {"write_file", "edit_file", "run_command"}
    assert not careful.requires_approval("read_file")
    assert not careful.requires_approval("send_file")
    assert "approve every change" in capability_brief(careful)
    assert "approve every change" not in capability_brief(full)
    assert "Ask first: write_file, edit_file, run_command" in capability_report(careful)


def test_the_mode_is_a_marker_in_the_workspace(workspace: Path) -> None:
    assert current_mode(workspace) == "full" and not careful_enabled(workspace)

    set_mode(workspace, "careful")
    assert (workspace / CAREFUL_SWITCH).is_file()
    assert current_mode(workspace) == "careful"

    set_mode(workspace, "full")
    assert not careful_enabled(workspace)
    with pytest.raises(ValueError):
        set_mode(workspace, "reckless")


def test_the_agent_reads_the_mode_when_it_builds_a_toolbox(workspace: Path) -> None:
    from app.agent.runtime import Agent
    from tests.fakes import ScriptedBackend

    with SqliteStore() as store:
        agent = Agent(ScriptedBackend(), store, workspace, user_id=LOCAL_USER_ID)
        assert not agent.toolbox("t").requires_approval("write_file")
        set_mode(agent.workspace, "careful")
        assert agent.toolbox("t").requires_approval("write_file")
        assert agent.toolbox("t").requires_approval("run_command")
        assert not agent.toolbox("t").requires_approval("list_files")


def test_python_is_the_machines_and_nothing_is_made_in_the_workspace(workspace: Path) -> None:
    """Roadmap 17: no venv made, nothing activated; the workspace holds only
    what a command puts there. The agent's own venv is not the answer either."""

    runner = LocalRunner()
    finished = run(runner.run("python -c \"import sys; print(sys.prefix)\"", workspace, 120))

    assert finished.exit_code == 0, finished.output
    prefix = Path(finished.output.strip()).resolve()
    assert workspace.resolve() not in prefix.parents
    assert prefix != Path(sys.prefix).resolve()
    assert not (workspace / ".venv").exists() and not (workspace / ".tmp").exists()
    assert runner.tmp.is_dir() and workspace.resolve() not in runner.tmp.resolve().parents
    assert runner.environment(workspace)["HOME"] == str(Path.home())


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the write boundary is Windows-only")


@windows_only
def test_a_command_can_write_inside_the_workspace_and_nowhere_else(workspace: Path, tmp_path: Path) -> None:
    """The human's rule, 2026-09-04: the operating system refuses a write outside."""

    (workspace / "existing").mkdir()  # made before the grant: inheritance must reach it
    outside = tmp_path / "outside.txt"
    script = (
        "import os, sys, pathlib\n"
        "pathlib.Path('inside.txt').write_text('ok')\n"
        "pathlib.Path('existing/deeper.txt').write_text('ok')\n"
        "pathlib.Path(os.environ['TEMP'], 'scratch.txt').write_text('ok')\n"
        "for label, target in [('outside', %r), ('base python', str(pathlib.Path(sys.base_prefix, 'agent-leak.txt'))), ('profile', %r)]:\n"
        "    try:\n"
        "        pathlib.Path(target).write_text('leak'); print(label, 'WRITTEN')\n"
        "    except PermissionError:\n"
        "        print(label, 'refused')\n"
    ) % (str(outside), str(Path.home() / "agent-leak.txt"))
    (workspace / "probe.py").write_text(script, encoding="utf-8")

    finished = run(LocalRunner().run("python probe.py", workspace, 120))

    assert finished.exit_code == 0, finished.output
    assert (workspace / "inside.txt").read_text() == "ok"
    assert (workspace / "existing" / "deeper.txt").read_text() == "ok"
    assert "outside refused" in finished.output
    assert "base python refused" in finished.output
    assert "profile refused" in finished.output
    assert "WRITTEN" not in finished.output
    assert not outside.exists()


@windows_only
def test_a_venv_the_model_makes_under_the_boundary_has_pip(workspace: Path) -> None:
    """Roadmap 17: no venv is made for the model, so the model makes one, and
    `python -m venv` runs `ensurepip` in isolated mode where the runner's
    `sitecustomize` is not on the path; the bundled wheel then lands in a
    0o700 temp directory and is refused. Every venv made here carries the
    accommodation in its own site-packages."""

    finished = run(
        LocalRunner().run(
            "python -m venv task\.venv && task\.venv\Scripts\python -m pip --version",
            workspace,
            180,
        )
    )

    assert finished.exit_code == 0, finished.output
    assert "pip" in finished.output
    assert (workspace / "task" / ".venv" / "Lib" / "site-packages" / "sitecustomize.py").is_file()
    assert not (workspace / ".venv").exists()


@windows_only
def test_what_the_command_starts_still_reaches_the_output(workspace: Path) -> None:
    """A grandchild's stdout was the thing that got lost; see shell_windows.py."""

    finished = run(LocalRunner().run("git --version & python -c \"print('grandchild ok')\"", workspace, 120))

    assert finished.exit_code == 0, finished.output
    assert "git version" in finished.output and "grandchild ok" in finished.output


@windows_only
def test_a_temporary_directory_can_be_made_and_used_under_the_boundary(workspace: Path) -> None:
    """CPython's owner-only 0o700 directories, accommodated by the `sitecustomize`
    every Python gets through `PYTHONPATH` (shell.py); temp is the runner's own."""

    finished = run(
        LocalRunner().run(
            "python -c \"import os, tempfile, pathlib; tempfile.TMP_MAX = 3; d = tempfile.mkdtemp(); "
            "pathlib.Path(d, 'f').write_text('x'); f = tempfile.NamedTemporaryFile(delete=False); "
            "f.write(b'y'); f.close(); print('temp ok', d.startswith(os.environ['TEMP']) "
            "and not d.startswith(str(pathlib.Path.cwd())))\"",
            workspace,
            60,
        )
    )

    assert finished.exit_code == 0, finished.output
    assert "temp ok True" in finished.output


@windows_only
def test_the_brief_says_the_boundary_holds_here() -> None:
    runner = LocalRunner()

    assert runner.bounded
    assert "only inside your workspace" in runner.where


def test_output_in_the_consoles_code_page_is_read_as_text() -> None:
    """`cmd` speaks the OEM code page; Python speaks UTF-8; both must read (P, 2026-09-04)."""

    from app.tools.shell import decoded

    assert decoded("привет".encode("utf-8")) == "привет"
    if sys.platform == "win32":
        assert decoded("не является".encode("oem")) == "не является"
    assert "\ufffd" in decoded(b"\xff\xfe\xfd\x80 mixed \xff") or sys.platform == "win32"


@windows_only
def test_what_cmd_says_in_russian_reaches_the_model_readable(workspace: Path) -> None:
    finished = run(LocalRunner().run("python3 --version", workspace, 60))

    assert finished.exit_code != 0
    assert "python3" in finished.output and "\ufffd" not in finished.output


# --- the deployed shape's share of the local code ---------------------------------------


def test_a_venv_in_the_workspace_is_used_only_when_a_command_names_it(
    workspace: Path, tmp_path: Path
) -> None:
    """Neither profile activates anything: a venv is the model's, by name."""

    (workspace / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")).mkdir(parents=True)

    for runner in (LocalRunner(), ContainerRunner()):
        env = command_environment(workspace, {"PATH": "/usr/bin"}, home=runner.home, tmp=runner.tmp)
        assert env["PATH"] == "/usr/bin"
        assert "VIRTUAL_ENV" not in env


def test_the_container_runners_home_and_temp_are_the_containers(workspace: Path) -> None:
    """OpenClaw's shape (roadmap 17): cwd is the workspace, home and /tmp are
    the container's own, so a socket path is short and a cache has its uid."""

    import tempfile

    runner = ContainerRunner()
    finished = run(runner.run(f"{PY} -c \"print('container ok')\"", workspace, 60))

    assert finished.exit_code == 0, finished.output
    assert "container ok" in finished.output
    assert not (workspace / ".tmp").exists() and not (workspace / ".venv").exists()
    env = runner.environment(workspace)
    assert env["TMPDIR"] == tempfile.gettempdir()
    assert env["HOME"] == str(Path.home())


def test_create_agent_hands_the_runner_to_the_registry(tmp_path: Path) -> None:
    from app.agent.runtime import create_agent
    from app.config import AgentSettings

    runner = Scripted(Finished(0, "", False, 0.0))
    settings = AgentSettings(workspace=str(tmp_path / "ws"), database=str(tmp_path / "m.sqlite3"))

    agent = create_agent(agent_settings=settings, runner=runner)
    try:
        assert agent.capability_registry.runner is runner
        assert isinstance(create_agent(agent_settings=settings).capability_registry.runner, LocalRunner)
    finally:
        agent.store.close()


def test_the_brief_carries_the_folder_per_task_rule(workspace: Path) -> None:
    """Roadmap 17, the human's line, literal: one folder per piece of work."""

    tools = Toolbox(shell_tools(workspace, LocalRunner()))
    brief = capability_brief(tools)

    assert "Each piece of work gets its own folder in your workspace" in brief
    assert "use that folder again" in brief



def test_a_non_zero_exit_carries_the_harness_own_line_and_a_zero_does_not() -> None:
    """The human's ask, 2026-09-04: at the moment of an error, say how to read it."""

    from app.tools.shell import UNWANTED_EXIT

    failed = describe(Finished(exit_code=1, output="Traceback ...", cut=False, seconds=0.3))
    fine = describe(Finished(exit_code=0, output="ok", cut=False, seconds=0.3))

    assert failed.endswith(UNWANTED_EXIT) and "Traceback ..." in failed
    assert UNWANTED_EXIT not in fine
    assert "pdf" not in UNWANTED_EXIT.lower() and "font" not in UNWANTED_EXIT.lower()
