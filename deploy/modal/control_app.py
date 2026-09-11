"""Modal CPU control plane for the deployed Telegram adapter.

Deploying this module never redeploys the model app. The unauthenticated HTTP
function verifies Telegram's own secret and allow list, persists the update,
spawns a separate CPU function and returns immediately. Only that worker calls
the existing transport-neutral ``TelegramAdapter``.

No function is invoked by importing this module. Database migrations are an
explicit local operation through ``tools/setup_control_plane.py``.
"""

import time
from pathlib import Path

import modal
from fastapi import Request

APP_NAME = "assistant-control"
SECRET_NAME = "assistant-control"
WORKSPACE_ROOT = "/workspaces"
# The worker container's life, the platform's own clock. A turn is bounded by
# its health check, not by this; this is a guard against a container that
# never ends. The same number is `ui.telegram.webhook.WORKER_TIMEOUT_SECONDS`
# (a test keeps them equal), which derives the drain window from it; the
# conversation lease is a heartbeat and no longer depends on this.
WORKER_TIMEOUT_SECONDS = 4 * 3600

app = modal.App(APP_NAME)
control_secret = modal.Secret.from_name(SECRET_NAME)

# Build only from the lock file, and copy only application source. In particular,
# never copy the repository's .env, data, reports or human workspaces into an
# image layer.
#
# The order below is load-bearing. Everything that changes rarely is built first
# and the source is copied last, because a copied directory invalidates every
# layer after it and the source changes on every single deploy. Chromium sat
# above the copy once: it pulls the whole X11 stack even for headless use, and
# every deploy reinstalled all of it — minutes, each time, for a one-line edit.
_dependencies = modal.Image.debian_slim(python_version="3.12").uv_sync(
    ".",
    groups=["app", "agent", "postgres", "deploy", "documents"],
    frozen=True,
    extra_options="--no-install-project",
)

# The browser, added before the source rather than after it, so it is installed
# once and then restored from cache until the lock file itself changes.
#
# The fonts are not cosmetic. Without them a screenshot of Cyrillic text is a row
# of boxes, and the assistant would be handing the model a picture of nothing.
_with_browser = _dependencies.apt_install(
    "chromium", "fonts-liberation", "fonts-dejavu-core"
)


def _with_source(image: modal.Image) -> modal.Image:
    """Put this repository's application code into an image, and nothing else.

    One function for both images so a file can never be present in one and
    missing from the other — the failure that would show up as a capability
    working in the webhook and not in the worker.
    """

    return (
        image.add_local_dir("app", "/root/project/app", copy=True)
        .add_local_dir("ui", "/root/project/ui", copy=True)
        # The live scenario runner, so `scenarios` below can drive the agent
        # here the way `loop_live` drives it on a person's machine.
        .add_local_dir("scripts", "/root/project/scripts", copy=True)
        .add_local_file(
            "deploy/modal/control_app.py", "/root/project/control_app.py", copy=True
        )
        # Settings that are not secrets; `app/config.py` reads it from the
        # project root, which is `/root/project` here.
        .add_local_file("config.toml", "/root/project/config.toml", copy=True)
        .env({"PYTHONPATH": "/root/project"})
    )


control_image = _with_source(_dependencies)

# The agent's image is the same thing with a browser under it. The webhook — the
# one function whose cold start a person waits on — never renders anything, so it
# stays on the smaller one.
#
# `WEB_LOCAL_BROWSER=0` is part of the image rather than of the secret because it
# is a fact about this container, not a credential: the agent worker holds the
# bot token, the model key and the database URL, so it does not open a web page
# in a browser of its own. It carries Chromium for `use_page`, which renders
# a local artifact with the network blocked; a page from the internet goes to
# `render_web_page` below. If that renderer is not configured, viewing fails and
# says so instead of silently running someone's JavaScript next to the secrets.
agent_image = _with_source(_with_browser).env(
    {"AGENT_WORKSPACE": WORKSPACE_ROOT, "WEB_LOCAL_BROWSER": "0"}
)

# The renderer runs the same code from the same layers and is a different image
# only in what it is *not* given: no workspace path, and below, no secret and no
# volume. Sharing the build keeps the browser a single installation.
render_image = _with_source(_with_browser)

