"""Run the same natural requests through the agent twice and compare what it did.

    python tools/prompt_scenarios.py --dry-run
    python tools/prompt_scenarios.py --label baseline --prompt-file <variant>.txt
    python tools/prompt_scenarios.py --label current --only castle

The live 4.3 acceptance was two chat messages, one per prompt version, and that
is not enough to attribute a behaviour change to a prompt. This runs a fixed
list of requests through the same `Agent` the bot uses — no Telegram, no queue,
one variable — and records what the agent *did*: which tools it called, in what
order, how many model calls it spent, and the whole answer.

Nothing here judges an answer. The machine-checkable part is the shape of the
turn; whether the answer is good is read by a person, which is why the report
carries the full text.

**Every run wakes the GPU.** It is a product-runtime worker and its own human
gate, every time. `--dry-run` composes everything and calls nothing, so the
scenarios and the assembled prompt can be checked for free.

The run is sealed off from real work: its own workspace, its own SQLite store,
its own telemetry file, and never the deployed database, whatever the
environment says.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.agent.runtime import Agent, MessageProduced, create_agent, text_message
from app.capabilities import system_message
from app.config import AgentSettings, ModelSettings
from app.context import DEFAULT_SYSTEM_PROMPT
from app.instructions import INSTRUCTIONS_FILE
from app.telemetry.base import TurnRun
from app.telemetry.cost import A10_USD_PER_SECOND, IDLE_WINDOW_SECONDS, gpu_cost
from app.telemetry.inspect import tool_calls
from app.telemetry.sqlite import SqliteTelemetry
from app.telemetry.trace import Telemetry
from app.tools import Tool, Toolbox
from app.tools.goal import TOOL_NAME as GOAL_TOOL
from app.tools.todo import TOOL_NAME as TODO_TOOL

RUNS = Path("reports/prompt_runs")

# The scenario user is not a real one. It gets its own workspace under the run
# directory, so nothing here can read or overwrite what a person keeps.
SCENARIO_USER = "prompt-scenarios"


@dataclass(frozen=True)
class Scenario:
    """One natural request, and what a person should look at in the answer.

    `expected_tools` is the only thing compared automatically, and it is a
    statement about the turn's shape rather than about its quality: these tools
    should appear somewhere. An empty tuple is a real expectation — a
    conversational question that spends a tool call has regressed in cost.
    """

    name: str
    request: str
    expected_tools: tuple[str, ...] = ()
    seed: tuple[tuple[str, str], ...] = ()
    look_for: str = ""
    external: bool = False
    # A turn taken in the same thread before the measured one. What the agent
    # did a turn ago is part of the situation it answers in, and a scenario
    # that always starts from nothing can only ever measure the empty case.
    # It is not measured itself: only the turn after it is.
    prelude_request: str = ""


BROKEN_PAGE = """<!doctype html>
<title>Прайс</title>
<style>body{font-family:sans-serif} .price{color:#fff;background:#fff}</style>
<h1>Тарифы</h1>
<p class="price">1990 рублей в месяц</p>
<script>document.querySelector('h1').textContnet = 'Тарифы 2026'</script>
"""

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="chat",
        request="Привет. В двух предложениях: чем отличается префиксный кеш от кеша ответов?",
        look_for="один ход модели, ноль инструментов. Это антирегрессия по стоимости.",
    ),
    Scenario(
        name="capabilities",
        request="Что ты умеешь? Коротко.",
        look_for=(
            "перечисляет только то, что действительно подключено, "
            "и не отрицает способность, которая у него есть"
        ),
    ),
    Scenario(
        name="note",
        request="Запиши в notes.txt три дела на завтра.",
        expected_tools=("write_file",),
        look_for="простая запись не должна порождать проверочный проход",
    ),
    Scenario(
        name="castle",
        request="Создай HTML с средневековым замком.",
        expected_tools=("write_file", "use_page"),
        look_for=(
            "сама регрессия: сделал ли он файл вместо кода в чате, "
            "осмотрел ли результат сам, спросил ли разрешения"
        ),
    ),
    # Three ways the same request stops being about an empty room. The live
    # turns that did write the file ran where a file already existed, so what
    # is being separated here is: the request naming one, the workspace holding
    # one, and the conversation having just made one.
    Scenario(
        name="castle_named",
        request="Создай HTML с средневековым замком в файле castle.html.",
        expected_tools=("write_file",),
        look_for="меняет ли что-то названный файл в самом запросе",
    ),
    Scenario(
        name="castle_seeded",
        request="Создай HTML с средневековым замком.",
        seed=(("index.html", "<!doctype html>\n<title>Заготовка</title>\n<h1>Тут пусто</h1>\n"),),
        expected_tools=("write_file",),
        look_for="меняет ли что-то файл, который уже лежит в рабочей папке",
    ),
    Scenario(
        name="castle_after",
        request="Создай HTML с средневековым замком.",
        prelude_request="Создай hello.html с приветствием.",
        expected_tools=("write_file",),
        look_for=(
            "воспроизведение живого случая: предыдущий ход уже создал файл, "
            "и только после этого просят замок"
        ),
    ),
    Scenario(
        name="broken_page",
        request="Посмотри price.html и скажи, что с ним не так.",
        seed=(("price.html", BROKEN_PAGE),),
        expected_tools=("use_page",),
        look_for=(
            "белый текст на белом фоне и опечатка в textContnet видны только "
            "тому, кто действительно открыл страницу"
        ),
    ),
    # Work with more than one step in it, which is the only situation a plan is
    # for. What the plan is used for is deliberately not an expectation: whether
    # a list helps is the model's decision, and hard-coding it here would be
    # measuring obedience instead of behaviour. The expectation is the outcome.
    Scenario(
        name="plan",
        request=(
            "Сделай страницу с прайс-листом на три услуги, посмотри на неё "
            "и почини, если что-то не так."
        ),
        expected_tools=("write_file", "use_page"),
        look_for=(
            "работа в несколько шагов: завёл ли он список дел, держал ли его "
            "в актуальном состоянии и закрыл ли пункты перед ответом — "
            "и не появился ли список там, где хватило бы одного шага"
        ),
    ),
    Scenario(
        name="standing_instructions",
        request="Привет. Что ты умеешь? Коротко.",
        seed=((INSTRUCTIONS_FILE, "Always answer in English, and start with the word OK.\n"),),
        look_for=(
            "дошёл ли оверлей до модели и послушалась ли она его: ответ должен "
            "быть по-английски и начинаться с OK, хотя спросили по-русски"
        ),
    ),
    # The request that failed twice live on 2026-08-30/31, word for word. It is
    # here rather than paraphrased because what is being measured is a parse
    # failure, and a shorter page might not reach the length that triggers it.
    Scenario(
        name="snake",
        request="Создай html с игрой змейка, Назови Снейк_Гейм, проверь что работает",
        expected_tools=("write_file", "use_page"),
        look_for=(
            "живой отказ: write_file приходит с чужими полями и без path. "
            "Смотреть на аргументы первого вызова после todo_write"
        ),
    ),
    Scenario(
        name="web",
        request="Когда вышла Gemma 3? Скажи, откуда взял.",
        expected_tools=("search_web",),
        look_for="называет страницу, а не пересказывает сниппет",
        external=True,
    ),
)


@dataclass
class Result:
    """What one scenario's turn actually did."""

    scenario: Scenario
    run_id: str
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_ms: int | None = None
    tools: list[dict[str, object]] = field(default_factory=list)
    answer: str = ""
    outcome: str = ""
    error: str = ""
    derived_usd: float = 0.0

    @property
    def names(self) -> list[str]:
        return [str(call["tool"]) for call in self.tools]

    @property
    def met(self) -> bool:
        """Whether the expected tools appeared. Not whether the answer is good."""

        seen = set(self.names)
        if not self.scenario.expected_tools:
            return not seen
        return set(self.scenario.expected_tools) <= seen


# --- the planning variants, which exist only to be compared -------------------
#
# The product's tool is `app/tools/todo.py` and is not changed by anything here.
# This is the same tool with the one thing under test replaced: an argument that
# nests — an array of objects — against an argument that does not. vLLM 51284
# says the Gemma 4 parser mishandles string values inside nested structures when
# the model writes them as plain quoted literals instead of the delimiter form,
# and that the rate climbs with the length of the values. A flat argument is the
# cheapest way to find out whether that is what this is.

FLAT_DESCRIPTION = (
    "Record and update your task list for the work in front of you. Send the "
    "ENTIRE checklist every call: it replaces the previous one, and there are "
    "no partial updates. One step per line, each line starting with [ ] for not "
    "started, [>] for the one you are working on now, or [x] for done. Mark a "
    "step done the moment it is done rather than in one batch at the end. Skip "
    "the list entirely for work that is a single step."
)

FLAT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "string",
            "description": (
                "The COMPLETE checklist, replacing any previous one. "
                "For example:\n[x] write the page\n[>] look at it"
            ),
        }
    },
    "required": ["plan"],
    "additionalProperties": False,
}


