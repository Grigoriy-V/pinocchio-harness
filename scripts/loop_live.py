"""A live check of the one loop. Needs the model endpoint, so it wakes a GPU.

    .venv\\Scripts\\python.exe -m scripts.loop_live                 all of A-S, here
    .venv\\Scripts\\python.exe -m scripts.loop_live --after-deploy  A, B and G
    .venv\\Scripts\\python.exe -m scripts.loop_live G               one by letter
    .venv\\Scripts\\python.exe -m scripts.loop_live --deployed R S  the same, in the
                                                                deployed worker

`--deployed` (2026-09-04, the human: the agent we work with is the deployed
one, so that is the one to test) runs the chosen scenarios inside the
deployed worker's own environment — its image, its secrets, its Volume, its
command runner — through the `scenarios` Function of
`deploy/modal/control_app.py`, in the workspace of a probe user of their
own, and prints the same report. It is a product-runtime worker and every
turn wakes the GPU: permission at the time, as here.

Five things the offline suite can only fake, because each of them is about what
a real model does with the loop rather than about what the loop does with a
scripted answer:

    A  an ordinary question           one model call, no tools, no mode
    B  a request that needs one tool  the tool runs and the answer uses it
    C  a multi-step workspace task    several steps, still no mode
    D  a stop while the work is live  the turn ends without another request
    E  a tool that fails              the typed result reaches the model, the
                                      model recovers, telemetry says why
    F  a page the model made          it looks at it with inspect_page, and the
                                      structure with refs is what it read
    G  the person's own request       an app, a look, and the files and the
                                      screenshot handed over unprompted, with
                                      no plan tool in the toolbox
    H  a detail behind the summary    the exact error text is found in stored
                                      history, not guessed from the summary
    I  a result already shortened     read back by position rather than the
                                      tool run again
    J  a worker killed mid-turn       a fresh agent takes the turn up from the
                                      checkpoint; nothing is redone unasked
    K  a fold in the middle of a turn the conversation is folded between two
                                      steps and the turn still finishes
    O  a script written and run       write_file, run_command, the output in
                                      the answer
    P  a PDF made and handed over     an install into the workspace, a run, a
                                      look at the document, send_file
    Q  a command past its timeout     shell.timeout reaches the model and the
                                      turn goes on
    R  data turned into a picture     a CSV summed with a command, a chart
                                      made from it, handed over
    S  a failing script repaired      the check run, the traceback read, one
                                      file fixed, the check run again green

Each scenario is checked, not only printed: a line starting with PASS or FAIL
says whether what happened is what the scenario expects, and the exit code is
the number of failures. The expectations are about the loop and the tool
boundary, never about the model's wording.

It writes into a temporary directory and its own telemetry file: nothing here
touches the deployed database, the real workspace, or the local profile's own
conversations. The run ids it prints can be read back with

    AGENT_TELEMETRY_DATABASE=<the file it names> python tools/show_run.py <id>

A to D are the acceptance evidence for roadmap sub-step 4.1; E is the live
half of 4.5 (`docs/v2_tool_system.md`, "Acceptance for 4.5"); F is the live
half of 4.5.5 and needs a browser where this runs; G is the request the
person tested live all day on 2026-09-03, in the plan-off shape that is the
default, with the numbers a plan-on run can be compared against. H and I are
the live half of 4.6b: the way back to what a summary or a stub stands for.
J and K are the live half of 4.7: restart, resume, and a turn across a
compaction, asserted on harness events. O, P and Q are the live half of 5b:
a command run on this machine, in the workspace, through the one tool. R and
S (2026-09-04, the human's ask: one PDF scenario is not enough to judge code
execution) are the two other shapes work with commands takes — data into a
picture, and a repair driven by a traceback — asserted on the files and the
last exit code, never on how the model got there. Every scenario line also carries the
derived GPU-active seconds and cost of its run, the item 3 estimate
(`app/telemetry/cost.py`), so a run can be read beside the 2026-08-29
baseline printed at the end. Every run of it costs GPU time and needs
permission at the time.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

from app.agent.runtime import create_agent, text_message
from app.agent.stop import MemoryStopRequests
from app.config import AgentSettings
from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.telemetry import TurnRun
from app.telemetry.cost import gpu_cost
from app.telemetry.open import open_telemetry

USER = "loop-live-check"


def settings_in(room: Path) -> AgentSettings:
    """The local profile, pointed entirely at a temporary directory.

    `database_url` is emptied deliberately: the developer's `.env` names the
    deployed database, and a check has no business writing conversations or
    telemetry into it.
    """

    (room / "workspace").mkdir(parents=True, exist_ok=True)
    return AgentSettings(
        database=str(room / "memory.sqlite3"),
        database_url="",
        checkpoints=str(room / "checkpoints.sqlite3"),
        telemetry=True,
        telemetry_database=str(room / "telemetry.sqlite3"),
        workspace=str(room / "workspace"),
    )


class Turn:
    """One measured turn driven directly, the way the worker drives one.

    Workspace writes are autonomous, so none of these scenarios should stop at
    a consent question. If one ever does — a tool gains `requires_approval` —
    this says yes, which is what the person running the check is doing by
    running it, and counts it so the report shows it happened.
    """

    def __init__(self, agent, telemetry, sequence: int) -> None:
        self.agent = agent
        self.telemetry = telemetry
        self.sequence = sequence
        self.text: list[str] = []
        self.tools: list[str] = []
        self.failures: list[tuple[str, ToolFailure]] = []
        self.tool_results: list[Message] = []
        self.approvals = 0
        self.run_id = f"{RUN_PREFIX}{sequence}"
        self._names: dict[str, str] = {}

    async def ask(self, thread_id: str, prompt: str) -> "Turn":
        run = TurnRun(run_id=self.run_id, source="loop-live", user_id=USER)
        run.thread_id = thread_id
        trace = self.telemetry.start(run)
        trace.route("loop")
        started = time.monotonic()
        outcome = "answer_delivered"
        try:
            async for event in self.agent.events(
                thread_id, text_message(prompt), trace, self.sequence
            ):
                self.observe(event)
            while (pending := await self.agent.pending(thread_id)) is not None:
                self.approvals += 1
                answers = {call["id"]: True for call in pending}
                async for event in self.agent.resume_events(thread_id, answers, trace):
                    self.observe(event)
        except asyncio.CancelledError:
            # J kills a turn this way. The record says so rather than
            # claiming an answer that never came.
            outcome = "failed"
            raise
        finally:
            trace.finish(outcome, error_type="killed" if outcome == "failed" else None)
            self.telemetry.release(self.run_id)
        self.seconds = time.monotonic() - started
        self.run = run
        return self

    async def take_up(self, thread_id: str) -> "Turn":
        """Continue the turn a killed worker left in `thread_id`, as a new run."""

        run = TurnRun(run_id=self.run_id, source="loop-live", user_id=USER)
        run.thread_id = thread_id
        trace = self.telemetry.start(run)
        trace.route("loop")
        started = time.monotonic()
        try:
            async for event in self.agent.resume_interrupted_events(thread_id, trace):
                self.observe(event)
        finally:
            trace.finish("answer_delivered")
            self.telemetry.release(self.run_id)
        self.seconds = time.monotonic() - started
        self.run = run
        return self

    def observe(self, event) -> None:
        message: Message | None = getattr(event, "message", None)
        if message is None:
            return
        for call in message.tool_calls:
            self.tools.append(call.name)
            self._names[call.id] = call.name
        if message.role == "tool":
            self.tool_results.append(message)
            if message.failure is not None:
                self.failures.append(
                    (self._names.get(message.tool_call_id or "", "?"), message.failure)
                )
        said = " ".join(part.text or "" for part in message.content).strip()
        if said and message.role == "assistant":
            self.text.append(said)

    def read_from(self, tool: str) -> str:
        """The text the model was given back by every call of this tool."""

        calls = {call_id for call_id, called in self._names.items() if called == tool}
        return " ".join(
            part.text or ""
            for message in self.tool_results
            if message.tool_call_id in calls
            for part in message.content
        )

    @property
    def answer(self) -> str:
        return self.text[-1] if self.text else ""

    def health_checked(self) -> bool:
        """Whether the turn ran long enough to be asked how it was doing."""

        store = self.telemetry.store
        if store is None:
            return False
        return any(event.type == "turn_health_check" for event in store.events(self.run_id))

    def events(self, kind: str) -> list[dict]:
        """The events of one type the store kept for this turn, by their data."""

        store = self.telemetry.store
        if store is None:
            return []
        return [event.data for event in store.events(self.run_id) if event.type == kind]

    def event_types(self) -> list[str]:
        store = self.telemetry.store
        if store is None:
            return []
        return [event.type for event in store.events(self.run_id)]

    def gpu(self) -> str:
        """Derived GPU-active seconds and cost, the item 3 estimate."""

        store = self.telemetry.store
        if store is None:
            return "-"
        cost = gpu_cost(store.events(self.run_id))
        if cost is None:
            return "no model call"
        return f"~{cost.estimated_active_ms / 1000:5.1f} s   ${cost.derived_usd:.4f}"

    def failed_events(self) -> list[dict]:
        """The `tool_failed` events the store kept for this turn, by their data."""

        store = self.telemetry.store
        if store is None:
            return []
        return [event.data for event in store.events(self.run_id) if event.type == "tool_failed"]

    def report(self, name: str, checks: dict[str, bool]) -> int:
        print(f"\n{name}")
        print(
            f"  model calls {self.run.model_calls}   tool calls {self.run.tool_calls}"
            f"   approvals {self.approvals}"
        )
        print(f"  tools       {self.tools or '-'}")
        if self.failures:
            print("  failures    " + "; ".join(f"{tool}: {why.code}" for tool, why in self.failures))
        print(f"  seconds     {self.seconds:6.2f}   run {self.run_id}")
        print(f"  gpu derived {self.gpu()}")
        print(f"  answer      {(self.answer or '(nothing said)')[:160]}")
        for expectation, held in checks.items():
            print(f"  {'PASS' if held else 'FAIL'}  {expectation}")
        return sum(1 for held in checks.values() if not held)


# What runs after every deploy of the agent: two quick answers, so a change
# to the loop has not broken the simple case, then the person's own request,
# which is where every defect of 2026-09-03 showed. Asked for by the human
# that day. Everything else stays available by letter.
AFTER_DEPLOY = ("A", "B", "G")


def chosen(argv: list[str]) -> frozenset[str]:
    """Which scenarios to run: `--after-deploy`, letters, or all of them."""

    if "--after-deploy" in argv:
        return frozenset(AFTER_DEPLOY)
    letters = {arg.upper() for arg in argv if len(arg) == 1 and arg.upper() in "ABCDEFGHIJKOPQRS"}
    return frozenset(letters) if letters else frozenset("ABCDEFGHIJKOPQRS")


# The run ids one invocation writes: `live-<sequence>` on this machine, in a
# telemetry file of the run's own; deployed, a prefix of the invocation's own
# so two runs into one database never share an id.
RUN_PREFIX = "live-"


async def start_clean(agent, selected, root: Path) -> None:
    """Every scenario begins in an empty conversation and an empty workspace.

    Locally that is what the sealed room gives for free. Deployed, the probe
    user's threads and workspace live in the real database and on the Volume
    and outlive the run: on 2026-09-05 the third G sample answered from the
    history of the two before it, in one model call and no tool, and measured
    nothing (run `deployed-808b8717-70`). What a scenario measures is one
    request from nothing, in both profiles.
    """

    from app.conversations import delete_conversation

    for letter in sorted(selected):
        await delete_conversation(agent.store, f"chat-{letter.lower()}", agent.checkpoints)
    for entry in sorted(Path(root).iterdir(), key=lambda path: path.is_dir()):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


async def run_scenarios(selected, agent, telemetry, agent_factory, prefix: str = "live-") -> int:
    """Run the chosen scenarios against one agent and print the report.

    `agent_factory` makes a fresh agent the way this one was made — J needs
    one after it kills the first. Closes the agent and the telemetry at the
    end. Returns the number of failed checks.
    """

    global RUN_PREFIX
    RUN_PREFIX = prefix

    def wanted(letter: str) -> bool:
        return letter in selected

    root = agent.capability_grant.root
    print(f"workspace {root}")
    await start_clean(agent, selected, root)
    failed = 0

    try:
        if wanted("A"):
            # A — one request, no tools, no mode.
            a = await Turn(agent, telemetry, 10).ask("chat-a", "Привет! Как ты?")
            failed += a.report(
                "A ordinary question",
                {
                    "one model call": a.run.model_calls == 1,
                    "no tools": not a.tools,
                    "an answer was given": bool(a.answer),
                },
            )

        if wanted("B"):
            # B — one tool, chosen by the model rather than by a route.
            (root / "notes.txt").write_text("The passphrase is marmalade.", encoding="utf-8")
            b = await Turn(agent, telemetry, 20).ask(
                "chat-b", "Read notes.txt in my workspace and tell me the passphrase."
            )
            failed += b.report(
                "B one tool",
                {
                    "read_file ran": "read_file" in b.tools,
                    "no tool failed": not b.failures,
                    "the passphrase is in the answer": "marmalade" in b.answer.lower(),
                },
            )

        if wanted("C"):
            # C — several steps, with no lifecycle to enter.
            c = await Turn(agent, telemetry, 30).ask(
                "chat-c",
                "In my workspace, write a file plan.txt containing three short lines "
                "about how to store apples, then read it back and tell me what it says.",
            )
            failed += c.report(
                "C multi-step work",
                {
                    "write_file then read_file": "write_file" in c.tools and "read_file" in c.tools,
                    "plan.txt exists": (root / "plan.txt").is_file(),
                    "no tool failed": not c.failures,
                    "an answer was given": bool(c.answer),
                },
            )

        if wanted("D"):
            # D — a stop recorded while the turn is running.
            stopped = Turn(agent, telemetry, 40)
            work = asyncio.create_task(
                stopped.ask(
                    "chat-d",
                    "In my workspace, create five files apple1.txt to apple5.txt, each "
                    "with one sentence about apples, reading each one back after you "
                    "write it.",
                )
            )
            # Late enough to be mid-work rather than mid-first-request: the stop is
            # only meaningful once the turn has actually started spending.
            while len(stopped.tools) < 2 and not work.done():
                await asyncio.sleep(0.05)
            await agent.stops.request(USER, 41)
            print("\n  (stop recorded while the turn was running)")
            await work
            failed += stopped.report(
                "D stopped mid-flight",
                {
                    "the turn ended with the stop message": stopped.answer == "Stopped at your request.",
                    "fewer than five files were made": len(list(root.glob("apple*.txt"))) < 5,
                },
            )

        if wanted("E"):
            # E — a tool that fails, typed. The model must read the result, not
            # lose the turn, and telemetry must say why. The failure is one the
            # model cannot see coming from a listing: the text it is asked to edit
            # occurs twice, so the first `edit_file` is refused as ambiguous.
            (root / "fruit.txt").write_text("apple pie\napple tart\n", encoding="utf-8")
            e = await Turn(agent, telemetry, 50).ask(
                "chat-e",
                "In my workspace, use edit_file on fruit.txt to replace the word "
                "'apple' with 'pear'. Do not read the file first and do not rewrite it "
                "with write_file. Then tell me what happened.",
            )
            codes = [why.code for _, why in e.failures]
            events = e.failed_events()
            failed += e.report(
                "E a failing tool",
                {
                    "edit_file ran": "edit_file" in e.tools,
                    "the failure reached the loop as fs.ambiguous_edit": "fs.ambiguous_edit" in codes,
                    "the model answered after the failure": bool(e.answer),
                    "the turn was not ended by the repeat guard": e.run.model_calls <= 4,
                    "tool_failed carries code and message": any(
                        event.get("code") == "fs.ambiguous_edit" and event.get("message")
                        for event in events
                    ),
                },
            )

        if wanted("F"):
            # F — the model looks at what it made. The check is about the loop:
            # the browser tool ran, returned no failure, and what the model read
            # was the structure with refs rather than a count of buttons.
            f = await Turn(agent, telemetry, 60).ask(
                "chat-f",
                "In my workspace, write a small self-contained page counter.html with a "
                "heading, a button labelled Count and a script that increments a number "
                "in the heading when the button is pressed. Then open it with inspect_page "
                "and tell me what the page contains.",
            )
            failed += f.report(
                "F a page the model made, looked at",
                {
                    "write_file then inspect_page": "write_file" in f.tools
                    and "inspect_page" in f.tools,
                    "counter.html exists": (root / "counter.html").is_file(),
                    "no tool failed": not f.failures,
                    "the model read a structure with a ref": "[ref=e" in f.read_from("inspect_page"),
                    "an answer was given": bool(f.answer),
                },
            )

        if wanted("G"):
            # G — the person's own request, plan off (the default). What the checks
            # ask is what the person asked for: it was built, looked at, and both
            # the files and the screenshot came without a second request.
            g = await Turn(agent, telemetry, 70).ask(
                "chat-g",
                "Создай небольшое веб-приложение Task Board. В отдельной папке Task Board\n\n"
                "отдельные index.html, styles.css и app.js;\n"
                "три колонки: To Do, In Progress, Done;\n"
                "можно создавать и удалять задачи;\n"
                "задачи можно переносить между колонками;\n"
                "состояние сохраняется в localStorage и восстанавливается после перезагрузки;\n"
                "добавь фильтр по тексту задачи;\n"
                "интерфейс должен нормально выглядеть на desktop и mobile;\n\n"
                "В итоге пришли в чат скриншот и файлы программы",
            )
            sent = [
                part.name or part.kind
                for message in g.tool_results
                for part in message.content
                if getattr(part, "outbound", False)
            ]
            failed += g.report(
                "G the person's request, plan off",
                {
                    "no plan tool was offered or called": "todo_write" not in g.tools
                    and "todo_write" not in agent.toolbox("chat-g").names,
                    "write_file then inspect_page": "write_file" in g.tools and "inspect_page" in g.tools,
                    "the files were sent": any(name.endswith(".html") for name in sent),
                    "the screenshot was sent": any(name.endswith(".png") for name in sent),
                    "no path was offered as delivery": "![" not in g.answer,
                    "no tool failed": not g.failures,
                    # Test 9, 2026-09-03: eleven writes and the ceiling. Test 8
                    # wrote four; more than five is the rewrite loop again.
                    "at most five write_file calls": g.tools.count("write_file") <= 5,
                    "the turn finished before its first health check": not g.health_checked(),
                },
            )
            print(f"  sent        {sent}")
            # Not a check: the model repeating beside-the-call text as its
            # closing message is ISS-0009, hidden by the Telegram adapter's
            # verbatim dedupe and left for 4.7. Said here so a run shows it.
            if len(g.text) > 1 and g.text[-1].strip() == g.text[0].strip():
                print("  note        the closing text repeats the text beside the call (ISS-0009)")
            elif len(g.text) > 1:
                print(f"  note        {len(g.text)} texts in the turn; the adapter shows each once")

        if wanted("H"):
            # H — the exact words behind the summary. A stored turn with one
            # failure, folded into a summary that keeps the fact of the failure
            # and loses its text, the way a summary does. The question is for
            # the text; the only place it exists is history.
            exact = "no such folder: board-7/assets — the parent 'board-7' is a file"
            agent.store.append(
                "chat-h",
                [
                    text_message("Create board-7/assets/app.js with a hello function."),
                    Message(
                        role="assistant",
                        tool_calls=(ToolCall(id="h1", name="write_file", arguments={"path": "board-7/assets/app.js", "content": "function hello() {}"}),),
                    ),
                    Message(
                        role="tool",
                        tool_call_id="h1",
                        content=[ContentPart(kind="text", text=f"error: {exact}")],
                        failure=ToolFailure(code="fs.blocked", message=exact),
                    ),
                    Message(role="assistant", content=[ContentPart(kind="text", text="The write failed: something in the way is a file. Shall I remove it?")]),
                    text_message("Not now."),
                    Message(role="assistant", content=[ContentPart(kind="text", text="Okay.")]),
                ],
                USER,
            )
            agent.store.set_summary(
                "chat-h",
                "Goal: create board-7/assets/app.js with a hello function.\n"
                "Done: one write_file attempt, which failed because of something in the path.\n"
                "Open: the person said not now.",
                6,
            )
            agent.store.record_compaction("chat-h", through=6, folded=6, trigger="asked", summary_chars=150)
            h = await Turn(agent, telemetry, 80).ask(
                "chat-h",
                "Какой точно был текст ошибки при той записи? Процитируй его дословно.",
            )
            failed += h.report(
                "H the exact words behind the summary",
                {
                    "history was searched or read": "search_history" in h.tools or "read_history" in h.tools,
                    "the exact text is in the answer": "board-7/assets" in h.answer and "is a file" in h.answer,
                    "nothing was written or retried": "write_file" not in h.tools,
                    "no tool failed": not h.failures,
                },
            )

        if wanted("I"):
            # I — a result already shortened on the surface. Three stored results,
            # so the first is a stub naming its position; the detail asked for is
            # in that one. The file is not on disk any more (the first run of
            # this, `live-90`, left it there and the model simply read it again,
            # which was fair), so the words exist only in history and the stub's
            # locator is the way back. Trying the file first is allowed.
            config = "\n".join(f"setting_{n} = {n * 7}" for n in range(1, 40)) + "\nretry_timeout = 4711\n"
            listing = "\n".join(f"file_{n}.txt" for n in range(1, 40))
            agent.store.append(
                "chat-i",
                [
                    text_message("Read config.ini, then list the workspace twice so I can compare."),
                    Message(role="assistant", tool_calls=(ToolCall(id="i1", name="read_file", arguments={"path": "config.ini"}),)),
                    Message(role="tool", tool_call_id="i1", content=[ContentPart(kind="text", text=config)]),
                    Message(role="assistant", tool_calls=(ToolCall(id="i2", name="list_files", arguments={"path": "."}),)),
                    Message(role="tool", tool_call_id="i2", content=[ContentPart(kind="text", text=listing)]),
                    Message(role="assistant", tool_calls=(ToolCall(id="i3", name="list_files", arguments={"path": "."}),)),
                    Message(role="tool", tool_call_id="i3", content=[ContentPart(kind="text", text=listing)]),
                    Message(role="assistant", content=[ContentPart(kind="text", text="Read config.ini (40 settings) and listed the workspace twice; the listings match.")]),
                ],
                USER,
            )
            i = await Turn(agent, telemetry, 90).ask(
                "chat-i",
                "What was the retry_timeout in the config we read earlier? Quote the line.",
            )
            failed += i.report(
                "I a shortened result, read back",
                {
                    "the value is in the answer": "4711" in i.answer,
                    "read back by position": "read_history" in i.tools,
                },
            )
            if "read_file" in i.tools:
                print("  note        the model tried the file first, then read history")

        if wanted("K"):
            # K — a fold in the middle of a turn. The conversation already
            # holds enough that a few steps of work push the request over a
            # small budget; `fitted` folds it between two steps and the turn
            # goes on. The checks are the fold event inside the turn, the
            # work done, and an answer — the roadmap's "continues correctly
            # across a compaction", on events.
            # Twelve stored messages: the last two exchanges always stay
            # verbatim, and a fold needs something older than that to fold.
            seeded = []
            for batch, street in enumerate(("elm", "oak", "ash", "fir", "yew", "bay")):
                notes = " ".join(
                    f"note {n}: the orchard at {n * 37} {street} street keeps {n * 3} trees"
                    for n in range(1, 12)
                )
                seeded.append(text_message(f"Orchard notes, batch {batch + 1}:\n{notes}"))
                seeded.append(
                    Message(
                        role="assistant",
                        content=[ContentPart(kind="text", text=f"Noted batch {batch + 1}.")],
                    )
                )
            agent.store.append("chat-k", seeded, USER)
            agent.context_tokens = 7600
            agent.rewire()
            k = await Turn(agent, telemetry, 100).ask(
                "chat-k",
                "In my workspace, write orchard.txt with twelve lines, one per month, "
                "each naming one job to do in an apple orchard that month. Then read it "
                "back, then write orchard-summary.txt with a two-line summary of it, "
                "and tell me which month has the most work.",
            )
            agent.context_tokens = None
            agent.rewire()
            kinds = k.event_types()
            folded_at = kinds.index("context_folded") if "context_folded" in kinds else -1
            failed += k.report(
                "K a fold in the middle of the turn",
                {
                    "the conversation was folded during the turn": folded_at >= 0,
                    "a model step followed the fold": folded_at >= 0
                    and "model_finished" in kinds[folded_at:],
                    "orchard.txt exists": (root / "orchard.txt").is_file(),
                    "an answer was given": bool(k.answer),
                    "the turn finished before its first health check": not k.health_checked(),
                },
            )

        if wanted("J"):
            # J — the worker dies while the model's tools are running, and a
            # fresh agent on the same checkpoints takes the turn up. What is
            # checked is the harness: the resume event, that the work exists,
            # that the resumed turn did not write again without looking first,
            # and that an answer came. Last, because the agent is replaced.
            killed = Turn(agent, telemetry, 110)
            work = asyncio.create_task(
                killed.ask(
                    "chat-j",
                    "In my workspace, write poem.txt with a four-line poem about apples, "
                    "then read it back and tell me its first line.",
                )
            )
            while not killed.tools and not work.done():
                await asyncio.sleep(0.05)
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            print("\n  (the turn was killed once the model asked for its first tool)")
            await agent.aclose()
            agent = agent_factory()
            left = await agent.unfinished("chat-j")
            j = await Turn(agent, telemetry, 111).take_up("chat-j")
            resumed = j.events("turn_resumed")
            first_write = j.tools.index("write_file") if "write_file" in j.tools else None
            looked_first = first_write is None or any(
                tool in ("read_file", "list_files") for tool in j.tools[:first_write]
            )
            failed += j.report(
                "J a worker killed mid-turn, taken up",
                {
                    "the checkpoint held an unfinished turn": left is not None,
                    "the turn was resumed, not restarted": bool(resumed),
                    "poem.txt exists": (root / "poem.txt").is_file(),
                    "no write without looking first": looked_first,
                    "an answer was given": bool(j.answer),
                },
            )
            if resumed:
                print(f"  resumed     {resumed[0]}")
        if wanted("O"):
            # O — the first command. The check is the loop and the tool: a file
            # written, a command run on this machine, and what it printed read
            # back into the answer.
            o = await Turn(agent, telemetry, 130).ask(
                "chat-o",
                "In my workspace, write primes.py that prints the prime numbers below "
                "50 on one line, run it with run_command, and tell me exactly what it "
                "printed.",
            )
            failed += o.report(
                "O a script written and run",
                {
                    "write_file then run_command": "write_file" in o.tools
                    and "run_command" in o.tools,
                    "no tool failed": not o.failures,
                    "the output reached the answer": "47" in o.answer,
                },
            )

        if wanted("P"):
            # P — the 4.3 acceptance that waited for this step: a PDF made with
            # whatever the model installs, looked at, and handed over. Asserted
            # on tools and outbound parts, never on wording.
            p = await Turn(agent, telemetry, 140).ask(
                "chat-p",
                "Make me a one-page PDF called apples.pdf about three kinds of apples, "
                "check that the PDF really contains that text, and send it to me.",
            )
            pdfs = list(root.glob("**/apples.pdf"))
            failed += p.report(
                "P a PDF made, checked, handed over",
                {
                    "run_command ran": "run_command" in p.tools,
                    "apples.pdf exists": bool(pdfs),
                    "the document was looked at": bool(
                        {"read_document", "view_pages"} & set(p.tools)
                    ),
                    "send_file ran": "send_file" in p.tools,
                    "an answer was given": bool(p.answer),
                },
            )

        if wanted("Q"):
            # Q — a command that does not finish. The tool's own timeout kills
            # it, the typed failure reaches the model, and the turn goes on.
            q = await Turn(agent, telemetry, 150).ask(
                "chat-q",
                "Run this exact command with run_command and timeout_seconds=3, then "
                "tell me what happened: python -c \"import time; time.sleep(60)\"",
            )
            codes = [why.code for _, why in q.failures]
            failed += q.report(
                "Q a command past its timeout",
                {
                    "run_command ran": "run_command" in q.tools,
                    "shell.timeout reached the loop": "shell.timeout" in codes,
                    "the model answered after it": bool(q.answer),
                    "it did not wait a minute": q.seconds < 45,
                },
            )

        if wanted("R"):
            # R — data into a picture. The second shape work with commands
            # takes: a file that is there, summed with whatever the model
            # runs, and a chart the person asked for, looked at and handed
            # over. The checks are the files, the look and the number, not
            # the library.
            (root / "sales.csv").write_text(
                "region,amount\nnorth,10\nsouth,25\nnorth,20\neast,15\nsouth,20\n",
                encoding="utf-8",
            )
            r = await Turn(agent, telemetry, 160).ask(
                "chat-r",
                "In my workspace there is sales.csv with the columns region and amount. "
                "Using run_command, compute the total amount per region, save a bar "
                "chart of those totals as chart.png in my workspace, look at the chart "
                "to check it, send it to me, and tell me which region has the largest "
                "total and what it is.",
            )
            failed += r.report(
                "R data turned into a picture",
                {
                    "run_command ran": "run_command" in r.tools,
                    "chart.png exists": (root / "chart.png").is_file(),
                    "the chart was looked at": "chart.png" in r.read_from("read_file"),
                    "send_file ran": "send_file" in r.tools,
                    "the answer names the largest total": "45" in r.answer,
                },
            )

        if wanted("S"):
            # S — a failing script repaired. The third shape: a check that
            # fails, a traceback that says why, one file changed, the check
            # green. Asserted on the last exit code and the file, so a rewrite
            # of the whole file and a one-line edit both pass; what is
            # measured is that the traceback was acted on, not how.
            (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (root / "check_calc.py").write_text(
                "from calc import add\n\nresult = add(2, 3)\nassert result == 5, "
                "f'add(2, 3) gave {result}, expected 5'\nprint('calc ok')\n",
                encoding="utf-8",
            )
            s_turn = await Turn(agent, telemetry, 170).ask(
                "chat-s",
                "In my workspace, check_calc.py fails when run with python. Run it with "
                "run_command, fix the bug in calc.py so that check_calc.py prints "
                "'calc ok', run it again to prove it, and tell me what was wrong.",
            )
            runs = [
                message
                for message in s_turn.tool_results
                if s_turn._names.get(message.tool_call_id or "") == "run_command"
            ]
            last = " ".join(part.text or "" for part in runs[-1].content) if runs else ""
            failed += s_turn.report(
                "S a failing script repaired",
                {
                    "the check was run at least twice": len(runs) >= 2,
                    "calc.py was changed": "a - b" not in (root / "calc.py").read_text(encoding="utf-8"),
                    "the last run is green": "exit code: 0" in last and "calc ok" in last,
                    "an answer was given": bool(s_turn.answer),
                },
            )

    finally:
        await agent.aclose()
        telemetry.close()

    print(f"\n{'all scenarios passed' if not failed else f'{failed} check(s) failed'}")
    return failed


def deployed(selected) -> int:
    """The same scenarios, in the deployed worker, through its `scenarios` Function."""

    import modal

    function = modal.Function.from_name("assistant-control", "scenarios")
    text, failed = function.remote("".join(sorted(selected)))
    print(text)
    print("Read any of them back with:  python tools/show_run.py --last 20   (the deployed database)")
    return failed


async def main() -> int:
    argv = sys.argv[1:]
    selected = chosen(argv)
    if "--deployed" in argv:
        return deployed(selected)

    room = Path(tempfile.mkdtemp(prefix="loop-live-"))
    settings = settings_in(room)
    telemetry = open_telemetry(settings)
    stops = MemoryStopRequests()

    def factory():
        return create_agent(
            agent_settings=settings, user_id=USER, telemetry=telemetry, stops=stops
        )

    print(f"telemetry {settings.telemetry_database}")
    failed = await run_scenarios(selected, factory(), telemetry, factory)
    print(
        "\nItem 3 baseline, 2026-08-29 (reports/2026-08-29_v2_gpu_baseline_measured.md,"
        " reports/2026-08-30_v2_step4_harness_preparation.md):\n"
        "  six live turns, 21.22 s derived GPU, $0.0065 a successful turn;\n"
        "  prefill ~2.3k tok/s, decode ~45 tok/s, a 12-second idle window charged to each turn."
    )
    print(
        f"\nRead any of them back with:\n  AGENT_TELEMETRY_DATABASE={settings.telemetry_database} "
        f"AGENT_DATABASE_URL= python tools/show_run.py --last 10 --summary"
    )
    return failed


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(main(), loop_factory=loop_factory))