# What a command finds installed where it runs (`DECISIONS.md` 2026-09-04): a
# developer's base set, so the everyday cases need no install at all. Not
# LibreOffice, for its size. Python is the image's own 3.12 with `pip` and
# `venv`; what a project needs beyond this goes into a venv in the workspace,
# which is on the Volume and outlives the container.
#
# The fonts are the renderer's, for the renderer's reason: a document made
# where there is no font with Cyrillic comes out as black squares (P, live,
# 2026-09-04), and this assistant is used in Russian. debian_slim has none.
BASE_TOOLS = (
    "fonts-dejavu-core",
    "fonts-liberation",
    "nodejs",
    "npm",
    "git",
    "curl",
    "zip",
    "unzip",
    "tar",
    "jq",
    "ffmpeg",
    "imagemagick",
    "poppler-utils",
    "pandoc",
)

# The libraries a person's everyday documents and data need, in the image
# rather than in a venv on the Volume (the human's list, 2026-09-04). Measured
# reason: a venv on a Modal Volume pays per file — a no-op `pip install` took
# 55 s cold and the first import of fonttools 50 s — and the venv itself was
# the first step of every turn, made again and again. With these here, a venv
# in the workspace is the exception a project needs, not the way in.
# `uv_pip_install`, because `uv_sync` above made the image's Python a uv venv
# (`/.uv/.venv`) that carries no `pip` module; a `pip_install` layer fails on
# it (measured on the first build, 2026-09-04).
BASE_PACKAGES = (
    # `pip` itself, into the image's uv venv: without it `pip` on the command's
    # PATH was the base interpreter's, so `pip list` showed three packages
    # while `python3` had them all, and a `pip install` would have landed
    # where `python3` never looks (thread `e8c54e07`, 2026-09-04; ISS-0043).
    # One interpreter behind `python3`, `pip` and `python3 -m pip`.
    "pip",
    "reportlab",
    "fpdf2",
    "python-docx",
    "openpyxl",
    "pandas",
    "matplotlib",
    "pillow",
    "pypdf",
    "markdown",
)

# Where a command runs: the same layers as the worker, the base tools and
# packages on top, no browser. Like the renderer it is defined by what it is
# not given below: no secret. The Volume it does get, because the workspace is
# the one thing a command and the worker have to agree on.
command_image = _with_source(_dependencies.apt_install(*BASE_TOOLS).uv_pip_install(*BASE_PACKAGES))

# Where the workspace stops dying with the container. A container's filesystem
# is gone the moment it scales down, so a file the assistant wrote in one
# message did not exist in the next — the capability was advertised and only
# ever half true.
#
# A volume, not a sandbox. The two questions this item raised — does a file
# survive, and where is untrusted content allowed to run — are separate, and
# only the second needs a sandbox. This one starts nothing and costs storage.
#
# Sharing one volume is not sharing one workspace: `user_workspace` puts each
# person in their own directory inside it, which is the same boundary the local
# profile has.
workspaces = modal.Volume.from_name("assistant-workspaces", create_if_missing=True)

# Load the webhook's modules in the container's global scope. `Image.imports` is
# what makes that safe: the block runs only inside the container, so importing
# this file locally to deploy it stays as cheap as before. The function bodies
# below still import what they use, which after this is a dictionary lookup —
# they are left that way so each function still reads as a list of what it
# depends on.
#
# Only the webhook's path is here, deliberately. Importing the agent stack would
# put it in every webhook container too, and that import is exactly what this
# function's cold start was paying for: 1.67-3.86 s of execution before the
# stack was moved out of reach, 0.34-0.46 s after.
#
# The worker keeps its imports inside itself until its own cold start has been
# measured rather than assumed.
with control_image.imports():
    from fastapi.responses import JSONResponse  # noqa: F401

    from app.config import AgentSettings, TelegramSettings  # noqa: F401
    from ui.telegram.inbox import PostgresUpdateInbox  # noqa: F401
    from ui.telegram.webhook import TelegramWebhook  # noqa: F401


# The container's own count, so the first command in a fresh container can say
# so: nothing an earlier command installed into the container is here, and what
# is in the workspace is. This is what "the container is disposable" means to
# the model, said once in the result instead of assumed.
_commands_run = 0