def _flat_write(plan: str) -> str:
    lines = [line.strip() for line in plan.splitlines() if line.strip()]
    done = sum(1 for line in lines if line.startswith("[x]"))
    active = sum(1 for line in lines if line.startswith("[>]"))
    return (
        f"Updated todo list: {len(lines) - done - active} pending, "
        f"{active} in progress, {done} completed."
    )


def flat_todo_tools() -> list[Tool]:
    """The same tool with a single string argument instead of a nested one."""

    return [
        Tool(
            name=TODO_TOOL,
            description=FLAT_DESCRIPTION,
            parameters=FLAT_PARAMETERS,
            run=_flat_write,
        )
    ]


def planning(agent: Agent, mode: str) -> None:
    """Replace the agent's planning tool for the length of one run.

    Wrapping the bound method rather than reaching into the graph: the toolbox
    is read once per thread when the graph is compiled, and the capability brief
    is generated from it, so a swap here changes both the schemas the model is
    offered and the sentence describing them — which is what a comparison of
    schemas has to change and nothing else.
    """

    if mode == "nested":
        return
    original = agent.toolbox

    def swapped(thread_id: str) -> Toolbox:
        # The private dictionary is the only way to get the built tools back
        # out. This is an instrument, not the product; the product never does
        # this.
        kept = [
            tool
            for tool in original(thread_id)._tools.values()  # noqa: SLF001
            if tool.name != TODO_TOOL
        ]
        if mode == "flat":
            kept += flat_todo_tools()
        return Toolbox(kept)

    agent.toolbox = swapped  # type: ignore[method-assign]


