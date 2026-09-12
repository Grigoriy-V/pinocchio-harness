"""The harness as an MCP server (roadmap 25).

    python -m tools.mcp_server            # stdio, what `.mcp.json` starts

What the project agent runs by hand from `tools/` and `scripts/` becomes
tools any MCP client can call: the last turns and one turn's trace, the
harness's own seconds, a thread's rows, the scenario families, the export
and the blind-judge pack, the work log, the doctor — and two that cost
money, `run_scenarios` and `run_turn`.

Each read-only tool runs the script it wraps as a subprocess, with the
environment this process was started in: what the operator types, typed by
the server, stdout captured. In-process would be simpler, but a script's
`print` would land on the protocol's own stdout; and several of them parse
`sys.argv` themselves.

The gate is in the protocol, twice. A tool that starts a worker or a model
call carries `anthropic/requiresUserInteraction`, which makes Claude Code
ask before every call in every permission mode but `dontAsk`; and it asks
again itself through elicitation, with the scope and the price in the
question, and runs only on an accepted yes. A client without elicitation
gets a refusal, not a run. Both, because annotations are advisory to a
client and the human's rule is that a worker starts on his word only.

Never returned: an environment value, a connection string, a token. The
database is reached only by the scripts, which open it through
`AgentSettings`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

# The flag Claude Code reads from a tool's `_meta`: ask before every call,
# whatever the permission mode (except `dontAsk`). Client-specific, not spec.
ASK_EVERY_TIME = {"anthropic/requiresUserInteraction": True}

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_FILES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
APPENDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
STARTS_WORKERS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

mcp = FastMCP(
    "pinocchio",
    instructions=(
        "The Pinocchio harness's operator tools. Read-only tools return what "
        "the operator's scripts print. `run_scenarios` and `run_turn` start "
        "priced work and ask the person before running; a declined question "
        "is a refusal, not an error to retry."
    ),
)


class Output(BaseModel):
    """What a wrapped script printed, and whether it succeeded."""

    ok: bool
    text: str = Field(description="the script's stdout and stderr, stderr last")
    command: str = Field(description="the command that ran, as the operator would type it")


LineSink = Callable[[str], Any] | None


async def run_script(
    argv: list[str], *, env: dict[str, str] | None = None, on_line: LineSink = None
) -> Output:
    """Run one of the repository's scripts and capture what it prints.

    `env` adds to this process's environment rather than replacing it, so a
    script sees the same `.env` the operator's shell would. `on_line` is
    called with each stdout line as it arrives (progress for a long run).
    """

    merged = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    # A blocking process in a thread, lines handed over through a queue:
    # asyncio's own subprocess support needs the proactor loop on Windows,
    # and the loop this runs on is whichever the host chose (the test suite
    # uses the selector loop).
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stderr_box: list[str] = []
    code_box: list[int] = []

    def pump() -> None:
        process = subprocess.Popen(
            [PYTHON, *argv],
            cwd=str(REPO),
            env=merged,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        errors = threading.Thread(target=lambda: stderr_box.append(process.stderr.read().decode("utf-8", errors="replace")))
        errors.start()
        for chunk in iter(process.stdout.readline, b""):
            loop.call_soon_threadsafe(queue.put_nowait, chunk.decode("utf-8", errors="replace").rstrip("\r\n"))
        code_box.append(process.wait())
        errors.join()
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()
    lines: list[str] = []
    while (line := await queue.get()) is not None:
        lines.append(line)
        if on_line is not None:
            result = on_line(line)
            if asyncio.iscoroutine(result):
                await result
    code = code_box[0] if code_box else 1
    stderr = (stderr_box[0] if stderr_box else "").strip()
    text = "\n".join(lines)
    if stderr:
        text = f"{text}\n--- stderr ---\n{stderr}" if text else stderr
    shown = " ".join(["python", *argv])
    return Output(ok=code == 0, text=text.strip(), command=shown)


# --- reading -----------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def runs(last: int = 20, user: str | None = None, failed: bool = False, summary: bool = False) -> Output:
    """The last measured turns from the telemetry the app would open (local
    SQLite, or the deployed database when `AGENT_DATABASE_URL` is set): run
    id, when, status, outcome, route, seconds, calls, tokens, user. `failed`
    keeps only turns that failed or never finished; `summary` adds GPU
    seconds and derived cost per successful turn over the window.
    Returns: the listing as `tools/show_run.py --last` prints it.
    Leaves: nothing."""

    argv = ["tools/show_run.py", "--last", str(last)]
    if user:
        argv += ["--user", user]
    if failed:
        argv.append("--failed")
    if summary:
        argv.append("--summary")
    return await run_script(argv)


@mcp.tool(annotations=READ_ONLY)
async def run(run_id: str) -> Output:
    """One measured turn by run id: summary, timings (first token, first
    visible, total), every model call with tokens, every tool call with its
    status and seconds, the steps, the timeline, the totals and the derived
    GPU cost. No conversation text.
    Returns: the rendering `tools/show_run.py <run_id>` prints.
    Leaves: nothing."""

    return await run_script(["tools/show_run.py", run_id])


@mcp.tool(annotations=READ_ONLY)
async def harness_seconds(run_ids: list[str] = [], last: int = 20, user: str | None = None) -> Output:
    """The harness's own seconds by name for the given run ids (or the last
    N turns): total, model, tools, and each named span of the harness
    (context assembly, persistence, delivery ...) with what remains unnamed.
    Returns: the table `tools/run_named_seconds.py` prints, one run a line.
    Leaves: nothing."""

    argv = ["tools/run_named_seconds.py", *run_ids, "--last", str(last)]
    if user:
        argv += ["--user", user]
    return await run_script(argv)


@mcp.tool(annotations=READ_ONLY)
async def thread(thread_id: str, head_chars: int = 400) -> Output:
    """One conversation's stored rows: role, tool calls, parts by kind and
    byte count, the head of each text. Media are shown as kind and size,
    never bytes.
    Returns: the rows as `tools/show_thread.py` prints them.
    Leaves: nothing."""

    return await run_script(["tools/show_thread.py", thread_id, str(head_chars)])


class ScenarioCase(BaseModel):
    letter: str
    index: int
    name: str
    thread: str
    sequence: int = Field(description="the run-id sequence this case writes")
    held_out: bool
    prompt: str
    interjection: str | None = Field(None, description="a message sent while the turn runs, where the case has one")


class Scenarios(BaseModel):
    mini: str = Field(description="the letters `loop_live` runs by default")
    wider: str
    families: dict[str, str] = Field(description="letter -> family name and what it stresses")
    cases: list[ScenarioCase]


@mcp.tool(annotations=READ_ONLY)
def scenarios() -> Scenarios:
    """The scenario suite the harness is measured with: the mini and wider
    letter sets of `scripts/loop_live.py`, and the seven training families
    D L N T U V X with every case — its prompt, thread, run-id sequence and
    whether it is held out of training. Contacts no model.
    Returns: the families and cases, structured.
    Leaves: nothing."""

    from scripts.loop_live import MINI, WIDER
    from scripts.training_scenarios import FAMILIES, HELD_OUT

    cases = [
        ScenarioCase(
            letter=family.letter,
            index=case.index,
            name=case.name,
            thread=family.thread(case),
            sequence=family.sequence(case),
            held_out=(family.letter, case.index) in HELD_OUT,
            prompt=case.prompt,
            interjection=case.interjection,
        )
        for family in FAMILIES.values()
        for case in family.cases
    ]
    return Scenarios(
        mini=MINI,
        wider=WIDER,
        families={f.letter: f"{f.name}; stresses {f.stresses}" for f in FAMILIES.values()},
        cases=cases,
    )


# --- data and judging ----------------------------------------------------------


@mcp.tool(annotations=WRITES_FILES)
async def export_trajectories(out: str, prefix: str = "", from_dir: str | None = None) -> Output:
    """Export trajectories with their outcome and checks to a folder:
    `runs/<run_id>.json` and `index.jsonl`. Reads the deployed Volume from
    this machine (no worker starts) or, with `from_dir`, local `.jsonl`
    files; joins each run with its telemetry. `prefix` keeps only run ids
    that start with it.
    Returns: how many runs were exported and where.
    Leaves: the export folder on this machine."""

    argv = ["tools/export_trajectories.py", "--out", out]
    if prefix:
        argv += ["--prefix", prefix]
    if from_dir:
        argv += ["--from-dir", from_dir]
    return await run_script(argv)


@mcp.tool(annotations=WRITES_FILES)
async def judge_pack(out: str, prefixes: list[str], export: str = "data/export", seed: int = 7) -> Output:
    """Pack exported runs into anonymised, shuffled transcripts for blind
    judges: `tNN.md` files, `INSTRUCTIONS.md` with the rubric, and `key.json`
    (for the unblinding, never for a judge). `prefixes` chooses the runs by
    run-id prefix; several prefixes put several models into one batch.
    Returns: how many transcripts were packed and where.
    Leaves: the batch folder on this machine."""

    argv = ["tools/judge_pack.py", "--export", export, "--out", out, "--seed", str(seed)]
    for prefix in prefixes:
        argv += ["--prefix", prefix]
    return await run_script(argv)


@mcp.tool(annotations=READ_ONLY)
async def judge_unblind(batch: str, export: str | None = None) -> Output:
    """Read the judges' `votes_*.json` in a batch folder against its
    `key.json`: mean score per model per judge and across judges, per
    criterion, the per-case table, disagreements over a point, the lowest
    transcripts. `export` fills in case letters the key lacks.
    Returns: the tables `tools/judge_unblind.py` prints.
    Leaves: nothing."""

    argv = ["tools/judge_unblind.py", batch]
    if export:
        argv += ["--export", export]
    return await run_script(argv)


@mcp.tool(annotations=APPENDS)
async def work_log_add(
    journal: str,
    agent: str = "",
    task: str = "",
    status: str = "completed",
    result: str = "",
    checks: str = "",
    summary: str = "",
    decision: str = "",
    links: list[str] = [],
) -> Output:
    """Append one record to a journal. `journal` is `agent` (one final record
    per material task: agent, task, status completed|failed|interrupted,
    result, checks) or `ml` (one record per measured outcome: summary,
    result, decision, links to reports). The record carries the git branch,
    head and changed files itself.
    Returns: the record id and what was written.
    Leaves: one line appended to `reports/agent_tasks.jsonl` or
    `reports/ml_work.jsonl`."""

    if journal == "agent":
        argv = [
            "tools/work_log.py", "agent", "add", "--agent", agent, "--task", task,
            "--status", status, "--result", result, "--checks", checks,
        ]
    elif journal == "ml":
        argv = ["tools/work_log.py", "ml", "add", "--summary", summary, "--result", result, "--decision", decision]
        for link in links:
            argv += ["--link", link]
    else:
        return Output(ok=False, text="journal must be `agent` or `ml`", command="")
    return await run_script(argv)


@mcp.tool(annotations=READ_ONLY)
async def work_log_search(journal: str, text: str = "", file: str | None = None) -> Output:
    """Search a journal (`agent` or `ml`) by text; for `agent`, `file` finds
    records whose commit changed that repository-relative file.
    Returns: the matching records as `tools/work_log.py search` prints them.
    Leaves: nothing."""

    if journal not in ("agent", "ml"):
        return Output(ok=False, text="journal must be `agent` or `ml`", command="")
    argv = ["tools/work_log.py", journal, "search", text]
    if file and journal == "agent":
        argv += ["--file", file]
    return await run_script(argv)


@mcp.tool(annotations=READ_ONLY)
async def doctor(endpoint: str | None = None, timeout: float = 2.0) -> Output:
    """The environment report: Python version, torch and CUDA, whether the
    model endpoint answers, whether credentials are configured (names only).
    Reports; installs and starts nothing.
    Returns: the checks as JSON, one object per check.
    Leaves: nothing."""

    argv = ["scripts/doctor.py", "--json", "--timeout", str(timeout)]
    if endpoint:
        argv += ["--endpoint", endpoint]
    return await run_script(argv)


# --- the gate ----------------------------------------------------------------------


class Yes(BaseModel):
    """The one question a priced tool asks."""

    ok: bool = Field(default=False, description="true to run exactly this")


async def permitted(ctx: Context, question: str) -> str | None:
    """Ask the person through the client; the reason it may not run, or None.

    A client without elicitation raises or answers oddly: every path but an
    accepted `ok = true` is a no. The question is asked before anything
    starts, so a re-run of the handler cannot repeat an effect.
    """

    try:
        answer = await ctx.elicit(message=question, schema=Yes)
    except Exception as error:  # noqa: BLE001 - the client's failure is a no
        return f"not run: the client could not ask the person ({type(error).__name__})"
    if answer.action != "accept":
        return f"not run: the person answered {answer.action}"
    if answer.data is None or not answer.data.ok:
        return "not run: the person did not say yes"
    return None


async def modal_profile() -> str:
    """The Modal workspace a deployed run would use; only its name."""

    try:
        done = await run_script(["-m", "modal", "profile", "current"])
    except OSError:
        return "unknown"
    return done.text.splitlines()[0].strip() if done.ok and done.text else "unknown"


@mcp.tool(annotations=STARTS_WORKERS, meta=ASK_EVERY_TIME)
async def run_scenarios(
    ctx: Context,
    letters: str = "",
    model: str = "",
    temperature: str = "",
    deployed: bool = False,
    repeat: int = 1,
    parallel: int = 1,
) -> Output:
    """Run scenario letters through a real model with `scripts/loop_live.py`:
    the mini set when `letters` is empty, else e.g. "DVX". `model` names a
    model set of `config.toml` (empty is the `.env` default); `deployed`
    runs them in the deployed worker through its `scenarios` Function on the
    active Modal workspace; `repeat` runs the set N times with fresh ids;
    `parallel` (deployed only) spawns N calls at once. Priced: model tokens
    every time, container minutes when deployed, a GPU when the set is a
    GPU App. Asks the person before starting and runs only on a yes.
    Returns: the PASS/FAIL report with run ids, or the reason it did not run.
    Leaves: locally, a temporary telemetry file named in the report;
    deployed, the probe user's turns in the deployed database."""

    chosen = "".join(sorted(set(letters.upper()))) or "the mini set"
    where = f"deployed on Modal workspace {await modal_profile()}" if deployed else "locally on this machine"
    question = (
        f"Run scenarios {chosen} on model set {model or '(the .env default)'}"
        f"{f' at temperature {temperature}' if temperature else ''}, {where}"
        f"{f', {repeat} times' if repeat > 1 else ''}{f', {parallel} calls at once' if parallel > 1 else ''}. "
        "Price: model tokens for every case"
        f"{'; the control container while it runs' if deployed else ''}"
        "; a GPU App wakes if the set is one. Run exactly this?"
    )
    if (reason := await permitted(ctx, question)) is not None:
        return Output(ok=False, text=reason, command="")
    argv = ["scripts/loop_live.py", *sorted(set(letters.upper()))]
    if model:
        argv += ["--model", model]
    if temperature:
        argv += ["--temperature", temperature]
    if deployed:
        argv.append("--deployed")
    if repeat > 1:
        argv += ["--repeat", str(repeat)]
    if parallel > 1 and deployed:
        argv += ["--parallel", str(parallel)]
    count = 0

    async def progress(line: str) -> None:
        nonlocal count
        if line.startswith(("  PASS", "  FAIL")) or line.startswith("all scenarios") or line.endswith("check(s) failed"):
            count += 1
            await ctx.report_progress(count, None, line.strip())

    return await run_script(argv, on_line=progress)


