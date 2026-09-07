"""What a tool is, and how a set of them is described to the model.

A tool is a name, a JSON-schema parameter description and a callable. It
**returns content on success and raises `ToolError` on failure**; it never
builds a failure by hand and never returns one as text. What it returns is what
the model will read, and what it raises is what the executor in
`app/tools/execution.py` will type. Nothing here knows about a provider or a
graph.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.models import ContentPart, Message, ToolCall, ToolFailure

ToolReturn = str | Sequence[ContentPart]

# The runtime's own failure codes. A family owns its own (`fs.not_found`) and
# adds one only when something has to branch on it.
UNKNOWN_TOOL = "unknown_tool"  # the name matched nothing the toolbox has
BAD_ARGUMENTS = "bad_arguments"  # the arguments failed the schema or were unreadable
OUTPUT_CUT = "output_cut"  # the model's answer hit its output limit inside the call
DECLINED = "declined"  # the person answered no to an approval
NOT_RUN = "not_run"  # the loop halted the call: budget, stop, or a repeating failure
INTERRUPTED = "interrupted"  # the worker died while the call ran; whether it ran is unknown
TIMEOUT = "timeout"  # the executor's deadline passed
INTERNAL = "internal"  # an exception the tool did not expect; traceback in the log
FAILED = "failed"  # a ToolError that named no code


class ToolError(RuntimeError):
    """The tool refuses, or cannot do, what the model asked.

    Raised, not returned: the executor turns it into a typed failure the model
    reads. `code` is the family's stable name for what went wrong; `detail` is
    the diagnostic worth showing beside the sentence, such as an `strerror`.
    """

    def __init__(
        self, message: str, *, code: str = FAILED, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ToolOutcome:
    """What one call produced. `failure is None` is the only definition of success."""

    content: tuple[ContentPart, ...]
    failure: ToolFailure | None = None


def handover(path: str, what: str = "it") -> str:
    """How a workspace item reaches the person, said by the tool that made it.

    A tool result names the action its output enables, in the shape of the
    call. The model was handing a bare path over as a markdown image, live,
    four turns in a row on 2026-09-03: a path looks like something to embed,
    a call looks like something to make. The decision is still the model's;
    the sentence only says what the option is.
    """

    return f"to hand {what} to the person: send_file(path={json.dumps(path)}); nothing is sent otherwise"


def tool_failed(message: Message) -> bool:
    """Whether this tool result reports a failure rather than a result."""

    return message.failure is not None


@dataclass(frozen=True)
class Tool:
    """One capability the model may call.

    `requires_approval` declares an effect that needs the person's yes before
    execution. The policy is about the boundary crossed rather than whether
    bytes change: work confined to the person's workspace is autonomous, while
    publication, third-party, spending and infrastructure effects set this.

    `timeout_seconds` is enforced by the executor, so the tool need not know. It
    exists because a tool that hangs otherwise hangs past the turn's own
    budget, which is only read at step boundaries. A synchronous tool with a
    timeout runs in a worker thread so the deadline can pass without it; one
    without a timeout runs on the loop as before.

    `replay_safe` says the call may simply be run again when nobody knows
    whether it ran: a worker died while a step's tools were running, and a
    later worker takes the turn up. Reading is replay-safe; anything that
    changes, sends or remembers is not, and the model is told the outcome is
    unknown instead (the `interrupted` result). Declared here, once, like
    `requires_approval`, rather than guessed at the moment of recovery.

    `mutates` says the tool changes the workspace. In the default mode that
    changes nothing: work inside the workspace is autonomous. In `careful`
    mode (`app/agent/mode.py`) the toolbox asks for these too, so a person can
    watch every change go by without the tools knowing which mode they are in.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., ToolReturn | Awaitable[ToolReturn]]
    # The contract's other two parts (roadmap 16, 2026-09-07): what a call
    # returns to the model, and what it leaves behind and where — a file in
    # the workspace, a message in the person's chat, a fact in memory, or
    # nothing. `description` says what the tool does and takes. The three are
    # rendered together into the schema the model reads, so a tool cannot
    # state one and leave the others to be guessed; `tests/test_tool_contracts.py`
    # refuses a wired tool that leaves either empty.
    returns: str = ""
    leaves: str = ""
    requires_approval: bool = False
    timeout_seconds: float | None = None
    replay_safe: bool = False
    mutates: bool = False
    # A tool that only hands something already made to the person. It costs no
    # model time and its result is the outcome of the turn, so a turn that has
    # spent its budget still runs it: the ceiling bounds work, not delivery.
    delivers: bool = False

    @property
    def contract(self) -> str:
        """The description the model reads: does, returns, leaves, in that order."""

        parts = [self.description.strip()]
        if self.returns:
            parts.append(f"Returns: {self.returns.strip()}")
        if self.leaves:
            parts.append(f"Leaves: {self.leaves.strip()}")
        return chr(10).join(parts)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.contract,
                "parameters": self.parameters,
            },
        }


