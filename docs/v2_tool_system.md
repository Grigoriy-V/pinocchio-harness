# V2 Tool System

**Status:** the architecture for roadmap step 4.5, finalized 2026-09-03 from the
draft in `reports/archive/v2_tool_system_design.md` after reading the tool layers of
DeepSeek Harness, Hermes Agent and OpenClaw, and implemented the same day
(`reports/2026-09-03_v2_tool_system_implementation.md`). This is the document
the implementation follows; the draft is kept as the record of where it came
from. Where the code and this text differ, `docs/PROJECT_MAP.md` is current.
The comparison, the options that were not taken and the reasons are in
`reports/2026-09-03_v2_tool_system_references_and_queue.md`. The durable
choices are in `DECISIONS.md` 2026-09-03. `ROADMAP.md` alone says when any of
this is built.

## What is being replaced, and why

The tool layer is the one part of the loop still carrying Version 1 shapes. A
`Tool` is a name, a schema and a callable returning text; a failure is a string
that starts with `error: `; every consumer that needs to know whether a call
failed — the loop's repeat guard, the plan reader, telemetry, both interfaces,
`/check` — reads that prefix. An unexpected exception is turned into
`"<tool> failed: <detail>"` with the platform's own wording, and a call the
served parser mangled used to end the request with a `BackendError` rather than
a result the model could read.

Every live failure of the last week had one of these under it: ISS-0005 (an OS
error unwrapped), ISS-0006 (a directory made as a file), ISS-0007 (a
`tool_failed` event with no reason), and the shape of ISS-0001 (a corrupted call
that the loop could not recognise as corrupted). The context engine planned in
4.6a would shorten tool results, and it cannot be built on prose it has to
parse. So the boundary is replaced first, and once.

Three things are **not** changing. The loop in `app/agent/graph.py` keeps its
four nodes and its `pre_execute -> execute -> post_execute` seam in
`app/tools/execution.py`. Capabilities and grants in `app/tools/capabilities.py`
keep their names and their root. The product rules in `docs/PRODUCT.md` —
observation is not presentation, the workspace root is the boundary, tool
output is untrusted data — are what the new contract enforces, not what it
revisits.

## The contract

### A tool

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]          # the JSON-schema subset the toolbox validates
    run: Callable[..., ToolReturn]      # str | Sequence[ContentPart], sync or async
    requires_approval: bool = False     # was `destructive`; the boundary crossed, not bytes changed
    timeout_seconds: float | None = None
    replay_safe: bool = False           # 4.7: may be run again when nobody knows whether it ran
```

`replay_safe` (2026-09-04) is read only when a worker died while a step's
tools were running: a reading tool is run again by the next worker, anything
else is answered `interrupted` and the model decides. The reading tools set
it; `write_file`, `edit_file`, `send_file`, `remember_fact` and the todo tool
do not.

A tool **returns content on success and raises `ToolError` on failure**. It
never builds a failure by hand and never returns one as text. What it returns
is what the model will read, and what it raises is what the executor will type.

```python
class ToolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "failed", detail: str | None = None): ...
```

`timeout_seconds` is the one new field and it exists because of a real gap: a
tool that hangs today hangs past the turn's own `max_seconds`, which is only
read at step boundaries. The executor enforces it with `asyncio.wait_for`; the
tool need not know. `requires_approval` is the old `destructive` under the name
that says what it does; the rename lands with the migration.

### An outcome

```python
@dataclass(frozen=True)
class ToolFailure:
    code: str            # stable, lower_snake, family-prefixed for family errors
    message: str         # one sentence the model can act on
    detail: str | None   # the sanitized diagnostic, when it adds something

@dataclass(frozen=True)
class ToolOutcome:
    content: tuple[ContentPart, ...]
    failure: ToolFailure | None = None
