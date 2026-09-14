# V2 Tool System — design proposal

**Status:** draft design proposal, 2026-08-31. This document is not a roadmap,
implementation authorization, or an approved durable decision. `ROADMAP.md`
remains the only source for current work order and approval.

## Why this exists

The V2 agent loop is already new, but the tool layer still carries several
assumptions from the earlier implementation. The current `Tool` contract is a
name, JSON schema and callable returning text or `ContentPart`; a failed result
is identified by the textual prefix `error:`. `ToolExecutor` already provides a
single `pre_execute -> execute -> post_execute` lifecycle for validation,
consent, execution and telemetry.

Recent live work exposed the cost of leaving the boundary this loose:

- environment and filesystem errors can reach the model in forms that are hard
  to act on;
- a tool failure is partly a string convention rather than a typed result;
- local and deployed profiles need the same model-facing capabilities even
  though their runtime details differ;
- later context compaction and recovery will need to distinguish useful tool
  output from diagnostics without parsing arbitrary prose;
- future browser and isolated-execution capabilities would otherwise add more
  one-off behaviour to the same boundary.

The goal is not to build a general tool framework. The goal is the smallest
replacement contract that makes the current system stable, portable and ready
for the next V2 steps.

## Product constraints

The design must preserve the product contract in `docs/PRODUCT.md`:

- the model remains the agent;
- infrastructure exposes truthful capabilities and evidence rather than hidden
  workflows;
- failures and uncertainty stay visible to the model so it can recover or
  report them honestly;
- local and deployed profiles remain the same product;
- interfaces stay thin;
- observation is not presentation;
- policy and safety remain application boundaries rather than prompt habits.

## Design principles

### 1. One model-facing tool contract across environments

A capability should keep the same name, arguments and semantics whether the
application is running locally or on Modal.

```text
write_file(path, content)
read_file(path)
inspect_page(path)
```

The environment may change how the operation is implemented, but it should not
create a second product API such as `modal_write_file` or
`windows_write_file`.

### 2. The model may know the real environment

Portability does not mean hiding the runtime from the agent. Environment facts
can matter to reasoning and error recovery.

Useful capability context can include facts such as:

```text
runtime: local | Modal
os: Windows | Linux
workspace: persistent
browser: available
isolated execution: unavailable
```

These facts should be stated once in runtime/capability context when useful, not
repeated in every tool result. A result includes environment detail only when it
matters to that result or failure.

### 3. Keep the existing execution seam

The V2 loop should not be rewritten again for this work.

```text
model ToolCall
    |
    v
pre_execute
    - resolve tool
    - validate arguments
    - apply approval policy
    |
    v
execute
    - call the tool implementation
    - convert known and unexpected failures
    |
    v
post_execute
    - telemetry
    - model-visible Message projection
```

`ToolExecutor` is already the right ownership boundary. The redesign replaces
what flows through that seam rather than introducing a second lifecycle.

### 4. Typed outcome, not a string protocol

The current `error:` prefix should stop being the internal truth about whether a
tool failed.

The first version needs only a small canonical result:

```python
@dataclass(frozen=True)
class ToolOutcome:
    content: tuple[ContentPart, ...]
    error: ToolFailure | None = None


@dataclass(frozen=True)
class ToolFailure:
    code: str | None
    message: str
    detail: str | None = None
```

`error is None` means the tool operation succeeded. This is enough for the model
projection, telemetry, UI settlement and the future context engine without
building a large result framework now.

Do not add speculative fields such as generic provider metadata, artifacts,
retry plans, cost objects or presentation policies until a real consumer needs
them.

### 5. Stable error classification plus faithful diagnostics

The model needs both semantic classification and the real reason an operation
failed.

Example:

```text
code: FS_PERMISSION_DENIED
message: Could not write "project/config.json".
detail: [Errno 13] Permission denied: '/workspace/project/config.json'
```

The code is useful to infrastructure and later context handling. The message and
sanitized detail are useful to the agent.

Error codes should be owned by the relevant capability and added only when the
system needs to distinguish them. Do not create a large global taxonomy in
advance.

Initial examples may include:

```text
runtime
  UNKNOWN_TOOL
  BAD_ARGUMENTS
  INTERNAL_TOOL_ERROR

filesystem
  FS_NOT_FOUND
  FS_NOT_DIRECTORY
  FS_PERMISSION_DENIED
  FS_IO_ERROR

browser
  BROWSER_UNAVAILABLE
  PAGE_LOAD_FAILED
```

This list is illustrative, not an approved enum.

### 6. Expected operation failure is not infrastructure failure

A tool can execute successfully and report that the operation it performed did
not achieve a zero/error-free application result.

For future shell execution:

```text
process launched successfully
exit_code: 1
stderr: Traceback ...
```

is a successful execution-tool call carrying a non-zero process result. It is
not the same as:

```text
failed to start the execution backend
sandbox unavailable
connection to executor lost
```

