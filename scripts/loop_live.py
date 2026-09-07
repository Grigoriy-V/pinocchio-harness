"""A live check of the one loop against a real model. Every run is paid.

    .venv\\Scripts\\python.exe -m scripts.loop_live                 the mini set, here
    .venv\\Scripts\\python.exe -m scripts.loop_live --deployed      the mini set, in the
                                                                deployed worker
    .venv\\Scripts\\python.exe -m scripts.loop_live --both          both, and a table of
                                                                the two side by side
    .venv\\Scripts\\python.exe -m scripts.loop_live B M             letters, here
    .venv\\Scripts\\python.exe -m scripts.loop_live --deployed R S  letters, deployed

Both profiles run bare (the human, 2026-09-07): no `AGENTS.md`, an empty
workspace and an empty conversation for every scenario, in a temporary
directory here and in the probe user's own directory on the Volume there,
cleared first. A scenario seeds the files it needs and nothing else. What
differs between the two runs is the environment — image, runner, database —
and the table `--both` prints is what that difference costs.

**The mini set** (item 15): one scenario per capability, checked on harness
events and the store, never on the model's wording; the request text is
literal. A scenario passes when every line under it passes; the set is
accepted when all eight pass deployed in one run.

    A  a plain question               one model call, no tool, an answer
    B  one tool                       the tool ran with valid arguments, its
                                      result was in the next request, the
                                      answer uses it
    C  files and a command            write_file, run_command exit 0, the
                                      command's output in the answer
    F  the browser                    a page the model wrote, opened with
                                      use_page, clicked twice, the effect read
    W  the web                        a fixed page fetched, its title in the
                                      answer
    H  memory and history             a fact saved in one turn is found in a
                                      later one; the exact words behind a
                                      summary are read from history
    E  a failing tool                 the typed failure reached the model,
                                      the turn went on, telemetry says why
    M  control                        a message sent mid-turn is taken at the
                                      next step and the task still finishes;
                                      a stop ends a turn at its next step

**The wider set** (item 19), by letter only: G the person's own request, I a
shortened result read back, J a worker killed mid-turn, K a fold inside a
turn, O a script run, P a PDF, Q a command past its timeout, R data into a
picture, S a failing script repaired. Their checks are unchanged from when
they were evidence (`reports/2026-09-05_suite_and_tools_review.md`).

It writes into a temporary directory and its own telemetry file locally;
deployed, into the probe user's threads and the deployed telemetry. The run
ids it prints can be read back with

    AGENT_TELEMETRY_DATABASE=<the file it names> python tools/show_run.py <id>
    python tools/show_run.py --last 20                    (the deployed database)
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.agent.interjections import MemoryInterjections
from app.agent.runtime import create_agent, text_message
from app.agent.stop import MemoryStopRequests
from app.config import AgentSettings
from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.telemetry import TurnRun
from app.telemetry.open import open_telemetry

USER = "loop-live-check"

MINI = "ABCFWHEM"
WIDER = "GIJKOPQRS"


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


@dataclass
class Result:
    """One scenario's line, in a shape `--both` can lay beside another's."""

    letter: str
    name: str
    failed: int
    seconds: float
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int

    @property
    def passed(self) -> bool:
        return self.failed == 0


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
        self.calls: list[tuple[str, dict]] = []
        self.failures: list[tuple[str, ToolFailure]] = []
        self.tool_results: list[Message] = []
        self.approvals = 0
        self.run_id = f"{RUN_PREFIX}{sequence}"
        self._names: dict[str, str] = {}
        self.seconds = 0.0

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
            self.calls.append((call.name, dict(call.arguments)))
        if message.role == "tool":
            self.tool_results.append(message)
            if message.failure is not None:
                self.failures.append(
                    (self._names.get(message.tool_call_id or "", "?"), message.failure)
                )
        said = " ".join(part.text or "" for part in message.content).strip()
        if said and message.role == "assistant":
            self.text.append(said)

    def arguments_of(self, tool: str) -> list[dict]:
        """The arguments of every call of this tool, in order."""

        return [arguments for name, arguments in self.calls if name == tool]

    def read_from(self, tool: str) -> str:
        """The text the model was given back by every call of this tool."""

        calls = {call_id for call_id, called in self._names.items() if called == tool}
        return " ".join(
            part.text or ""
            for message in self.tool_results
            if message.tool_call_id in calls
            for part in message.content
        )

    def outbound(self) -> list[str]:
        """What a presentation tool marked as sent, by name or kind."""

        return [
            part.name or part.kind
            for message in self.tool_results
            for part in message.content
            if getattr(part, "outbound", False)
        ]

    @property
    def answer(self) -> str:
        return self.text[-1] if self.text else ""

    @property
    def said(self) -> str:
        return " ".join(self.text)

    def health_checked(self) -> bool:
        """Whether the turn ran long enough to be asked how it was doing."""

        return bool(self.events("turn_health_check"))

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

    def failed_events(self) -> list[dict]:
        return self.events("tool_failed")

    def report(self, name: str, checks: dict[str, bool]) -> int:
        print(f"\n{name}")
        print(
            f"  model calls {self.run.model_calls}   tool calls {self.run.tool_calls}"
            f"   approvals {self.approvals}   tokens {self.run.input_tokens} in / "
            f"{self.run.output_tokens} out"
        )
        print(f"  tools       {self.tools or '-'}")
        if self.failures:
            print("  failures    " + "; ".join(f"{tool}: {why.code}" for tool, why in self.failures))
        print(f"  seconds     {self.seconds:6.2f}   run {self.run_id}")
        print(f"  answer      {(self.answer or '(nothing said)')[:160]}")
        for expectation, held in checks.items():
            print(f"  {'PASS' if held else 'FAIL'}  {expectation}")
        return sum(1 for held in checks.values() if not held)


def chosen(argv: list[str]) -> frozenset[str]:
    """Which scenarios to run: letters, or the mini set."""

    letters = {arg.upper() for arg in argv if len(arg) == 1 and arg.upper() in MINI + WIDER}
    return frozenset(letters) if letters else frozenset(MINI)


# The run ids one invocation writes: `live-<sequence>` on this machine, in a
# telemetry file of the run's own; deployed, a prefix of the invocation's own
# so two runs into one database never share an id.
RUN_PREFIX = "live-"


def threads_of(letter: str) -> list[str]:
    """The conversations a scenario uses; a second one where a scenario has two."""

    return [f"chat-{letter.lower()}", f"chat-{letter.lower()}2"]


async def start_clean(agent, selected, root: Path) -> None:
    """Every scenario begins in an empty conversation and an empty workspace.

    Locally that is what the sealed room gives for free. Deployed, the probe
    user's threads and workspace live in the real database and on the Volume
    and outlive the run: on 2026-09-05 the third G sample answered from the
    history of the two before it, in one model call and no tool, and measured
    nothing (run `deployed-808b8717-70`). What a scenario measures is one
    request from nothing, in both profiles — and with the workspace goes any
    `AGENTS.md`, so neither profile runs with standing instructions.
    """

    from app.conversations import delete_conversation

    for letter in sorted(selected):
        for thread in threads_of(letter):
            await delete_conversation(agent.store, thread, agent.checkpoints)
    for entry in sorted(Path(root).iterdir(), key=lambda path: path.is_dir()):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


async def run_scenarios(
    selected, agent, telemetry, agent_factory, prefix: str = "live-"
) -> tuple[int, list[Result]]:
    """Run the chosen scenarios against one agent and print the report.

    `agent_factory` makes a fresh agent the way this one was made — J needs
    one after it kills the first. Closes the agent and the telemetry at the
    end. Returns the number of failed checks and one `Result` per scenario.
    """

    global RUN_PREFIX
    RUN_PREFIX = prefix

    def wanted(letter: str) -> bool:
        return letter in selected

    root = agent.capability_grant.root
    print(f"workspace {root}")
    await start_clean(agent, selected, root)
    failed = 0
    rows: list[Result] = []

    def done(letter: str, name: str, *turns: Turn, checks: dict[str, bool]) -> None:
        """Report the scenario's checks and keep its line."""

        nonlocal failed
        last = turns[-1]
        for turn in turns[:-1]:
            turn.report(f"{name} — turn {turn.sequence}", {})
        misses = last.report(name, checks)
        failed += misses
        rows.append(
            Result(
                letter=letter,
                name=name,
                failed=misses,
                seconds=sum(turn.seconds for turn in turns),
                model_calls=sum(turn.run.model_calls for turn in turns),
                tool_calls=sum(turn.run.tool_calls for turn in turns),
                input_tokens=sum(turn.run.input_tokens for turn in turns),
                output_tokens=sum(turn.run.output_tokens for turn in turns),
            )
        )

    try:
        # --- the mini set -----------------------------------------------------

        if wanted("A"):
            a = await Turn(agent, telemetry, 10).ask("chat-a", "Привет! Как ты?")
            done(
                "A", "A a plain question", a,
                checks={
                    "one model call": a.run.model_calls == 1,
                    "no tool": not a.tools,
                    "an answer was given": bool(a.answer),
                },
            )

        if wanted("B"):
            (root / "notes.txt").write_text("The passphrase is marmalade.", encoding="utf-8")
            b = await Turn(agent, telemetry, 20).ask(
                "chat-b", "Read notes.txt in my workspace and tell me the passphrase."
            )
            done(
                "B", "B one tool", b,
                checks={
                    "read_file ran": "read_file" in b.tools,
                    "no tool failed": not b.failures,
                    "the result reached the model": "marmalade" in b.read_from("read_file"),
                    "the answer uses it": "marmalade" in b.answer.lower(),
                },
            )

        if wanted("C"):
            c = await Turn(agent, telemetry, 30).ask(
                "chat-c",
                "In my workspace, write primes.py that prints the prime numbers below "
                "50 on one line, run it with run_command, and tell me exactly what it "
                "printed.",
            )
            done(
                "C", "C files and a command", c,
                checks={
                    "write_file then run_command": "write_file" in c.tools and "run_command" in c.tools,
                    "primes.py exists": (root / "primes.py").is_file(),
                    "the command exited 0": "exit code: 0" in c.read_from("run_command"),
                    "no tool failed": not c.failures,
                    "the output reached the answer": "47" in c.answer,
                },
            )

        if wanted("F"):
            f = await Turn(agent, telemetry, 60).ask(
                "chat-f",
                "In my workspace, write a small self-contained page counter.html with a "
                "heading, a button labelled Count and a script that increments a number "
                "in the heading when the button is pressed. Then open it with use_page, "
                "press the button twice, and tell me what the heading says after that.",
            )
            # The whole point of the page tool (roadmap 16): the model acts on
            # the page and reads the effect, rather than looking at it once.
            actions = f.arguments_of("use_page")
            done(
                "F", "F the browser", f,
                checks={
                    "write_file then use_page": "write_file" in f.tools and "use_page" in f.tools,
                    "counter.html exists": (root / "counter.html").is_file(),
                    "no tool failed": not f.failures,
                    "the page was opened": any(a.get("action") == "open" for a in actions),
                    "the model read a structure with a ref": "[ref=e" in f.read_from("use_page"),
                    "the button was clicked twice": sum(a.get("action") == "click" for a in actions) >= 2,
                    "the answer says 2": "2" in f.answer,
                },
            )

        if wanted("W"):
            w = await Turn(agent, telemetry, 65).ask(
                "chat-w",
                "Open https://example.com and tell me the exact text of its heading.",
            )
            # Since roadmap 16 a public address can also be opened with
            # use_page: the check is that the page was read, whichever way.
            fetched = (
                w.read_from("fetch_page") + w.read_from("view_web_page") + w.read_from("use_page")
            )
            done(
                "W", "W the web", w,
                checks={
                    "a web tool ran": bool({"fetch_page", "view_web_page", "use_page"} & set(w.tools)),
                    "no tool failed": not w.failures,
                    "the page reached the model": "Example Domain" in fetched,
                    "the heading is in the answer": "example domain" in w.answer.lower(),
                },
            )

        if wanted("H"):
            # Memory: a fact saved in one conversation, asked for in another.
            h1 = await Turn(agent, telemetry, 80).ask(
                "chat-h", "Remember this: my favourite apple variety is Antonovka."
            )
            h2 = await Turn(agent, telemetry, 81).ask(
                "chat-h2", "What is my favourite apple variety?"
            )
            # History: the exact words behind a summary. A stored turn with one
            # failure, folded into a summary that keeps the fact of the failure
            # and loses its text; the only place the text exists is history.
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
            stored = len(agent.store.messages("chat-h"))
            agent.store.set_summary(
                "chat-h",
                "Goal: remember the favourite apple variety; then create "
                "board-7/assets/app.js with a hello function.\n"
                "Done: the fact was saved; one write_file attempt, which failed because "
                "of something in the path.\n"
                "Open: the person said not now.",
                stored,
            )
            agent.store.record_compaction("chat-h", through=stored, folded=stored, trigger="asked", summary_chars=150)
            h3 = await Turn(agent, telemetry, 82).ask(
                "chat-h",
                "Какой точно был текст ошибки при той записи? Процитируй его дословно.",
            )
            done(
                "H", "H memory and history", h1, h2, h3,
                checks={
                    "remember_fact ran": "remember_fact" in h1.tools,
                    "the fact was found in another conversation": "antonovka" in h2.answer.lower(),
                    "history was searched or read": "search_history" in h3.tools or "read_history" in h3.tools,
                    "the exact text is in the answer": "board-7/assets" in h3.answer and "is a file" in h3.answer,
                    "nothing was written or retried": "write_file" not in h3.tools,
                    "no tool failed": not (h1.failures or h2.failures or h3.failures),
                },
            )

        if wanted("E"):
            # The failure is one the model cannot see coming from a listing:
            # the text it is asked to edit occurs twice, so the first
            # `edit_file` is refused as ambiguous.
            (root / "fruit.txt").write_text("apple pie\napple tart\n", encoding="utf-8")
            e = await Turn(agent, telemetry, 50).ask(
                "chat-e",
                "In my workspace, use edit_file on fruit.txt to replace the word "
                "'apple' with 'pear'. Do not read the file first and do not rewrite it "
                "with write_file. Then tell me what happened.",
            )
            codes = [why.code for _, why in e.failures]
            done(
                "E", "E a failing tool", e,
                checks={
                    "edit_file ran": "edit_file" in e.tools,
                    "the failure reached the loop as fs.ambiguous_edit": "fs.ambiguous_edit" in codes,
                    "the model answered after the failure": bool(e.answer),
                    "the turn was not ended by the repeat guard": e.run.model_calls <= 4,
                    "tool_failed carries code and message": any(
                        event.get("code") == "fs.ambiguous_edit" and event.get("message")
                        for event in e.failed_events()
                    ),
                },
            )

        if wanted("M"):
            # A message while the turn works: offered once the model has asked
            # for its first tool, taken at the end of that batch, answered, and
            # the task still finished (ISS-0059 is exactly this not happening).
            lane = agent.interjections
            assert isinstance(lane, MemoryInterjections), "M needs the memory lane"
            m = Turn(agent, telemetry, 180)
            work = asyncio.create_task(
                m.ask(
                    "chat-m",
                    "In my workspace, write three files note1.txt, note2.txt and "
                    "note3.txt, one at a time, each with one sentence about apples, "
                    "reading each one back after you write it. Then tell me you are done.",
                )
            )
            while not m.tools and not work.done():
                await asyncio.sleep(0.05)
            await lane.offer(USER, 181, text_message("By the way, what is 12 times 12? Answer, then continue."))
            print("\n  (a message was sent while the turn was running)")
            await work
            positions = [message.role for message in agent.store.messages("chat-m")]
            mid = [index for index, role in enumerate(positions) if role == "user" and index > 0]
            # A stop recorded while a second turn is running.
            stopped = Turn(agent, telemetry, 190)
            work = asyncio.create_task(
                stopped.ask(
                    "chat-m2",
                    "In my workspace, create five files apple1.txt to apple5.txt, each "
                    "with one sentence about apples, reading each one back after you "
                    "write it.",
                )
            )
            while len(stopped.tools) < 2 and not work.done():
                await asyncio.sleep(0.05)
            await agent.stops.request(USER, 191)
            print("\n  (stop recorded while the turn was running)")
            await work
            done(
                "M", "M control: a message mid-turn, then a stop", m, stopped,
                checks={
                    "the message was taken mid-turn": bool(m.events("turn_interjected")),
                    "it is stored where the turn read it": bool(mid),
                    "it was answered": "144" in m.said,
                    "the task was still finished": all(
                        (root / f"note{n}.txt").is_file() for n in (1, 2, 3)
                    ),
                    "no tool failed": not m.failures,
                    "the stopped turn ended with the stop message": stopped.answer == "Stopped at your request.",
                    "fewer than five files were made": len(list(root.glob("apple*.txt"))) < 5,
                },
            )

        # --- the wider set ------------------------------------------------------

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
            sent = g.outbound()
            done(
                "G", "G the person's request, plan off", g,
                checks={
                    "no plan tool was offered or called": "todo_write" not in g.tools
                    and "todo_write" not in agent.toolbox("chat-g").names,
                    "write_file then use_page": "write_file" in g.tools and "use_page" in g.tools,
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
            if len(g.text) > 1 and g.text[-1].strip() == g.text[0].strip():
                print("  note        the closing text repeats the text beside the call (ISS-0009)")
            elif len(g.text) > 1:
                print(f"  note        {len(g.text)} texts in the turn; the adapter shows each once")

        if wanted("I"):
            # I — a result already shortened on the surface. Three stored results,
            # so the first is a stub naming its position; the detail asked for is
            # in that one. The file is not on disk, so the words exist only in
            # history and the stub's locator is the way back.
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
            done(
                "I", "I a shortened result, read back", i,
                checks={
                    "the value is in the answer": "4711" in i.answer,
                    "read back by position": "read_history" in i.tools,
                },
            )
            if "read_file" in i.tools:
                print("  note        the model tried the file first, then read history")

        if wanted("K"):
            # K — a fold in the middle of a turn: the conversation already holds
            # enough that a few steps push the request over a small budget;
            # `fitted` folds it between two steps and the turn goes on.
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
            done(
                "K", "K a fold in the middle of the turn", k,
                checks={
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
            # fresh agent on the same checkpoints takes the turn up. Last,
            # because the agent is replaced.
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
            done(
                "J", "J a worker killed mid-turn, taken up", j,
                checks={
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
            o = await Turn(agent, telemetry, 130).ask(
                "chat-o",
                "In my workspace, write primes.py that prints the prime numbers below "
                "50 on one line, run it with run_command, and tell me exactly what it "
                "printed.",
            )
            done(
                "O", "O a script written and run", o,
                checks={
                    "write_file then run_command": "write_file" in o.tools and "run_command" in o.tools,
                    "no tool failed": not o.failures,
                    "the output reached the answer": "47" in o.answer,
                },
            )

        if wanted("P"):
            p = await Turn(agent, telemetry, 140).ask(
                "chat-p",
                "Make me a one-page PDF called apples.pdf about three kinds of apples, "
                "check that the PDF really contains that text, and send it to me.",
            )
            pdfs = list(root.glob("**/apples.pdf"))
            done(
                "P", "P a PDF made, checked, handed over", p,
                checks={
                    "run_command ran": "run_command" in p.tools,
                    "apples.pdf exists": bool(pdfs),
                    "the document was looked at": bool({"read_document", "view_pages"} & set(p.tools)),
                    "send_file ran": "send_file" in p.tools,
                    "an answer was given": bool(p.answer),
                },
            )

        if wanted("Q"):
            q = await Turn(agent, telemetry, 150).ask(
                "chat-q",
                "Run this exact command with run_command and timeout_seconds=3, then "
                "tell me what happened: python -c \"import time; time.sleep(60)\"",
            )
            codes = [why.code for _, why in q.failures]
            done(
                "Q", "Q a command past its timeout", q,
                checks={
                    "run_command ran": "run_command" in q.tools,
                    "shell.timeout reached the loop": "shell.timeout" in codes,
                    "the model answered after it": bool(q.answer),
                    "it did not wait a minute": q.seconds < 45,
                },
            )

        if wanted("R"):
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
            done(
                "R", "R data turned into a picture", r,
                checks={
                    "run_command ran": "run_command" in r.tools,
                    "chart.png exists": (root / "chart.png").is_file(),
                    "the chart was looked at": "chart.png" in r.read_from("read_file"),
                    "send_file ran": "send_file" in r.tools,
                    "the answer names the largest total": "45" in r.answer,
                },
            )

        if wanted("S"):
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
            done(
                "S", "S a failing script repaired", s_turn,
                checks={
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
    return failed, rows


def side_by_side(local: list[Result], remote: list[Result]) -> str:
    """The two runs as one table, a scenario per line, the difference last."""

    here = {row.letter: row for row in local}
    there = {row.letter: row for row in remote}
    width = 36
    lines = [
        f"{'':<3}{'local':<{width}}{'deployed':<{width}}{'difference':<12}",
        f"{'':<3}{'result  seconds  calls  tokens in/out':<{width}}"
        f"{'result  seconds  calls  tokens in/out':<{width}}{'seconds':<12}",
    ]

    def cell(row: Result | None) -> str:
        if row is None:
            return f"{'-':<{width}}"
        verdict = "PASS" if row.passed else f"FAIL {row.failed}"
        return (
            f"{verdict:<7} {row.seconds:7.1f}  {row.model_calls:>2}m/{row.tool_calls:<2}t "
            f"{row.input_tokens:>6}/{row.output_tokens:<4}"
        ).ljust(width)

    for letter in sorted(set(here) | set(there), key=lambda l: (MINI + WIDER).index(l)):
        a, b = here.get(letter), there.get(letter)
        delta = f"{b.seconds - a.seconds:+7.1f}" if a and b else "-"
        lines.append(f"{letter:<3}{cell(a)}{cell(b)}{delta}")
    return "\n".join(lines)


def deployed(selected) -> tuple[int, list[Result]]:
    """The same scenarios, in the deployed worker, through its `scenarios` Function."""

    import modal

    function = modal.Function.from_name("assistant-control", "scenarios")
    text, failed, rows = function.remote("".join(sorted(selected)))
    # The deployed telemetry also logs every event to stdout as one JSON line;
    # the report is the rest.
    print("\n".join(line for line in text.splitlines() if not line.startswith('{"run_id"')))
    print("Read any of them back with:  python tools/show_run.py --last 20   (the deployed database)")
    return failed, [Result(**row) for row in rows]


async def local(selected) -> tuple[int, list[Result]]:
    room = Path(tempfile.mkdtemp(prefix="loop-live-"))
    settings = settings_in(room)
    telemetry = open_telemetry(settings)
    stops = MemoryStopRequests()
    lane = MemoryInterjections()

    def factory():
        return create_agent(
            agent_settings=settings,
            user_id=USER,
            telemetry=telemetry,
            stops=stops,
            interjections=lane,
        )

    print(f"telemetry {settings.telemetry_database}")
    failed, rows = await run_scenarios(selected, factory(), telemetry, factory)
    print(
        f"\nRead any of them back with:\n  AGENT_TELEMETRY_DATABASE={settings.telemetry_database} "
        f"AGENT_DATABASE_URL= python tools/show_run.py --last 10"
    )
    return failed, rows


async def main() -> int:
    argv = sys.argv[1:]
    selected = chosen(argv)
    if "--both" in argv:
        print("=== local ===")
        failed_here, here = await local(selected)
        print("\n=== deployed ===")
        failed_there, there = deployed(selected)
        print("\n=== side by side ===")
        print(side_by_side(here, there))
        return failed_here + failed_there
    if "--deployed" in argv:
        return deployed(selected)[0]
    return (await local(selected))[0]


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(main(), loop_factory=loop_factory))