def goal(agent: Agent, mode: str) -> None:
    """Take the goal tool away for the length of one run (`--goal off`).

    The product offers it always; the comparison is the loop with the
    request's parts written down against the loop without, the way
    `--planning none` is for the plan.
    """

    if mode == "on":
        return
    original = agent.toolbox

    def without(thread_id: str) -> Toolbox:
        return Toolbox(
            [tool for tool in original(thread_id)._tools.values() if tool.name != GOAL_TOOL]  # noqa: SLF001
        )

    agent.toolbox = without  # type: ignore[method-assign]


def select(only: Sequence[str] = (), external: bool = False) -> list[Scenario]:
    """The scenarios a run will actually take.

    A scenario that spends a third-party service is left out unless it is asked
    for: waking the GPU is one gate the person gives, and sending a query to a
    search provider is a different one they may not have meant to give.
    """

    return [
        scenario
        for scenario in SCENARIOS
        if (not only or scenario.name in only) and (external or not scenario.external)
    ]


def revision() -> str:
    try:
        found = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return found.stdout.strip() or "unknown"


def identity(prompt: str) -> str:
    """A short stable name for one exact prompt, so two runs can be told apart."""

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def assembled(agent: Agent, prompt: str, thread_id: str) -> str:
    """The whole system message, the way the graph builds it.

    The capability brief is generated from the wired toolbox, so it is part of
    what a comparison is comparing even when only the core changed.
    """

    return system_message(agent.toolbox(thread_id), agent.delivery, prompt)


def sealed(root: Path, *, stream: bool = True) -> AgentSettings:
    """Settings that cannot reach anything real.

    `database_url` is cleared explicitly rather than left to the environment:
    the deployed database is one variable away, and a scenario run writing into
    it would put synthetic conversations beside a person's own.
    """

    return AgentSettings(
        database=str(root / "memory.sqlite3"),
        database_url="",
        alt_database_url="",
        checkpoints=str(root / "checkpoints.sqlite3"),
        workspace=str(root / "workspaces"),
        telemetry=True,
        telemetry_database=str(root / "telemetry.sqlite3"),
        stream_answers=stream,
    )


