"""Per turn, from the telemetry store: total, model, tools, and the harness's named seconds.

    python tools/run_named_seconds.py --last 20
    python tools/run_named_seconds.py --user loop-live-check --last 12
    python tools/run_named_seconds.py <run_id> [<run_id> ...]

The same table `log_named_seconds.py` makes from a log dump, read from the
store the runs were written to (the local SQLite by default, the deployed
database when `AGENT_DATABASE_URL` is set), so it needs no dump. The
log-only events after a turn (inbox completion, the Volume trips around the
worker) are not here; they never reach the store. Read-only; starts nothing.
"""

import argparse
import sys
from collections import defaultdict

from app.config import AgentSettings
from app.telemetry.open import open_telemetry

NAMED = (
    "turn_prepared",
    "graph_built",
    "context_loaded",
    "checkpoint_read",
    "checkpoint_write",
    "telegram_call",
    "interjections_read",
    "history_written",
    "volume_commit",
    "volume_reload",
    "command_remote",
    "turn_closed",
    "persist_finished",
)
# Inside a tool's own duration: listed, not subtracted again.
INSIDE_TOOLS = ("volume_commit", "volume_reload", "command_remote")


def table(store, runs) -> None:
    print(
        f"{'run':22} {'total':>7} {'model':>7} {'tools':>7} {'named':>7} {'unnamed':>8}"
        "  named by kind (s)"
    )
    for run in runs:
        events = store.events(run.run_id)
        if run.total_ms is None:
            continue
        total = run.total_ms / 1000
        model = sum(e.duration_ms or 0 for e in events if e.type == "model_finished") / 1000
        tools = (
            sum(e.duration_ms or 0 for e in events if e.type in ("tool_finished", "tool_failed"))
            / 1000
        )
        named: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for e in events:
            if e.type in NAMED and e.duration_ms is not None:
                named[e.type] += e.duration_ms / 1000
                counts[e.type] += 1
        outside = sum(v for k, v in named.items() if k not in INSIDE_TOOLS)
        unnamed = total - model - tools - outside
        detail = ", ".join(
            f"{k}×{counts[k]}={v:.2f}" for k, v in sorted(named.items(), key=lambda kv: -kv[1])
        )
        print(
            f"{run.run_id[:22]:22} {total:7.1f} {model:7.1f} {tools:7.1f} {outside:7.1f}"
            f" {unnamed:8.1f}  {detail}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_ids", nargs="*")
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--user", default=None)
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    telemetry = open_telemetry(AgentSettings())
    store = telemetry.store
    if store is None:
        print("telemetry is off (AGENT_TELEMETRY=0); there is nothing to read")
        return 2
    try:
        if options.run_ids:
            runs = [r for r in (store.get_turn(i) for i in options.run_ids) if r is not None]
        else:
            runs = store.recent_runs(limit=options.last, user_id=options.user)
        table(store, runs)
        return 0
    finally:
        telemetry.close()


if __name__ == "__main__":
    raise SystemExit(main())