This distinction prevents the harness from treating normal agent-observable
failures as broken infrastructure.

### 7. The runtime reports; the model decides recovery

The runtime should make failures understandable, but it should not silently act
as another agent.

It may provide:

- what failed;
- a stable category when known;
- the faithful sanitized diagnostic;
- narrowly factual help when the tool contract itself knows the correction,
  such as the accepted argument shape.

It should not automatically choose a new path, select another tool, re-plan the
work, or repeatedly retry semantic failures. The next action remains the
model's decision.

### 8. Unexpected exceptions should not casually kill the turn

Known tool-domain errors should become `ToolFailure` directly. Unexpected
ordinary exceptions crossing the tool boundary should be handled separately:

```text
known tool error
  -> typed model-visible failure

unexpected Exception
  -> full exception and traceback in developer telemetry/logs
  -> INTERNAL_TOOL_ERROR with a faithful sanitized diagnostic for the model
```

Do not catch `BaseException`; cancellation, shutdown and similar control signals
must still be able to escape normally.

Sensitive implementation data, credentials and internal-only paths must not be
leaked merely to expose a raw traceback. The model gets the useful cause;
developer diagnostics retain the full exception chain.

## Portability without premature backend frameworks

The application should abstract a backend only when there is a genuinely
different backend.

Running the same filesystem code on a local Linux process and a Modal Linux
worker does not by itself require two filesystem providers. If both expose an
ordinary mounted filesystem, a single implementation parameterized by an
allowed root is preferable.

```text
Filesystem(root)
    -> Windows local filesystem
    -> Linux local filesystem
    -> mounted Modal workspace
```

A separate interface becomes justified when semantics really differ, for
example a future remote isolated sandbox whose files are reached through an API
rather than normal local I/O.

The same rule applies to other capabilities: introduce dependency injection or a
small `Protocol` at the first real second implementation, not because a generic
Service/Provider architecture looks complete on paper.

### Explicit non-goal

Do not introduce, at this stage:

```text
ToolRuntime
  -> ServiceRegistry
  -> ServiceDefinition
  -> ProviderRegistry
  -> Provider
  -> Adapter
```

for every tool family. Deep modular frameworks are useful once several real
implementations exist; building one first would add indirection without solving
a current product problem.

## Filesystem direction

The existing filesystem tools should keep model-facing semantics independent of
host OS path quirks as far as the workspace contract allows.

Required properties:

- every model-supplied path remains bounded by an explicit allowed root;
- path errors are converted into readable capability errors rather than raw
  platform-only codes;
- local and deployed profiles use the same tool names and expected behaviour;
- raw OS details may appear in sanitized diagnostics when they help the model;
- file writes should move toward atomic replacement where practical so worker
  interruption cannot leave a partially written artifact.

Atomic mutation is more valuable at this stage than a generic filesystem
provider hierarchy.

## Browser direction

The browser should be designed so later interaction does not require replacing
the capability boundary, but the first migration should remain deliberately
small.

### Current need

The agent needs a real browser observation of a generated local HTML artifact:

```text
open/render
visible text / basic structure
console errors
screenshot
```

The current `inspect_page` already provides this product behaviour and should
not be expanded into a full browser-control project merely for architectural
symmetry.

### Future capability shape

The eventual browser capability may need operations such as:

```text
open / navigate
snapshot
screenshot
click
type / select / press
evaluate
close
```

The internal Chromium boundary should therefore avoid assumptions that make
those operations impossible later.

### First implementation boundary

For the tool-system migration, exposing only the read-only inspection basis is
enough. No browser session manager, public `session_id`, click/type/evaluate API
or interactive verification workflow is required yet.

A future interactive browser step can add those operations when there is a
product requirement to exercise generated applications rather than only inspect
them.

## Multimodal results

The new outcome must preserve the existing `ContentPart` model. A tool result is
not necessarily text.

```text
ToolOutcome.content
  - text
  - image
  - other supported ContentPart kinds
```

Browser and document tools can therefore return evidence without inventing a
second result transport.

Observation remains private evidence until the model explicitly chooses a
presentation capability such as `send_file`.

## UI and telemetry

Typed outcomes should remove the need for consumers to infer success by parsing
model-visible text.

Minimum requirement:

```text
ToolOutcome
   |-> model Message projection
   |-> success/failure telemetry
   `-> current interface settlement
```

Do not add a generalized UI metadata framework to every `Tool` in the first
migration. Existing interface activity labels can remain where they are unless
migration evidence shows that they are causing duplication or drift.

The important change is that UI and telemetry no longer need `text.startswith("error: ")`
to know whether a call failed.

## Relationship to later context work

This redesign should happen before the planned context engine because tool
results are part of the active conversation surface that the context engine
will later shorten.

A typed canonical result gives that work a stable boundary:

```text
full tool result / diagnostics
        |
        v
model-visible projection
        |
        v