def plant(workspace: Path, scenario: Scenario) -> None:
    """Give the scenario the workspace it assumes, and nothing else.

    Emptied first: a run where `castle` can see the file `note` wrote is not
    the same run twice, and the point of this instrument is that it is.
    """

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in scenario.seed:
        (workspace / name).write_text(content, encoding="utf-8")


async def run_one(
    agent: Agent,
    telemetry: Telemetry,
    scenario: Scenario,
    *,
    idle_window: float,
    rate: float,
) -> Result:
    thread_id = f"{scenario.name}-{uuid.uuid4().hex[:8]}"
    plant(agent.workspace, scenario)
    run = TurnRun(
        run_id=uuid.uuid4().hex,
        user_id=SCENARIO_USER,
        thread_id=thread_id,
        source="scenario",
        source_update_id=scenario.name,
    )
    if scenario.prelude_request:
        # Untraced and unreported: it is the situation, not the measurement.
        async for _ in agent.events(thread_id, text_message(scenario.prelude_request)):
            pass
    trace = telemetry.start(run)
    result = Result(scenario=scenario, run_id=run.run_id)
    answers: list[str] = []
    try:
        async for event in agent.events(thread_id, text_message(scenario.request), trace):
            if not isinstance(event, MessageProduced):
                continue
            message = event.message
            if message.role != "assistant":
                continue
            spoken = " ".join(part.text or "" for part in message.content).strip()
            if spoken:
                answers.append(spoken)
    except Exception as error:  # noqa: BLE001 - one broken scenario is a row, not the end
        result.error = f"{type(error).__name__}: {error}"
        trace.finish("failed", error_type=type(error).__name__)
    else:
        pending = await agent.pending(thread_id)
        trace.finish("approval_requested" if pending else "answer_delivered")
    # The counters are the trace's own, filled in as the turn ran.
    result.model_calls = run.model_calls
    result.input_tokens = run.input_tokens
    result.output_tokens = run.output_tokens
    result.total_ms = run.total_ms
    result.outcome = run.outcome or "failed"
    # The last thing it said is the answer; anything before it was narration on
    # the way to a tool call, which the interface does not keep either.
    result.answer = answers[-1] if answers else ""
    store = telemetry.store
    if store is not None:
        events = store.events(run.run_id)
        result.tools = tool_calls(events)
        cost = gpu_cost(events, idle_window_seconds=idle_window, rate_per_second=rate)
        result.derived_usd = cost.derived_usd if cost else 0.0
    telemetry.release(run.run_id)
    return result