class TurnResult(BaseModel):
    ok: bool
    answer: str = Field(description="what the assistant said last")
    run_id: str = ""
    tools: list[str] = Field(default_factory=list, description="the tools called, in order")
    seconds: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    telemetry: str = Field("", description="the telemetry file the trace was written to")
    reason: str = Field("", description="why it did not run, when it did not")


def one_turn(text: str, model: str, thread: str, user: str, ceiling: int) -> TurnResult:
    """One turn of the assistant in a sealed room, driven the way a worker
    drives one; runs in its own thread with its own event loop, because the
    agent wants the selector loop on Windows and this process's loop is the
    protocol's."""

    from app.agent.interjections import MemoryInterjections
    from app.agent.runtime import create_agent, text_message
    from app.agent.stop import MemoryStopRequests
    from app.telemetry import TurnRun
    from app.telemetry.open import open_telemetry
    from scripts.loop_live import settings_in

    room = Path(tempfile.mkdtemp(prefix="mcp-turn-"))
    settings = settings_in(room)
    telemetry = open_telemetry(settings)
    # The model set is chosen from the environment the settings read
    # (`MODEL`), set for this turn only and put back after.
    previous = os.environ.get("MODEL")
    if model:
        os.environ["MODEL"] = model
    agent = create_agent(
        agent_settings=settings,
        user_id=user,
        telemetry=telemetry,
        stops=MemoryStopRequests(),
        interjections=MemoryInterjections(),
    )
    if model:
        if previous is None:
            os.environ.pop("MODEL", None)
        else:
            os.environ["MODEL"] = previous
    sequence = int(time.time()) % 1_000_000
    run_id = f"mcp-{sequence}"
    said: list[str] = []
    tools: list[str] = []

    async def drive() -> None:
        run = TurnRun(run_id=run_id, source="mcp", user_id=user)
        run.thread_id = thread
        trace = telemetry.start(run)
        trace.route("mcp")
        outcome = "answer_delivered"
        try:

            def observe(event: Any) -> None:
                message = getattr(event, "message", None)
                if message is None:
                    return
                for call in message.tool_calls:
                    tools.append(call.name)
                    if len(tools) > ceiling:
                        raise RuntimeError(f"the turn passed {ceiling} tool calls")
                if message.role == "assistant":
                    text_ = " ".join(part.text or "" for part in message.content).strip()
                    if text_:
                        said.append(text_)

            async for event in agent.events(thread, text_message(text), trace, sequence):
                observe(event)
            # A caller of this tool is not the person: an approval inside
            # the turn is answered no, and the model is told so.
            while (pending := await agent.pending(thread)) is not None:
                answers = {call["id"]: False for call in pending}
                async for event in agent.resume_events(thread, answers, trace):
                    observe(event)
        except Exception:
            outcome = "failed"
            raise
        finally:
            trace.finish(outcome, error_type="failed" if outcome == "failed" else None)
            telemetry.release(run_id)
            await agent.aclose()

    started = time.monotonic()
    reason = ""
    try:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        asyncio.run(drive(), loop_factory=loop_factory)
    except Exception as error:  # noqa: BLE001 - reported, not raised across the protocol
        reason = f"{type(error).__name__}: {error}"
    seconds = time.monotonic() - started
    store = telemetry.store
    run = store.get_turn(run_id) if store is not None else None
    telemetry.close()
    return TurnResult(
        ok=not reason,
        answer=said[-1] if said else "",
        run_id=run_id,
        tools=tools,
        seconds=round(seconds, 2),
        model_calls=run.model_calls if run else 0,
        tool_calls=run.tool_calls if run else 0,
        telemetry=settings.telemetry_database,
        reason=reason,
    )


