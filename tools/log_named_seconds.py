"""Per turn: total, model, tools, and the harness's own seconds by name, from a log dump.

    python tools/log_named_seconds.py <dump.txt>

The same dump `log_gaps.py` reads. For every finished turn: the total, the
model's and the tools' own seconds, the seconds the harness named on the
trace (roadmap 18's first half: `spent(kind)` events with a `duration_ms`),
and the remainder nobody named, with the named seconds listed by kind. The
log-only events after the trace (inbox completion, Volume trips around the
worker, telemetry close) are printed apart. This is the table roadmap 18's
second half decides from. Reads a file; starts nothing.
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

NAMED = (
    "turn_prepared", "graph_built", "context_loaded", "checkpoint_read", "checkpoint_write",
    "telegram_call", "interjections_read", "history_written", "volume_commit", "volume_reload",
    "command_remote", "turn_closed", "persist_finished",
)

events = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = re.search(r"(\{\"run_id\".*\})\s*$", line)
    if not m:
        continue
    try:
        e = json.loads(m.group(1))
    except json.JSONDecodeError:
        continue
    e["_t"] = datetime.fromisoformat(e["timestamp"]) if "timestamp" in e else None
    events.append(e)

by_run = defaultdict(list)
for e in events:
    by_run[e["run_id"]].append(e)

print(f"{'run':10} {'total':>7} {'model':>7} {'tools':>7} {'named':>7} {'unnamed':>8}  named by kind (s)")
for run, evs in by_run.items():
    kinds = {e["event"] for e in evs}
    if "turn_started" not in kinds or "turn_finished" not in kinds:
        continue
    evs.sort(key=lambda e: e["seq"])
    t0 = next(e["_t"] for e in evs if e["event"] == "turn_started")
    t1 = next(e["_t"] for e in evs if e["event"] == "turn_finished")
    total = (t1 - t0).total_seconds()
    model = sum(e.get("duration_ms", 0) for e in evs if e["event"] == "model_finished") / 1000
    # A command's own time is what the container spent; the two Volume trips
    # around it and the remote call's overhead are named separately.
    tools = sum(e.get("duration_ms", 0) for e in evs if e["event"] in ("tool_finished", "tool_failed")) / 1000
    named = defaultdict(float)
    counts = defaultdict(int)
    for e in evs:
        if e["event"] in NAMED and e.get("duration_ms") is not None:
            named[e["event"]] += e["duration_ms"] / 1000
            counts[e["event"]] += 1
    # Telegram calls, checkpoint writes and the Volume trips happen inside
    # what tools/model time already covers only partly; they are listed, not
    # subtracted twice.
    outside_tools = sum(v for k, v in named.items() if k not in ("volume_commit", "volume_reload", "command_remote"))
    unnamed = total - model - tools - outside_tools
    detail = ", ".join(f"{k}×{counts[k]}={v:.1f}" for k, v in sorted(named.items(), key=lambda kv: -kv[1]))
    print(f"{run[:10]:10} {total:7.1f} {model:7.1f} {tools:7.1f} {outside_tools:7.1f} {unnamed:8.1f}  {detail}")

after = [e for e in events if e["event"] in ("inbox_completed", "volume_reload_before_turn", "volume_commit_after_turn", "telemetry_closed", "telemetry_written")]
if after:
    print()
    print("after the trace (log only):")
    for e in after:
        print(" ", e["run_id"][:14], e["event"], e.get("duration_ms", e.get("data", {}).get("duration_ms")))