future context shortening / archival recovery
```

The tool redesign does not implement compaction or exact history recovery. It
only avoids making those steps depend on parsing legacy tool-result prose.

## Reference systems

The design was checked against three agent systems as references, not as
blueprints to copy.

### DeepSeek Harness

Useful ideas:

- stable capability-specific failure codes plus readable diagnostics;
- a distinction between process exit and failure of the execution tool itself;
- explicit separation of model-facing capability from multiple real
  implementations where those implementations actually exist.

Guardrail for this project: DeepSeek's package/service/provider structure is
larger than this project currently needs. Copy the boundaries that solve current
problems, not the whole framework.

Reference: <https://github.com/deepseek-ai/deepseek-harness>

### Hermes Agent

Useful ideas:

- one model-facing terminal capability can execute in different environments;
- execution failures remain visible to the model;
- environment-specific execution can live below a stable tool surface.

Guardrail for this project: a universal tool can itself become a large routing
layer. Do not collect all platform logic into one giant tool implementation.

Reference: <https://github.com/NousResearch/hermes-agent>

### OpenClaw

Useful ideas:

- the execution target can be explicit and observable without changing the
  model-facing concept of the tool;
- sandbox location, tool policy and elevated execution are separate concerns;
- browser capability can grow from observation toward interaction without
  requiring every operation on day one.

Guardrail for this project: do not implement interactive browser/session
machinery before the product needs it.

Reference: <https://github.com/openclaw/openclaw>

## Overengineering guardrails

The redesign is too large if it requires any of the following before a current
consumer exists:

- a registry hierarchy for services and providers;
- a Modal-specific implementation solely because the process runs on Modal;
- environment metadata repeated in every result;
- a generic automatic retry/recovery engine;
- a large universal error enum;
- a browser session manager before browser interaction exists;
- a generic UI-presentation metadata framework for every tool;
- a second agent lifecycle or a rewrite of the V2 loop.

Prefer adding one small interface later over maintaining an unused abstraction
now.

## Proposed minimum architecture

```text
                      MODEL
                        |
                     ToolCall
                        |
                        v
              existing ToolExecutor
          pre_execute -> execute -> post_execute
                  |               |
                  |               `-> telemetry
                  v
               Tool handler
                  |
                  v
              ToolOutcome
          +-------------------+
          | content[]         |
          | error?            |
          +-------------------+
                  |
         +--------+---------+
         |                  |
         v                  v
 model-visible Message   UI settlement

ToolFailure
  - code?
  - message
  - detail?
```

Capability-specific implementation boundaries are added only where there is a
real second implementation.

## Migration shape to evaluate later

This is design sequencing, not roadmap order or implementation approval.

1. Introduce `ToolOutcome` and `ToolFailure` beside the current types.
2. Make `ToolExecutor` own normalization and outcome-to-`Message` projection.
3. Migrate the simplest filesystem tools first and verify the same semantics
   locally and in the deployed profile without creating platform-specific tool
   names.
4. Migrate remaining observation/presentation/state tools incrementally.
5. Keep browser behaviour read-only during this migration; only clean its
   internal boundary enough that later interaction can be added without
   replacing the tool system.
6. Remove the `error:` prefix as an internal failure protocol only after every
   consumer reads typed outcomes.

A broad source implementation crossing the tool boundary would still require a
separate explicit human start gate under `AGENTS.md`.

## Acceptance questions for the design

Before this proposal becomes roadmap work, the architecture should be able to
answer these without adding another layer:

1. Can the same model-facing filesystem tools run in local and Modal profiles
   with the same semantics?
2. Does a recoverable filesystem error give the model enough faithful
   information to choose a next action?
3. Does an unexpected implementation exception become useful model-visible
   failure information without killing an otherwise recoverable turn?
4. Can developer telemetry retain the full exception while the model receives
   a safe diagnostic?
5. Can telemetry determine tool success without parsing result prose?
6. Can multimodal tools still return images through `ContentPart`?
7. Can a future context engine identify and shorten tool results without
   depending on the literal `error:` convention?
8. Can browser interaction be added later without changing the general tool
   execution contract?
9. Did the migration avoid introducing an abstraction that has only one real
   implementation and no current consumer?

## Open decisions

The following are intentionally not settled by this draft:

- exact Python names and module layout for `ToolOutcome` / `ToolFailure`;
- the first concrete error-code set;
- whether `Tool.run` returns `ToolOutcome` directly or a smaller capability
  result that `ToolExecutor` wraps;
- whether any current capability already justifies a backend `Protocol` during
  the first migration;
- the eventual public shape of interactive browser actions;
- exact roadmap placement and numbering;
- whether and how `todo_write`, `ask_user`, browser interaction and the future
  execution sandbox are reordered after the tool boundary is approved.

Those choices should be made from the existing code and migration surface, then
reflected in `ROADMAP.md` only after explicit human approval.
