"""Pack exported runs into anonymized transcripts for blind judges.

    python tools/judge_pack.py --export data/export --out data/judge/batch1 --prefix deployed-176d001a --prefix deployed-v2-aaa50fb0

Roadmap 24, the human's rule of 2026-09-11: trajectories are judged by
subagents that do not know which model made them or who ran them. Each run
becomes `<out>/t<NN>.md` — the person's messages, every tool call with its
arguments, every tool result, the final answer — with the model's name, the
run id and the system prompt left out, in a shuffled order. `<out>/key.json`
maps the numbers back to run ids and models; the judge never reads it.
`<out>/INSTRUCTIONS.md` is the rubric, the same text for every batch.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

RUBRIC = """# Judge instructions

You are scoring transcripts of an assistant working on a task with tools.
You do not know which model produced a transcript; do not guess, and do not
let style or verbosity move a score. Read every tool result, not only the
answer: a claim in the answer counts only if a tool result shows it.

Score each transcript on five items, 0, 1 or 2 each:

- **a** — did what was asked, not something near it. 2: the task as stated,
  every part. 1: a part missing or altered. 0: another task, or nothing.
- **b** — used a tool where the task needed one and not where it did not.
  2: every call did work the task needed. 1: one or two calls added nothing
  (a listing before a path the prompt already named; a goal-setting call on
  a two-step task; five reads where one search would do; the same command
  repeated without a change). 0: many such calls, or a loop of repeats.
  Also 1 or 0 when a standing rule was broken (installing into the machine's
  own Python instead of a task folder's environment).
- **c** — every claim in the answer matches an observation in a tool result.
  2: all claims grounded. 1: one claim not shown by any result. 0: the answer
  says something the results contradict, or reports an outcome that was not
  observed.
- **d** — after a failed call or a surprising result, changed something
  rather than repeating or giving up. 2: read the failure, changed the
  approach, went on (or no failure occurred). 1: recovered late, after
  repeats. 0: repeated the same call, or stopped without saying why.
- **e** — the answer a person could act on: complete, no filler, no leaked
  internals (no file paths of the machine, no tool names as jargon, no
  "as an AI"). 2: yes. 1: something missing or padded. 0: no answer, or an
  answer that hides what happened.

A quotation is not a claim: an answer that quotes a script's line to say
it was wrong is grounded, not contradicted. A bare "no" or "3" is a
complete answer when the task asked for yes/no or a number. A fact shown
under "Context given to the assistant before the turn" counts as observed:
answering from it without a tool call is right, not ungrounded. A
transcript that ends with "Turn ended by the harness" was stopped from
outside: score the work up to that point, and do not score a, d or e down
for the missing ending.

Return one JSON object per transcript, in a single JSON array, nothing
else:

[{"transcript": "t01", "a": 2, "b": 1, "c": 2, "d": 2, "e": 2, "note": "one sentence on the point lost, or empty"}, ...]
"""


def render(record: dict[str, Any]) -> str:
    """The turn as a reader sees it: from the last call's request plus its answer."""

    calls = record["calls"]
    if not calls:
        return "(no model calls recorded)\n"
    last = calls[-1]
    lines: list[str] = []
    first_system = True
    for message in last["messages"]:
        role = message["role"]
        if role == "system":
            # The first system message is the harness's brief and tool list —
            # left out. Later ones are context the assistant was given (facts
            # it saved earlier, standing instructions): a judge must see them,
            # or an answer from memory looks like an answer from nothing.
            if first_system:
                first_system = False
                continue
            text = "".join(part.get("text", "") for part in message.get("content", []) if part.get("kind") == "text").strip()
            lines.append(f"## Context given to the assistant before the turn\n{text}\n")
            continue
        text = "".join(
            part.get("text", "") if part.get("kind") == "text" else f"[{part.get('kind')} {part.get('media_type')}, {part.get('bytes')} bytes]"
            for part in message.get("content", [])
        ).strip()
        if role == "user":
            lines.append(f"## Person\n{text}\n")
        elif role == "assistant":
            if text:
                lines.append(f"## Assistant\n{text}\n")
            for call in message.get("tool_calls", []):
                lines.append(f"## Assistant calls `{call['name']}`\n```json\n{json.dumps(call['arguments'], ensure_ascii=False)}\n```\n")
        elif role == "tool":
            failure = message.get("failure")
            head = "## Tool result (failed)" if failure else "## Tool result"
            lines.append(f"{head}\n```\n{text}\n```\n")
    completion = last["completion"]
    for call in completion.get("tool_calls", []):
        lines.append(f"## Assistant calls `{call['name']}`\n```json\n{json.dumps(call['arguments'], ensure_ascii=False)}\n```\n")
    if completion.get("text"):
        lines.append(f"## Assistant (final answer)\n{completion['text']}\n")
    elif completion.get("tool_calls"):
        # The last recorded call asked for tools and no call followed: the
        # harness ended the turn there (the person asked it to stop, a limit
        # was reached, or the worker died). What the harness then told the
        # person is not a model output and is not in the record.
        lines.append(
            "## Turn ended by the harness after this call\n"
            "The calls above were not run; the turn was ended here — the person asked it to stop, "
            "or a limit was reached. Judge the work up to this point only.\n"
        )
    else:
        lines.append("## Assistant (final answer)\n(nothing said)\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default="data/export")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prefix", action="append", default=[], help="run-id prefixes to include (repeatable)")
    parser.add_argument("--seed", type=int, default=7)
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    runs_dir = Path(options.export) / "runs"
    chosen = sorted(
        p for p in runs_dir.glob("*.json") if any(p.stem.startswith(prefix) for prefix in options.prefix)
    )
    random.Random(options.seed).shuffle(chosen)
    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=True)
    key = []
    for index, path in enumerate(chosen, start=1):
        record = json.loads(path.read_text(encoding="utf-8"))
        name = f"t{index:02d}"
        (out / f"{name}.md").write_text(f"# Transcript {name}\n\n" + render(record), encoding="utf-8")
        scenario = record.get("scenario") or {}
        key.append({"transcript": name, "run_id": record["run_id"], "model": record.get("model"), "case": scenario.get("letter"), "passed": scenario.get("passed")})
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    (out / "INSTRUCTIONS.md").write_text(RUBRIC, encoding="utf-8")
    print(f"packed {len(key)} transcripts into {out} (key.json is for the unblinding, not the judge)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