@app.function(
    image=command_image,
    # No `secrets`, and that is the function: a command the model wrote, or a
    # package it installed, runs where there is no TELEGRAM_TOKEN, no
    # MODEL_API_KEY and no AGENT_DATABASE_URL to read. The Volume is mounted
    # whole, as it is in the worker: one operator today, and a command in one
    # person's directory can reach another's, which is the known limit until
    # workspaces are mounted one at a time.
    volumes={WORKSPACE_ROOT: workspaces},
    cpu=1.0,
    memory=2048,
    min_containers=0,
    max_containers=8,
    # Three minutes, the human's number (2026-09-04): long enough that the
    # commands of one piece of work land in one container, so a `pip install`
    # is still there for the run that follows it a minute later, and short
    # enough that an idle container is not paid for. Anything a person needs
    # past that is in the workspace.
    scaledown_window=180,
    # Above the tool's own ceiling (`MAX_TIMEOUT`, 600 s) and the executor's
    # deadline on top of it, so the command is always the thing that is killed,
    # by the runner, with its partial output kept.
    timeout=660,
    include_source=False,
)
async def run_command(workspace: str, command: str, timeout: float) -> dict[str, object]:
    """Run one shell command in one person's workspace, beside the secrets, not with them.

    `workspace` is the person's directory name inside the Volume, never a path
    the caller chose freely: it is resolved under `WORKSPACE_ROOT` and refused
    if it climbs out. The Volume is reloaded before the command, so it sees
    what the worker wrote this turn, and committed after, so the worker sees
    what the command wrote — the round trip that makes one filesystem out of
    two containers.

    The result is a plain dictionary: what `Finished` holds, or `failure` with
    the runner's own code, so the worker can raise the same typed `ToolError`
    it would have raised locally.
    """

    global _commands_run

    from app.tools.base import ToolError
    from app.tools.shell import ContainerRunner

    root = Path(WORKSPACE_ROOT)
    # The mount path, not `resolve()`'s real one: the Volume is a symlink to
    # `/__modal/volumes/vo-…`, and a socket a tool binds relative to a cwd
    # that long is over the 108-byte limit (ISS-0058). The climb check alone
    # looks at the real path.
    cwd = root / workspace
    real_root, real_cwd = root.resolve(), cwd.resolve()
    if real_cwd == real_root or real_root not in real_cwd.parents:
        return {"failure": {"code": "shell.not_started", "message": "the workspace is not inside the volume", "detail": None}}
    fresh = _commands_run == 0
    _commands_run += 1
    await workspaces.reload.aio()
    try:
        # The worker made the person's directory before it wrote anything; a
        # probe with a name of its own gets one made here, inside the root.
        cwd.mkdir(parents=True, exist_ok=True)
        finished = await ContainerRunner().run(command, cwd, timeout)
    except ToolError as error:
        return {"failure": {"code": error.code, "message": str(error), "detail": error.detail}}
    finally:
        await workspaces.commit.aio()
    return {
        "exit_code": finished.exit_code,
        "output": finished.output,
        "cut": finished.cut,
        "seconds": finished.seconds,
        "fresh": fresh,
    }