@mcp.tool(annotations=STARTS_WORKERS, meta=ASK_EVERY_TIME)
async def run_turn(ctx: Context, text: str, model: str = "", thread: str = "chat-mcp", max_tool_calls: int = 40) -> TurnResult:
    """The assistant as a tool: one turn on `text` in a sealed room (a
    temporary workspace and databases of its own, a probe user), on the
    model set `model` (empty is the `.env` default, the product's hosted
    model). An approval the turn asks for is answered no: the caller is not
    the person. Stops after `max_tool_calls`. Priced: the model's tokens; a
    GPU App wakes if the set is one. Asks the person before starting.
    Returns: the answer, the run id, the tools called, seconds and call
    counts, and the telemetry file the trace went to (read it with `run`
    after pointing AGENT_TELEMETRY_DATABASE at it).
    Leaves: the sealed room under the system temp folder."""

    question = (
        f"Run one turn of the assistant on model set {model or '(the .env default)'}, "
        f"text: {text[:200]!r}. Price: the model's tokens; a GPU App wakes if the set is one. Run?"
    )
    if (reason := await permitted(ctx, question)) is not None:
        return TurnResult(ok=False, answer="", reason=reason)
    user = f"mcp-{threading.get_ident()}"
    return await asyncio.to_thread(one_turn, text, model, thread, user, max_tool_calls)


if __name__ == "__main__":
    mcp.run()