# --- what open-weight models get wrong in the same few ways -------------------

# A name the model wrapped the way another harness taught it. Stripped before
# the case-insensitive match, and only against the allowlist: a name that still
# matches nothing is `unknown_tool`, never a guess.
NAME_PREFIXES = ("functions.", "functions:", "tools.", "tools:", "call:")

# Names the roadmap has renamed, old to new. Empty because it has renamed none;
# the table exists so a rename lands here rather than in a model's memory.
LEGACY_NAMES: dict[str, str] = {}


def _as_int(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    return value


def _as_number(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return value
    return value


def _as_boolean(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return value


def _parsed(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return None


def _as_array(value: Any) -> Any:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = _parsed(value)
        if isinstance(parsed, list):
            return parsed
    if isinstance(value, dict):
        return value
    return [value]


def _as_object(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _parsed(value)
        if isinstance(parsed, dict):
            return parsed
    return value


COERCIONS: dict[str, Callable[[Any], Any]] = {
    "integer": _as_int,
    "number": _as_number,
    "boolean": _as_boolean,
    "array": _as_array,
    "object": _as_object,
}


def coerce_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Bring each argument to its declared type where the intent is unambiguous.

    Numbers and booleans as strings, a scalar where an array is declared, an
    object or array serialized as a string: the shapes open-weight models
    produce for a schema they read correctly. A value that cannot be coerced is
    left alone for validation to name, and nothing is ever invented.
    """

    properties = schema.get("properties") or {}
    coerced = dict(arguments)
    for name, value in arguments.items():
        expected = (properties.get(name) or {}).get("type")
        convert = COERCIONS.get(expected) if isinstance(expected, str) else None
        if convert is not None and value is not None:
            coerced[name] = convert(value)
    return coerced


class Toolbox:
    """The tools one agent may use, and how a call is matched against them."""

    def __init__(self, tools: Iterable[Tool] = (), ask_for_changes: bool = False) -> None:
        self._tools = {tool.name: tool for tool in tools}
        # `careful` mode: a tool that changes the workspace asks first.
        self.ask_for_changes = ask_for_changes

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def resolve(self, name: str) -> str | None:
        """The tool this name means, or `None`.

        Exact first. Otherwise, against the allowlist only: case-insensitive,
        with a prefix another harness taught the model stripped, and the names
        the roadmap has renamed. Nothing fuzzier — a near miss is a different
        tool, and running it would be inventing a call the model never made.
        """

        if name in self._tools:
            return name
        candidate = name.strip()
        lowered = candidate.lower()
        for prefix in NAME_PREFIXES:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix) :]
                lowered = candidate.lower()
                break
        candidate = LEGACY_NAMES.get(lowered, candidate)
        by_lower = {known.lower(): known for known in self._tools}
        return by_lower.get(candidate.lower())

    def requires_approval(self, name: str) -> bool:
        """Whether policy requires approval before this tool may execute.

        An unknown name never runs, so there is nothing to ask about.
        """

        tool = self._tools.get(name)
        if tool is None:
            return False
        return tool.requires_approval or (self.ask_for_changes and tool.mutates)

    def coerce(self, call: ToolCall) -> ToolCall:
        """The call with its arguments brought to the declared types."""

        tool = self._tools.get(call.name)
        if tool is None:
            return call
        return replace(call, arguments=coerce_arguments(call.arguments, tool.parameters))

    def validation_error(self, call: ToolCall) -> str | None:
        """Return a readable JSON-schema error without executing the tool.

        Tool schemas in this project deliberately use a small JSON-schema
        subset. Keeping the check here makes the schema shown to the model the
        same contract enforced before consent and execution.
        """

        tool = self._tools.get(call.name)
        if tool is None:
            return None
        schema = tool.parameters
        arguments = call.arguments
        if schema.get("type") == "object" and not isinstance(arguments, dict):
            return "arguments must be an object"

        properties = schema.get("properties", {})
        missing = [name for name in schema.get("required", []) if name not in arguments]
        if missing:
            # The served parser reads past a string that ends with a markdown
            # fence and loses the argument after it (ISS-0001). Three
            # identical calls on 2026-09-03 (run `e54b442b`) show the bare
            # "missing" was not enough for the model to change anything; the
            # cause and the way out are named where it can read them.
            fenced = [
                name
                for name, value in arguments.items()
                if isinstance(value, str) and value.rstrip().endswith("```")
            ]
            hint = (
                f"; {fenced[0]} ends with a markdown fence, which is what lost "
                f"{missing[0]} — send the call again with {missing[0]} first and no fence"
                if fenced
                else ""
            )
            return f"missing required argument(s): {', '.join(missing)}{hint}"

        if schema.get("additionalProperties") is False:
            unexpected = [name for name in arguments if name not in properties]
            if unexpected:
                return f"unexpected argument(s): {', '.join(unexpected)}"

        json_types = {
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
        }
        for name, value in arguments.items():
            expected = properties.get(name, {}).get("type")
            if expected in json_types and not json_types[expected](value):
                return f"argument {name!r} must be {expected}"
            minimum = properties.get(name, {}).get("minLength")
            if minimum is not None and isinstance(value, str) and len(value) < minimum:
                return f"argument {name!r} must contain at least {minimum} character(s)"
        return None

    def signature(self, name: str) -> str:
        """How to call this tool, in one line, for a model that just got it wrong.

        The schema is already in the request, but a model that has just made a
        malformed call is plainly not reading it there. Repeating the shape
        beside the complaint costs a few tokens and is the difference between a
        correction and another identical attempt — eight of which happened live
        on 2026-08-30.
        """

        tool = self._tools.get(name)
        if tool is None:
            return ""
        properties = tool.parameters.get("properties") or {}
        required = tool.parameters.get("required") or []
        if not properties:
            return f"{name} takes no arguments"
        shown = []
        for argument, schema in properties.items():
            kind = schema.get("type", "value")
            optional = "" if argument in required else ", optional"
            shown.append(f"{argument} ({kind}{optional})")
        return f"{name} takes: {', '.join(shown)}"

    def run(self, call: ToolCall) -> Message:
        """Run a synchronous tool and return the message shown to the model.

        A convenience over the executor for callers with no turn to trace —
        `/check` and the tests. The lifecycle is the executor's; there is not a
        second one here.
        """

        from app.tools.execution import ToolExecutor

        return ToolExecutor(self).call_sync(call)

    async def run_async(self, call: ToolCall) -> Message:
        """Run either a synchronous or asynchronous tool."""

        from app.tools.execution import ToolExecutor

        return await ToolExecutor(self).call(call)