class ModalRunner:
    """The worker's side of `run_command`: the `Runner` the deployed agent gets.

    A command runs in the Function above; the worker's part is to make sure
    the two containers see one workspace. Everything the worker wrote this turn
    is committed before the call, and everything the command wrote is reloaded
    after it, so a file from `write_file` is there for the command and a file
    from the command is there for `read_file`, in the same turn.
    """

    # Facts about the place, and no commands: a recipe in the brief was run
    # verbatim as the first command of every turn (thread `30ed956f`,
    # 2026-09-04), venv or no venv. What is there is said; what to do with it
    # is the model's.
    where = (
        "in a Linux container of its own, through sh, with no secret in it and "
        "your workspace mounted. Installed there: python3 with pip and venv and "
        "the packages reportlab, fpdf2, python-docx, openpyxl, pandas, matplotlib, "
        "Pillow, pypdf, markdown; node and npm; git, curl, zip, unzip, tar, jq, "
        "ffmpeg, imagemagick, poppler (pdftotext, pdftoppm), pandoc; TrueType fonts "
        "with Cyrillic under /usr/share/fonts/truetype (DejaVu, Liberation). The "
        "container is disposable and its home and /tmp are its own: what a command "
        "installs into the container is gone by the next turn; a virtual "
        "environment and node packages made in the task's folder in your workspace "
        "stay, like every file there. `python3` and `pip` are the container's and "
        "nothing in the workspace is activated for you: to install a package, "
        "make a venv in the task's folder and run its python"
    )

    async def run(self, command: str, cwd: Path, timeout: float):
        from app.tools.base import ToolError
        from app.tools.shell import COMMAND_NOT_STARTED, Finished

        root = Path(WORKSPACE_ROOT).resolve()
        try:
            relative = Path(cwd).resolve().relative_to(root)
        except ValueError as error:
            raise ToolError("the workspace is not on the volume", code=COMMAND_NOT_STARTED) from error
        from app.telemetry.trace import spent

        started = time.monotonic()
        with spent("volume_commit", where="before_command"):
            await workspaces.commit.aio()
        with spent("command_remote"):
            result = await run_command.remote.aio(str(relative.as_posix()), command, timeout)
        with spent("volume_reload", where="after_command"):
            await workspaces.reload.aio()
        failure = result.get("failure")
        if failure:
            raise ToolError(str(failure["message"]), code=str(failure["code"]), detail=failure.get("detail"))
        return Finished(
            exit_code=int(result["exit_code"]),
            output=str(result["output"]),
            cut=bool(result["cut"]),
            # The whole wait, not the command's own: the container's start is
            # part of what the person waited for, and the model should see it.
            seconds=time.monotonic() - started,
            fresh=bool(result["fresh"]),
        )


def _settings() -> tuple[object, object]:
    from app.config import AgentSettings, TelegramSettings

    telegram = TelegramSettings()
    agent = AgentSettings()
    if not telegram.token:
        raise RuntimeError("TELEGRAM_TOKEN is not configured")
    if not telegram.webhook_secret:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is not configured")
    if not telegram.allowed and not telegram.open_access:
        raise RuntimeError("no Telegram account is allowed")
    if not agent.database_url:
        raise RuntimeError("AGENT_DATABASE_URL is not configured")
    return telegram, agent


@app.function(
    image=agent_image,
    secrets=[control_secret],
    volumes={WORKSPACE_ROOT: workspaces},
    cpu=1.0,
    memory=2048,
    min_containers=0,
    max_containers=8,
    # 15 s, not the platform floor. The arithmetic here is the opposite of the
    # GPU's: a cold start costs about three seconds of someone's attention,
    # while holding this container costs $0.00026 for the whole window. The
    # expensive resource is the person waiting, not the CPU.
    # 60 s, matching the webhook, so a conversation stays warm as one thing
    # rather than in halves. Measured from the platform's own startup column:
    # a cold worker costs 3.23 s of scheduling plus 1.71 s of first-execution
    # work — imports, graph construction, first connection — for 4.93 s over a
    # warm one, which is the largest CPU number in the chain.
    #
    # This container is dearer to hold than the webhook: a full core with 2 GiB
    # is $0.0000175 a second, so a minute costs $0.00105 against the webhook's
    # $0.00026. At a hundred wakes a day that is about $3 a month, against the
    # roughly $46 of GPU it sits in front of.
    scaledown_window=60,
    timeout=WORKER_TIMEOUT_SECONDS,
    # One re-invocation of the same update after the container dies. A crash
    # is rescheduled by the platform on its own; this covers the kill at
    # `timeout`. The dead worker's heartbeat has stopped, so within a lease
    # the retry claims the row and takes the turn up from its checkpoint.
    retries=1,
    include_source=False,
)
async def process_telegram_update(update_id: int) -> bool:
    """Claim and process one durable update in a separate CPU worker."""

    from app.telemetry import open_telemetry
    from ui.telegram.adapter import TelegramAdapter
    from ui.telegram.api import TelegramClient
    from ui.telegram.inbox import PostgresUpdateInbox
    from ui.telegram.webhook import TelegramUpdateWorker

    telegram, agent = _settings()
    client = TelegramClient(telegram)
    # One recorder for the container, shared by the worker that opens the turn
    # and the adapter that decides how it ended. Its tables are migrated by
    # `tools/setup_control_plane.py`, never by a worker starting up.
    telemetry = open_telemetry(agent)
    # The runner is passed, never defaulted: without it the agent would run
    # commands in this container, beside the secrets.
    inbox = PostgresUpdateInbox(agent.database_url, agent.database_schema)
    # The inbox is also the lane a running turn reads mid-turn messages from.
    adapter = TelegramAdapter(
        client, telegram, agent, telemetry=telemetry, runner=ModalRunner(), inbox=inbox
    )
    # Around the turn, not around the process. A container is reused for as long
    # as its idle window holds, so a container that has been alive since before
    # another one wrote a file would keep serving the version it first saw, and
    # the next message would be answered against a stale workspace. Committing
    # afterwards is what makes this turn's files exist for the next one, whichever
    # container answers it.
    from app.telemetry.trace import TraceEvent, log_event

    def said(kind: str, started: float) -> None:
        # Outside any turn's trace, so the log alone has it (roadmap 18).
        log_event(
            TraceEvent(
                run_id=f"update-{update_id}",
                seq=0,
                type=kind,
                data={"duration_ms": int((time.monotonic() - started) * 1000)},
            )
        )

    started = time.monotonic()
    await workspaces.reload.aio()
    said("volume_reload_before_turn", started)
    try:
        # `_spawn` again, for the same reason the webhook has it: a conversation
        # with more messages than one worker's drain window continues in a fresh
        # container instead of being cut off by this one's timeout.
        return await TelegramUpdateWorker(
            inbox, adapter, telemetry, spawn=_spawn
        ).run(update_id)
    finally:
        started = time.monotonic()
        await workspaces.commit.aio()
        said("volume_commit_after_turn", started)
        await adapter.aclose()
        await client.aclose()
        # Flushes anything a turn left behind before the container can be
        # scaled away with it still in memory.
        started = time.monotonic()
        telemetry.close()
        said("telemetry_closed", started)


