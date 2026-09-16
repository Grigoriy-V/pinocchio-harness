"""A yes the person gave once and asked to be remembered (roadmap 32).

Every reference remembers an approval: Hermes once / this session / always,
Codex a prefix rule, OpenClaw a grant until revoked. Here a grant is keyed by
the tool's name and, for `run_command`, by the command's first word (the
program), so "always allow git" is one grant and "allow this one write" is
none. Scopes: `once` remembers nothing; `conversation` holds for one thread;
`always` holds for this person until they remove it.

Kept in the person's own workspace, `.agent/grants.json`, beside the other
switches: never in the repository, the same in every interface, gone with the
workspace. The interface's buttons that choose a scope are roadmap 34's; the
answer to a consent question carries the scope as a string, and a plain
`True` is `once`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import ToolCall

GRANTS_FILE = Path(".agent") / "grants.json"
SCOPES = ("once", "conversation", "always")
PREFIXED = {"run_command": "command"}


def prefix_of(call: ToolCall) -> str:
    """What of the call a grant is keyed on beside the tool's name: the
    command's program for `run_command`, nothing for the rest."""

    argument = PREFIXED.get(call.name)
    if argument is None:
        return ""
    text = str(call.arguments.get(argument) or "").strip()
    return text.split()[0] if text else ""


def scope_of(answer: Any) -> str | None:
    """The scope an approval answer carries; `None` for a no."""

    if answer is True:
        return "once"
    if isinstance(answer, str) and answer in SCOPES:
        return answer
    return None


class Grants:
    """The remembered approvals of one person, read and written per question."""

    def __init__(self, workspace: Path | str) -> None:
        self.path = Path(workspace) / GRANTS_FILE

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    @staticmethod
    def _matches(entries: Any, call: ToolCall) -> bool:
        if not isinstance(entries, list):
            return False
        prefix = prefix_of(call)
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("tool") != call.name:
                continue
            wanted = str(entry.get("prefix") or "")
            if not wanted or wanted == prefix:
                return True
        return False

    def allows(self, thread_id: str, call: ToolCall) -> bool:
        """Whether an earlier yes covers this call, so no question is asked."""

        data = self._read()
        return self._matches(data.get("always"), call) or self._matches(
            (data.get("conversation") or {}).get(thread_id), call
        )

    def remember(self, thread_id: str, call: ToolCall, scope: str) -> None:
        """Keep a yes under its scope; `once` keeps nothing."""

        if scope not in SCOPES or scope == "once":
            return
        entry = {"tool": call.name, "prefix": prefix_of(call)}
        data = self._read()
        if scope == "always":
            entries = data.setdefault("always", [])
        else:
            entries = data.setdefault("conversation", {}).setdefault(thread_id, [])
        if not isinstance(entries, list):
            return
        if entry not in entries:
            entries.append(entry)
        self._write(data)

    def forget(self, thread_id: str | None = None) -> None:
        """Drop one conversation's grants, or every grant when none is named."""

        if thread_id is None:
            self._write({})
            return
        data = self._read()
        (data.get("conversation") or {}).pop(thread_id, None)
        self._write(data)

    def listed(self, thread_id: str) -> list[str]:
        """The grants in force for a conversation, one line each, for a person."""

        data = self._read()
        lines = []
        for entry in data.get("always") or []:
            if isinstance(entry, dict):
                lines.append(f"always: {entry.get('tool')} {entry.get('prefix') or ''}".rstrip())
        for entry in (data.get("conversation") or {}).get(thread_id) or []:
            if isinstance(entry, dict):
                lines.append(
                    f"this conversation: {entry.get('tool')} {entry.get('prefix') or ''}".rstrip()
                )
        return lines


class NoGrants:
    """For a loop with nowhere to keep a yes: every question is asked."""

    def allows(self, thread_id: str, call: ToolCall) -> bool:
        return False

    def remember(self, thread_id: str, call: ToolCall, scope: str) -> None:
        return None


NO_GRANTS = NoGrants()