```

`failure is None` means the operation succeeded. Nothing else does. The model
projection, telemetry, the loop's guards, the plan reader and both interfaces
ask this field and never a string.

**Failure is not the same as an unwanted result.** A shell command that exits
`1` is a successful `run` whose content says `exit_code: 1`; a page that renders
with console errors is a successful `inspect_page` whose content lists them.
`ToolFailure` is for the tool not doing what was asked — the file could not be
written, the URL was refused, the browser is not installed — because that is
the distinction the harness has to make and the model cannot: an infrastructure
failure is the runtime's news, an application failure is the model's evidence.

### Codes

Codes are for the runtime, telemetry and the future context engine; the model
reads `message`. A family owns its codes and adds one only when something has
to branch on it. The runtime's own:

```text
unknown_tool      the name matched nothing the toolbox has
bad_arguments     the arguments failed the schema, or could not be read as an object
declined          the person answered no to an approval
not_run           the loop halted the call: budget, stop, or a repeating failure
timeout           the executor's deadline passed
internal          an exception the tool did not expect; the traceback is in the log
failed            a ToolError that named no code
```

Families start with what the current tools already distinguish in words:
`fs.outside_root`, `fs.not_found`, `fs.not_a_file`, `fs.not_a_directory`,
`fs.is_directory`, `fs.blocked_by_file`, `fs.ambiguous_edit`, `fs.too_large`,
`fs.io`; `doc.unsupported`, `doc.unreadable`; `browser.unavailable`,
`browser.load_failed`; `web.refused`, `web.unreachable`, `web.too_large`,
`web.no_provider`; `memory.invalid`; `todo.invalid`. This is the starting list,
not an enum to complete in advance.

### The result the model sees

The executor projects an outcome into the tool `Message`:

- success: the content, as it is;
- failure: one text part, `error: <message>` and `(<detail>)` when there is
  detail, plus the tool's one-line signature after a `bad_arguments`.

The leading word is wording the model responds to well and stays. It is no
longer a protocol: nothing in the runtime reads it back.

The typed failure rides on the message itself:

```python
@dataclass(frozen=True)
class Message:
    role: Role
    content: Sequence[ContentPart] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    failure: ToolFailure | None = None   # tool messages only
```

`tool_failed(message)` becomes `message.failure is not None`. The field is part
of the turn's checkpointed state (`ToolFailure` joins `CHECKPOINT_TYPES`), so
the repeat guard and the plan reader see it across an interrupt and a resume.
**It is not stored in conversation history yet.** The store's message row has
no column for it, a column is a `user_version` bump, and the roadmap holds one
schema-3 migration for 4.6a. Until then stored history carries the text
projection, which is all the model ever read from it anyway; the failure column
joins the 4.6a migration, where the context engine is the first reader that
needs it.

## The executor

`ToolExecutor` in `app/tools/execution.py` keeps its three stages and takes
ownership of everything that happens between the model's call and the model's
result. The loop still decides when a batch runs, pauses or is halted, and
passes the halted calls through the same projection (`not_run` with the reason
the loop already writes).

```text
pre_execute(call)
    resolve the name            exact; else the allowlisted normalisations below
    read the arguments          a dict, or bad_arguments — never a BackendError
    coerce to the schema        "42" -> 42, "true" -> true, "x" -> ["x"], a JSON string -> its object
    validate against the schema unchanged
    approval policy             unchanged
execute(prepared)
    the tool body, under timeout_seconds
    ToolError            -> ToolFailure(code, message, detail)
    Exception            -> ToolFailure("internal", "<tool> failed: <type>", sanitized message)
                            and the full traceback to the logger, never to the model
    BaseException        -> propagates: cancellation and shutdown are not tool failures
post_execute(outcome)
    bound the content           text head + marker + tail past the cap; image count and bytes capped
    sanitize failure text       no code fence, no role or tool-call token, first line, bounded length
    record                      tool_finished, or tool_failed with code and message
    project                     the Message above