@app.function(
    image=render_image,
    # Deliberately empty, and this is the whole point of the function. No
    # `secrets`: no TELEGRAM_TOKEN, no MODEL_API_KEY, no AGENT_DATABASE_URL. No
    # `volumes`: no person's workspace. This is the only place in the deployment
    # where a page's own JavaScript is executed, and Chromium under
    # `--no-sandbox` as root has no isolation of its own — so the container it
    # can reach is one that holds nothing worth reaching.
    #
    # Proxy authentication, so the renderer is not a public URL-fetching service
    # for whoever finds it. The caller is the update worker, which has the token.
    cpu=1.0,
    memory=2048,
    min_containers=0,
    max_containers=4,
    # Short. A person waits on this only while they wait on the whole turn, and
    # unlike the worker there is no conversation to keep warm — but a page often
    # arrives as one of several, so a few seconds of reuse is worth the cent.
    scaledown_window=20,
    timeout=180,
    include_source=False,
)
@modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
async def render_web_page(request: Request):
    """Open one public page in a browser that can reach nothing else here.

    The URL is validated again on this side. The caller already refused to send
    an internal address; this refuses to open one, and neither half assumes the
    other did its job.
    """

    import base64

    from fastapi.responses import JSONResponse

    from app.web import WebError, render_locally

    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        return JSONResponse({"detail": "a JSON object with a url is required"}, status_code=400)
    try:
        rendered = await render_locally(payload["url"], full_page=bool(payload.get("full_page")))
    except WebError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    return JSONResponse(
        {
            "url": rendered.url,
            "title": rendered.title,
            "text": rendered.text,
            "screenshot": base64.b64encode(rendered.screenshot).decode("ascii"),
            "console_errors": list(rendered.console_errors),
            "refused": list(rendered.refused),
        }
    )