def render(header: dict[str, str], results: list[Result]) -> str:
    """The comparison document. One file per run, diffed against another run."""

    lines = [
        f"# Prompt scenarios — {header['label']}",
        "",
        *(f"**{key}:** {value}  " for key, value in header.items() if key != "label"),
        "",
        "Tool expectations are the only automatic check, and they are about the "
        "shape of the turn, not the quality of the answer. Read the answers.",
        "",
        "| scenario | shape | model | tools | tokens in/out | seconds | derived $ |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        seconds = "-" if result.total_ms is None else f"{result.total_ms / 1000:.1f}"
        names = ", ".join(result.names) or "none"
        lines.append(
            f"| {result.scenario.name} | {'ok' if result.met else 'off'} "
            f"| {result.model_calls} | {names} "
            f"| {result.input_tokens}/{result.output_tokens} | {seconds} "
            f"| {result.derived_usd:.4f} |"
        )
    total = sum(result.derived_usd for result in results)
    lines += ["", f"Derived GPU cost for the whole run, upper bound: ${total:.4f}.", ""]

    for result in results:
        scenario = result.scenario
        lines += [
            f"## {scenario.name}",
            "",
            f"**Request:** {scenario.request}",
            "",
            f"**Expected tools:** {', '.join(scenario.expected_tools) or 'none'}  ",
            f"**Called:** {', '.join(result.names) or 'none'}  ",
            f"**Outcome:** {result.outcome}",
            "",
        ]
        if scenario.look_for:
            lines += [f"**Look for:** {scenario.look_for}", ""]
        if result.tools:
            lines += ["```text"]
            for call in result.tools:
                path = call.get("path")
                where = f" {path}" if path else ""
                milliseconds = call.get("duration_ms")
                took = "" if milliseconds is None else f" {int(milliseconds)}ms"
                lines.append(f"{call['tool']}{where} {call['status']}{took}")
            lines += ["```", ""]
        if result.error:
            lines += [f"**Failed:** {result.error}", ""]
        lines += ["```text", result.answer or "(no text)", "```", ""]
    return "\n".join(lines)


async def measure(options: argparse.Namespace) -> int:
    prompt = DEFAULT_SYSTEM_PROMPT
    source = "app.context.window.DEFAULT_SYSTEM_PROMPT"
    if options.prompt_file:
        prompt = Path(options.prompt_file).read_text(encoding="utf-8").strip()
        source = options.prompt_file

    chosen = select(options.only, options.external)
    if not chosen:
        print("no scenarios selected")
        return 2

    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
    root = Path(options.out or RUNS / f"{stamp}_{options.label}")
    root.mkdir(parents=True, exist_ok=True)
    settings = sealed(root, stream=not options.no_stream)
    telemetry = Telemetry(SqliteTelemetry(settings.telemetry_database))
    model_settings = ModelSettings()
    agent = create_agent(
        model_settings=model_settings,
        agent_settings=settings,
        user_id=SCENARIO_USER,
        telemetry=telemetry,
        system_prompt=prompt,
    )
    planning(agent, options.planning)
    goal(agent, options.goal)
    whole = assembled(agent, prompt, "preview")
    header = {
        "label": options.label,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "revision": revision(),
        "prompt": f"`{identity(whole)}` from {source}, {len(whole)} characters",
        "model": f"{model_settings.name} at {model_settings.endpoint}",
        "sampling": f"temperature {model_settings.temperature}, "
        f"max_tokens {model_settings.max_tokens}",
        "planning": options.planning,
        "goal": options.goal,
        "streaming": "on" if settings.stream_answers else "off",
    }

    if options.dry_run:
        print("\n".join(f"{key}: {value}" for key, value in header.items()))
        print(f"\nscenarios: {', '.join(scenario.name for scenario in chosen)}")
        print(f"\n--- assembled system message ---\n{whole}")
        await agent.aclose()
        telemetry.close()
        return 0

    results = []
    try:
        for scenario in chosen:
            print(f"running {scenario.name} ...", flush=True)
            results.append(
                await run_one(
                    agent,
                    telemetry,
                    scenario,
                    idle_window=options.idle_window,
                    rate=options.gpu_rate,
                )
            )
    finally:
        await agent.aclose()
        telemetry.close()

    report = root / "report.md"
    report.write_text(render(header, results), encoding="utf-8")
    # The exact prompt that produced these answers, beside them. A comparison
    # against a prompt nobody can reconstruct is not a comparison.
    (root / "system_prompt.txt").write_text(whole, encoding="utf-8")
    print(f"\n{report}")
    for result in results:
        print(f"  {result.scenario.name}: {'ok' if result.met else 'off'} "
              f"{result.model_calls}m {len(result.tools)}t {', '.join(result.names) or 'no tools'}")
    return 0


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run", help="names the run directory")
    parser.add_argument("--prompt-file", default="", help="a prompt variant to measure")
    parser.add_argument("--only", nargs="*", default=[], help="run these scenarios only")
    parser.add_argument("--out", default="", help="where to write, instead of a dated directory")
    parser.add_argument(
        "--external",
        action="store_true",
        help="also run scenarios that spend a third-party service",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compose everything and call nothing, so no GPU is woken",
    )
    parser.add_argument(
        "--planning",
        choices=("nested", "flat", "none"),
        default="nested",
        help="which planning tool the agent is given, or none at all",
    )
    parser.add_argument(
        "--goal",
        choices=("on", "off"),
        default="on",
        help="whether the set_goal tool is offered, as the product offers it",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="ask for the whole completion at once instead of streaming it",
    )
    parser.add_argument("--idle-window", type=float, default=IDLE_WINDOW_SECONDS)
    parser.add_argument("--gpu-rate", type=float, default=A10_USD_PER_SECOND)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(measure(parse(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    raise SystemExit(main())