```

Every stage that refuses produces an outcome, and the outcome is what reaches
the model. The only exception is deliberate: `BaseException` — `CancelledError`,
`KeyboardInterrupt`, interpreter shutdown — is not caught, because a stop must
be able to stop.

### Bounds at the seam

A tool result goes straight into the next request and the model cannot decline
what it has already been given. Each tool already caps its own output; the
executor adds the backstop that does not depend on a tool remembering to: a
text cap per result, applied as head, a `[... N characters omitted ...]`
marker and tail so that both the beginning and the end survive; a cap on the
number and total bytes of image parts. The per-turn aggregate — many
medium-sized results adding up — and spilling a full result to a file the
model can read later are context-engine work and stay in 4.6a.

### Sanitizing failure text

A failure message is the one thing the model is most likely to imitate: live on
2026-08-31 the model copied its own malformed call three times, and a markdown
fence inside a value is what broke the served parser in the first place. So a
failure's text never contains a fence, a role tag, the served model's string
delimiter or a tool-call token; it is the first line of the diagnostic, bounded;
and a path in it is the path the model supplied, never a resolved absolute
one. The full exception stays in the process log for the developer.

## The model boundary: any model, any server

The parser fix in `tools/gemma4_parser.py` stays offline and is not deployed.
It is a correction for one model's parser on one server; the product runs on
whatever OpenAI-compatible endpoint is configured, and the runtime has to
survive what any of them emits. Two layers, each bounded, each allowlisted, and
neither inventing a value:

**In the model adapter** (`app/models/openai_compatible.py`), about wire
shape and knowing nothing about tools:

- streamed fragments are assembled by identity, not only by position, so a
  second call is never appended to the first (done, 2026-08-31);
- a call whose arguments are not a JSON object is delivered with its raw text
  kept, and the executor refuses it as `bad_arguments`. Today `parse_arguments`
  raises `BackendError` and the whole request fails; a model that emitted one
  bad call must instead get one bad result and keep its turn;
- a call carrying fragments of another call — a name that cannot be a
  parameter name, a value ending in `,name:` for an argument that is then
  absent — has those fragments removed and nothing added (`readable`, done).

**In the executor**, where the schema is known:

- name resolution: exact match first; otherwise, against the allowlist only,
  case-insensitive with a `functions.` / `tools.` / `call:` prefix stripped,
  and the legacy names the roadmap has renamed. A name that resolves to nothing
  is `unknown_tool` with the available names;
- coercion to the declared type, which open-weight models get wrong in the
  same few ways: numbers and booleans as strings, a scalar where an array is
  declared, an object or array serialized as a string. A value that cannot be
  coerced is left alone for validation to name;
- a refused call answers with the tool's signature, and a `write_file` that
  lost its path is told to send `path` first (done).

What is deliberately not done: **promoting a tool call the model wrote as
plain text** into a structured call. OpenClaw does it with an allowlist and a
byte cap; it is the next lever if a server ever hands us a call as prose, and
nothing observed so far has. It is recorded in the report as the option it is.
Guided decoding on the server is likewise not a runtime concern and not
planned.

The loop's own guard is unchanged: a call that has failed twice identically is
refused a third time and the turn is asked to answer. DeepSeek's advisory
reminder at 3, 5 and 8 repeats — a nudge, never a block — is the shape to
consider in 4.7 if a live suite shows the hard stop ending turns that would
have recovered.

## Portability without a provider hierarchy

One implementation of each capability, parameterized by what actually differs.
Filesystem tools take a root; that is the whole difference between a Windows
workspace, a Linux one and the mounted Modal volume, so there is one
`filesystem.py` and no `Filesystem` protocol. The environment facts the model
can use — local or deployed, which browser, whether isolated execution exists —
are stated once in the capability brief and never repeated in results.

A second interface arrives with the second real implementation. That is item 5:
a sandbox is an `execute` backend behind the same executor, with its own root,
holding no control-plane secret. Hermes's `BaseEnvironment` (one `terminal`
tool over local, Docker, SSH, Modal) and DeepSeek's `ctx.fs` seam (one contract,
`fs-local` / `fs-sandbox` / `fs-e2b` behind it) are the references for that
step, and this step lays no groundwork for them beyond the typed outcome they
would return through.

## Shell: one tool, a runner behind it (5b and 5, 2026-09-04)

`run_command(command, timeout_seconds=120)` runs one shell command in the
workspace and returns the exit code, the elapsed time and the output, cut in
the middle above 16k characters with both ends kept. A non-zero exit is a
result. The family's codes: `shell.timeout` (killed at its deadline, the
tail of what it printed as detail) and `shell.not_started`. Where it runs is
a `Runner` with one method, chosen by the profile and owned by the
`CapabilityRegistry`: `LocalRunner` is a process on the person's machine in
the workspace, with the environment reduced to what a shell needs, `HOME` in
the workspace, and nothing of the agent's own environment passed on; the
deployed runner is `ModalRunner` (`deploy/modal/control_app.py`), which
calls the `run_command` Function — the worker's layers plus base tools, the
workspaces Volume, no secret, 180 s scaledown — where a `ContainerRunner`
runs the command as a plain process; the worker commits the Volume before
the call and reloads after it, the Function reloads before the command and
commits after, and the first command in a fresh container says `new
environment`. Locally the workspace's `.venv` is made on first use; in the
container none is, and the runner's `where` tells the model to make one for
a project's packages. When `.venv` exists it is first on `PATH`, so `python`
and `pip` are the project's. On Windows the command runs under a
write-restricted token (`app/tools/shell_windows.py`): the operating system
refuses every write outside the workspace and its temp, a `pip install` into
the machine's Python included (ISS-0038); reading and the network are
untouched, and a place Everyone may write to stays writable, as in DeepSeek
Harness. The brief says where commands run and whether that boundary holds,
in the runner's own words, and that the workspace is what survives between
turns. `DECISIONS.md` 2026-09-04.

**Two modes.** A tool declares `mutates` when it changes the workspace
(`write_file`, `edit_file`, `run_command`). In `full`, the default, that
changes nothing. In `careful` (`/mode careful`, a marker in the workspace
read by the next toolbox) the toolbox's `requires_approval` is true for them
too, so they go through the same approval path as an outbound effect. Nothing
else knows the mode exists.

## Filesystem

The current tools keep their names and arguments. What changes:

- every failure is a `fs.*` code with a message in the words a person would
  use, the `strerror` as detail, and nothing platform-specific in the message;
- `write_file` becomes atomic — temp file, fsync, replace — the way `edit_file`
  already is, so an interrupted worker cannot leave a half-written artifact;
- the trailing-separator refusal and the blocked-ancestor message (ISS-0006)
  stay and get their codes;
- the same test file runs the same assertions on Windows, on Linux and, through
  `/check`, in the deployed profile.

The read-before-edit policy DeepSeek attaches as a plugin is not adopted: with
one person and one worker per conversation, the version race it guards against
does not exist here. The unique-match rule on `edit_file` is the ambiguity
guard that matters, and it keeps its code.

## Browser: designed whole, exposed in part

Roadmap 4.5.5, not this step, but the boundary is fixed here so 4.5 does not
have to be redone for it. Implemented 2026-09-03
(`reports/2026-09-03_v2_browser_session.md`); `docs/PROJECT_MAP.md` is
current where the two differ.

The internal contract is one `BrowserSession` in `app/tools/chromium.py`,
designed for the full set of operations even though the first version exposes
only observation:

```text
open(document | url)      snapshot(max_chars, query?)   screenshot(full_page?)
navigate(url)             evaluate(expression)          console()
click(ref)  type(ref, text)  press(key)  select(ref, value)
close()
```

A **snapshot** is the page as text — a bounded accessibility tree whose
interactive elements carry stable `ref`s — where a screenshot is pixels.
Actions take refs, not CSS selectors, because a ref is what the model was just
shown and a selector is a guess. That is the pattern OpenClaw settled on and
the one Hermes's browser tool converged to, and it is why the session is
designed with refs from the start: adding `click` later must not change what
`snapshot` returns.

The first version re-implements `inspect_page` on that session and adds the
snapshot to what it returns: structure with refs, visible text, console errors,
screenshot. No click, type or navigate tool is exposed, on purpose: the product
question those answer — judging a generated page by using it, ISS-0008 — is
4.5.5's, after the tool contract they would return through exists.

The trust boundary is unchanged and is a property of the session, not of the
tool: a local artifact renders where the agent runs with every network scheme
blocked; an internet page is rendered by the isolated function with no secret,
database URL or user volume. Both drive the same session API.

## Interfaces and telemetry

- `tool_failed` carries `code` and `message` (ISS-0007 closes by construction).
  `tools/show_run.py` prints them.
- The Telegram activity line and the Chainlit step ask `message.failure`;
  neither parses text. No per-tool presentation metadata is added: the label
  table in the adapter stays where it is until a second interface needs it.
- `/check` runs the tools through the executor exactly as the model does and
  reads the outcome, not the prefix.
- Stored history is read with the text projection until the failure column
  lands in 4.6a.

## Migration

One bounded implementation, in this order, each step green on the offline suite
before the next:

1. **Types and executor.** `ToolFailure`, `ToolOutcome`, `ToolError(code,
   detail)`, `Message.failure`, the checkpoint type, executor normalization,
   bounds, sanitizing, timeout, projection, telemetry reason. Every consumer of
   the prefix — `graph.failed_before`, `todo.current`, `preflight`, the Chainlit
   adapter and history, the Telegram adapter, `tests/` — switches to the field.
2. **Model boundary.** Invalid arguments become a refusal, not a
   `BackendError`; name resolution and schema coercion in `pre_execute`; the
   fragment removal stays where it is.
3. **Filesystem.** Codes, atomic write, one test file for both profiles.
4. **Documents, presentation, memory, todo, web.** Codes; no behaviour change.
5. **Remove `ERROR_PREFIX`** as an exported name once nothing imports it. The
   wording stays in the projection.

Step 4.5.5 then builds `BrowserSession` and moves `inspect_page` onto it.

## Acceptance for 4.5

- The offline suite passes, and there is one test per family showing its
  failures arrive as `ToolFailure` with the family's codes.
- A call with unreadable arguments — the ISS-0001 shape, replayed from the
  fixture — produces a `bad_arguments` result with the signature, no
  `BackendError`, and the turn continues.
- An exception a tool did not expect produces an `internal` failure the model
  can read, the traceback in the log, and a turn that goes on.
- A tool that exceeds its `timeout_seconds` produces `timeout` and the turn
  goes on.
- `tools/show_run.py` on a run with a failed tool shows the code and message.
- `/check` in the deployed profile reports the same filesystem semantics as the
  local run. That is a product-runtime worker and its own gate.

## What this document does not decide

- The per-tool timeout values, the text cap and the image caps: set in the
  implementation from the current tools' own limits and measured, not chosen
  here.
- The exact list of legacy name aliases: the roadmap's renames, read at the
  time.
- Anything about compaction, spill or an aggregate budget: 4.6a.
- The model-facing shape of browser actions: 4.5.5, from the session API
  above.
- Whether `ask_user` is a tool or an interrupt kind: 4.8, on this contract.