@app.function(
    image=agent_image,
    secrets=[control_secret],
    volumes={WORKSPACE_ROOT: workspaces},
    cpu=0.25,
    memory=512,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    timeout=300,
    include_source=False,
)
async def self_test(include_model: bool = False, include_credit: bool = False) -> str:
    """Try every capability here, in the environment the assistant runs in.

    The point is the environment, not the code: the offline suite already covers
    the logic, and every failure this catches — a missing browser, a store that
    breaks on the second call, tables in the wrong schema — is invisible until
    something runs inside a deployed container.

    Free by default. `include_model` adds one completion, which wakes the GPU,
    and `include_credit` adds the probes that spend an outside provider's
    allowance — one search, two Firecrawl credits. Each is a separate decision
    every time it is made.
    """

    from app.agent.runtime import create_agent
    from app.config import AgentSettings
    from ui.telegram.adapter import DELIVERY

    telegram, _ = _settings()
    owner = "deployed-self-test"
    agent = create_agent(
        agent_settings=AgentSettings(), user_id=owner, delivery=DELIVERY, runner=ModalRunner()
    )
    try:
        costs = ("free",)
        if include_credit:
            costs += ("credit",)
        if include_model:
            costs += ("gpu",)
        return await agent.selftest(f"self-test-{owner}", costs)
    finally:
        await agent.aclose()


@app.function(
    image=agent_image,
    secrets=[control_secret],
    volumes={WORKSPACE_ROOT: workspaces},
    cpu=1.0,
    memory=2048,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    # The mini set is eight scenarios of well under a minute each; the wider
    # letters a few minutes. Half an hour holds either.
    timeout=1800,
    include_source=False,
)
async def scenarios(letters: str = "ABCFWHEM", model: str = "") -> tuple[str, int, list[dict]]:
    """Run `scripts/loop_live.py`'s scenarios here, in the worker's own environment.

    The same image, secrets, Volume and command runner the worker has, so what
    passes here is what passes for a person — the local run answers a different
    question (the human's point, 2026-09-04). A probe user's workspace on the
    Volume, the deployed telemetry with ids of this invocation's own. Returns
    the printed report, the number of failed checks and one row per scenario
    for the side-by-side table. Every turn is a paid model call: a
    product-runtime worker, permission each time.

    `model` names a model set for this run only (`MODEL=<set>`), so a set that
    is not the deployment's own — Gemma on its GPU App, a fine-tuned one —
    can be measured on the same scenarios (roadmap 24). The run ids carry
    the set's name, because a run row does not.
    """

    import contextlib
    import io
    import os
    import uuid
    from dataclasses import asdict

    if model:
        os.environ["MODEL"] = model

    from app.agent.interjections import MemoryInterjections
    from app.agent.runtime import create_agent
    from app.agent.stop import MemoryStopRequests
    from app.config import AgentSettings
    from app.telemetry import open_telemetry
    from scripts.loop_live import run_scenarios
    from ui.telegram.adapter import DELIVERY

    _settings()
    agent_settings = AgentSettings()
    telemetry = open_telemetry(agent_settings)
    stops = MemoryStopRequests()
    lane = MemoryInterjections()

    def factory():
        return create_agent(
            agent_settings=agent_settings,
            user_id="loop-live-check",
            delivery=DELIVERY,
            telemetry=telemetry,
            stops=stops,
            interjections=lane,
            runner=ModalRunner(),
        )

    out = io.StringIO()
    await workspaces.reload.aio()
    try:
        with contextlib.redirect_stdout(out):
            failed, rows = await run_scenarios(
                frozenset(letters.upper()),
                factory(),
                telemetry,
                factory,
                prefix=f"deployed-{model + '-' if model else ''}{uuid.uuid4().hex[:8]}-",
            )
    finally:
        await workspaces.commit.aio()
    return out.getvalue(), failed, [asdict(row) for row in rows]


