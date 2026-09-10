"""Where the seconds go between events, from a Modal log dump of trace events.

    python tools/log_gaps.py <dump.txt>

The dump is the control app's log as the CLI prints it, one trace event per
line as JSON after the timestamp (`modal app logs assistant-control`, saved
to a file). The first table is the gap between consecutive events of one
run, for the pairs where only the harness runs; the second is per turn:
total, model, tools, persist and the rest. Made for ISS-0056 and roadmap 18
(`reports/2026-09-08_item18_harness_seconds.md` §1). Reads a file; starts
nothing.
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

events = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = re.search(r"(\{\"run_id\".*\})\s*$", line)
    if not m:
        continue
    try:
        e = json.loads(m.group(1))
    except json.JSONDecodeError:
        continue
    e["_t"] = datetime.fromisoformat(e["timestamp"])
    events.append(e)

by_run = defaultdict(list)
for e in events:
    by_run[e["run_id"]].append(e)

gaps = defaultdict(list)  # (prev event, next event) -> seconds
for run, evs in by_run.items():
    evs.sort(key=lambda e: e["seq"])
    for a, b in zip(evs, evs[1:]):
        dt = (b["_t"] - a["_t"]).total_seconds()
        key = (a["event"], b["event"])
        gaps[key].append(dt)

# Only the pairs where the harness, not the model or a tool, is running.
harness = [
    ("update_enqueued", "turn_started"),
    ("turn_started", "loop_step"),
    ("loop_step", "context_prepared"),
    ("context_prepared", "model_started"),
    ("model_finished", "tool_started"),
    ("model_finished", "telegram_final_sent"),
    ("telegram_final_sent", "tool_started"),
    ("telegram_final_sent", "persist_started"),
    ("tool_finished", "loop_step"),
    ("tool_finished", "tool_started"),
    ("tool_finished", "turn_interjected"),
    ("turn_interjected", "loop_step"),
    ("model_finished", "persist_started"),
    ("persist_started", "persist_finished"),
    ("persist_finished", "turn_finished"),
    ("model_first_token", "telegram_preview_started"),
    ("telegram_preview_started", "model_finished"),
]
print(f"{'from -> to':60} {'n':>4} {'median':>8} {'p90':>8} {'max':>8} {'total':>8}")
for key in harness:
    xs = sorted(gaps.get(key, []))
    if not xs:
        continue
    n = len(xs)
    med = xs[n // 2]
    p90 = xs[int(n * 0.9)]
    print(f"{key[0] + ' -> ' + key[1]:60} {n:4} {med:8.2f} {p90:8.2f} {xs[-1]:8.2f} {sum(xs):8.1f}")

# Per turn: total, model, tool, and the rest.
print()
print(f"{'run':10} {'total':>7} {'model':>7} {'tools':>7} {'persist':>7} {'harness':>7} {'steps':>5}")
rows = []
for run, evs in by_run.items():
    evs.sort(key=lambda e: e["seq"])
    kinds = {e["event"] for e in evs}
    if "turn_started" not in kinds or "turn_finished" not in kinds:
        continue
    t0 = next(e["_t"] for e in evs if e["event"] == "turn_started")
    t1 = next(e["_t"] for e in evs if e["event"] == "turn_finished")
    total = (t1 - t0).total_seconds()
    model = sum(e.get("duration_ms", 0) for e in evs if e["event"] == "model_finished") / 1000
    tools = sum(e.get("duration_ms", 0) for e in evs if e["event"] in ("tool_finished", "tool_failed")) / 1000
    persist = sum(e.get("duration_ms", 0) for e in evs if e["event"] == "persist_finished") / 1000
    steps = sum(1 for e in evs if e["event"] == "loop_step")
    rows.append((run[:8], total, model, tools, persist, total - model - tools - persist, steps))
for r in sorted(rows, key=lambda r: -r[1]):
    print(f"{r[0]:10} {r[1]:7.1f} {r[2]:7.1f} {r[3]:7.1f} {r[4]:7.1f} {r[5]:7.1f} {r[6]:5}")