@app.function(
    image=control_image,
    secrets=[control_secret],
    cpu=0.25,
    memory=512,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    timeout=120,
    include_source=False,
)
def measure_database_latency(
    operation: str, warm_runs: int = 5, database: str = "primary"
) -> dict[str, object]:
    """Measure one complete production read or write without touching the model.

    ``prepare`` creates a representative isolated read fixture. A later
    ``read`` or ``write`` invocation must happen only after Neon is known to be
    idle if its first sample is to count as database-cold acceptance.

    ``database`` selects between the deployed database and a second one
    configured as ``AGENT_ALT_DATABASE_URL``. ``compare`` measures the warm read
    against both **inside one invocation**, which is the only way to get an
    honest answer: placement is unpinned, so two separate calls can run in two
    regions and the difference between them would not be the difference between
    the databases. A DSN is never passed as an argument — it would then appear
    in the platform's own call records — so the choice is a name and the values
    stay in the secret.
    """

    import os
    import time
    import uuid

    from app.config import AgentSettings
    from app.context import load_turn_context
    from app.context.window import DEFAULT_SYSTEM_PROMPT
    from app.memory import ConversationStore, open_store
    from app.models import ContentPart, Message

    if operation not in {"prepare", "read", "write", "compare"}:
        raise ValueError("operation must be prepare, read, write or compare")
    if not 1 <= warm_runs <= 20:
        raise ValueError("warm_runs must be between 1 and 20")
    if database not in {"primary", "alternate"}:
        raise ValueError("database must be primary or alternate")

    configured = AgentSettings()

    def profile(name: str) -> AgentSettings:
        url = configured.database_url if name == "primary" else configured.alt_database_url
        if not url:
            key = "AGENT_DATABASE_URL" if name == "primary" else "AGENT_ALT_DATABASE_URL"
            raise RuntimeError(f"{key} is not configured")
        return configured.model_copy(update={"database_url": url})

    settings = profile(database)

    owner = "database-latency-benchmark-v1"
    fixture = "database-latency-read-fixture-v1"

    def message(role: str, value: str) -> Message:
        return Message(role=role, content=[ContentPart(kind="text", text=value)])

    if operation == "prepare":
        store = open_store(settings)
        try:
            existing_owner = store.thread_owner(fixture)
            if existing_owner not in {None, owner}:
                raise RuntimeError("latency fixture id belongs to another owner")
            if store.message_count(fixture) == 0:
                turns = []
                for index in range(4):
                    turns.extend(
                        [
                            message("user", f"latencyfixture question {index}"),
                            message("assistant", f"latencyfixture answer {index}"),
                        ]
                    )
                store.append(fixture, turns, owner)
                store.set_summary(fixture, "Representative earlier conversation.", 0)
            saved = set(store.facts(owner))
            for index in range(5):
                fact = f"latencyfixture durable fact {index}"
                if fact not in saved:
                    store.remember(fact, owner, fixture)
            return {"operation": operation, "fixture": "ready"}
        finally:
            store.close()

    query = "latencyfixture current question"

    def read_once(store: ConversationStore) -> dict[str, int]:
        context = load_turn_context(
            store, fixture, owner, query, 5, DEFAULT_SYSTEM_PROMPT
        )
        shape = {"history": len(context.history), "prelude": len(context.prelude)}
        if shape != {"history": 8, "prelude": 3}:
            raise RuntimeError("representative read fixture is absent or incomplete")
        return shape

    created_threads: list[str] = []

    def write_once(store: ConversationStore) -> dict[str, int]:
        thread_id = f"database-latency-write-{uuid.uuid4().hex}"
        created_threads.append(thread_id)
        count = store.append(
            thread_id,
            [message("user", "benchmark question"), message("assistant", "benchmark answer")],
            owner,
        )
        return {"messages": count}

    def sample(chosen: AgentSettings, run_once) -> dict[str, object]:
        started = time.perf_counter()
        store = open_store(chosen)
        try:
            shape = run_once(store)
            cold_ms = (time.perf_counter() - started) * 1000
            warm_ms = []
            for _ in range(warm_runs):
                warm_started = time.perf_counter()
                shape = run_once(store)
                warm_ms.append((time.perf_counter() - warm_started) * 1000)
        finally:
            for thread_id in created_threads:
                store.delete_thread(thread_id)
            created_threads.clear()
            store.close()
        return {
            "cold_ms": round(cold_ms, 3),
            "warm_ms": [round(value, 3) for value in warm_ms],
            "warm_max_ms": round(max(warm_ms), 3),
            "warm_min_ms": round(min(warm_ms), 3),
            "shape": shape,
        }

    if operation == "compare":
        # One container, one region, back to back. The only thing that differs
        # between the two results is where the database is.
        return {
            "operation": operation,
            "region": os.environ.get("MODAL_REGION", "unknown"),
            "primary": sample(profile("primary"), read_once),
            "alternate": sample(profile("alternate"), read_once),
        }

    run_once = read_once if operation == "read" else write_once
    started = time.perf_counter()
    store = open_store(settings)
    try:
        shape = run_once(store)
        cold_ms = (time.perf_counter() - started) * 1000
        warm_ms = []
        for _ in range(warm_runs):
            warm_started = time.perf_counter()
            shape = run_once(store)
            warm_ms.append((time.perf_counter() - warm_started) * 1000)
        return {
            "operation": operation,
            "database": database,
            "cold_ms": round(cold_ms, 3),
            "warm_ms": [round(value, 3) for value in warm_ms],
            "warm_max_ms": round(max(warm_ms), 3),
            "cold_pass": cold_ms <= 500,
            "warm_pass": max(warm_ms) <= 100,
            "shape": shape,
            "region": os.environ.get("MODAL_REGION", "unknown"),
        }
    finally:
        for thread_id in created_threads:
            store.delete_thread(thread_id)
        store.close()


_waker: object | None = None


async def _warm() -> bool:
    """Start the model waking while the update worker is still being scheduled.

    The webhook decides *whether* to call this — it knows whether the update
    needs a model at all — and this decides *how*, which is the platform's
    business and not the transport's.

    The backend is kept for the life of the container so that a second message
    reuses the connection instead of paying another TLS handshake to wake a
    model that is already awake. If the client is ever unusable — a different
    event loop, a closed pool — `warm` returns False and the turn is the one we
    had before this existed, which is why nothing here re-raises.
    """

    global _waker

    from app.config import ModelSettings
    from app.models.openai_compatible import OpenAICompatibleBackend

    if _waker is None:
        _waker = OpenAICompatibleBackend(ModelSettings())
    return await _waker.warm()


async def _spawn(update_id: int) -> None:
    # ``spawn`` returns immediately and the durable inbox owns the retry state.
    # The async form matters here rather than being a style preference: the
    # blocking one is a synchronous RPC to Modal's control plane made from
    # inside the event loop, and it stalled the webhook that Telegram is
    # waiting on. Modal's own runtime warned about it in production logs.
    await process_telegram_update.spawn.aio(update_id)


@app.function(
    image=control_image,
    secrets=[control_secret],
    cpu=0.25,
    memory=512,
    min_containers=0,
    max_containers=20,
    # 60 s, and the reasoning is the opposite of the GPU's. What is left of this
    # function's cold start is ~3.5 s of Modal scheduling that no code removes —
    # measured, after both the imports and a memory snapshot were tried against
    # it. The only remaining lever is not being cold, and here that is nearly
    # free: a quarter-core with 512 MiB costs $0.0000044 a second, so holding the
    # window open for a full minute after the last update costs $0.00026. A
    # hundred wakes a day is under a dollar a month.
    #
    # A minute is chosen against how people actually type: it covers reading a
    # reply and answering it, which is the case that was paying 3.5 s at 15 s.
    scaledown_window=60,
    timeout=30,
    include_source=False,
    # No `enable_memory_snapshot`. It was tried here and measured over nine cold
    # starts, and the numbers said to take it out:
    #
    #     before snapshots      mean 5.36 s   execution 1.67-3.86 s
    #     snapshot creation     mean 8.56 s   6 of the 9 cold starts
    #     snapshot restore      mean 4.06 s   execution 0.34-0.46 s
    #
    # A restore is only about a second better than no snapshot, and subtracting
    # execution shows why: the container itself still takes ~3.5 s either way.
    # Restoring skips the initialization, not the scheduling — and the
    # initialization had already been removed by keeping the agent stack out of
    # this function's imports. The two are substitutes, and the free one won.
    # What is left is spent creating snapshots that a later cold start may not
    # even land on, because a snapshot only restores onto the worker type that
    # made it and placement here is unpinned.
    #
    # Reconsider only for a function with real init to capture. This one has none
    # left, which is the point.
)
@modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=False)
async def telegram_webhook(request: Request):
    """Translate one HTTP request into the transport-neutral webhook core."""

    from fastapi.responses import JSONResponse

    from ui.telegram.inbox import PostgresUpdateInbox
    from ui.telegram.webhook import TelegramWebhook

    telegram, agent = _settings()
    inbox = PostgresUpdateInbox(agent.database_url, agent.database_schema)
    accepted = await TelegramWebhook(telegram, inbox, _spawn, warm=_warm).accept(
        request.headers,
        await request.body(),
    )
    return JSONResponse({"detail": accepted.detail}, status_code=accepted.status)
