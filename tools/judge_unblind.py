"""Unblind a judge batch: votes_*.json beside key.json, scores by model and case.

    python tools/judge_unblind.py reports/judge/<batch> [--export data/export3]

Roadmap 24. The judges never saw key.json; this reads it after the votes
are in and prints, for each model, the mean total over its transcripts per
judge and across judges, the mean of each criterion, a case-by-case table,
and where the judges disagreed by more than a point. `--export` fills the
case of a run whose key line lacks one (older exports) from its thread id.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

CRITERIA = ("a", "b", "c", "d", "e")


def load_votes(batch: Path) -> dict[str, dict[str, dict]]:
    votes: dict[str, dict[str, dict]] = {}
    for path in sorted(batch.glob("votes_*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"{path.name}: unreadable ({error}); skipped")
            continue
        votes[path.stem.removeprefix("votes_")] = {row["transcript"]: row for row in rows}
    return votes


def cases_from_export(export: Path | None) -> dict[str, str]:
    if not export or not (export / "index.jsonl").is_file():
        return {}
    out = {}
    for line in (export / "index.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        case = (entry.get("scenario") or {}).get("letter")
        if not case:
            found = re.search(r"chat-([a-z]\d)", entry.get("thread_id", ""))
            case = found.group(1).upper() if found else "?"
        out[entry["run_id"]] = case
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("batch", type=Path)
    parser.add_argument("--export", type=Path, default=None)
    args = parser.parse_args()

    key = json.loads((args.batch / "key.json").read_text(encoding="utf-8"))
    cases = cases_from_export(args.export)
    votes = load_votes(args.batch)
    if not votes:
        print("no votes_*.json yet")
        return 1
    judges = sorted(votes)
    print(f"judges: {', '.join(judges)}; transcripts: {len(key)}")

    by_model: dict[str, list[dict]] = defaultdict(list)
    for entry in key:
        entry = dict(entry)
        entry["case"] = entry.get("case") or cases.get(entry["run_id"]) or "?"
        totals = [votes[j][entry["transcript"]]["total"] for j in judges if entry["transcript"] in votes[j]]
        entry["totals"] = totals
        entry["mean"] = statistics.mean(totals) if totals else None
        entry["criteria"] = {
            c: statistics.mean(
                votes[j][entry["transcript"]][c] for j in judges if entry["transcript"] in votes[j]
            )
            for c in CRITERIA
        } if totals else {}
        by_model[entry["model"]].append(entry)

    print("\n== by model (mean total of 10, per judge and across) ==")
    for model, entries in sorted(by_model.items()):
        per_judge = []
        for j in judges:
            scored = [votes[j][e["transcript"]]["total"] for e in entries if e["transcript"] in votes[j]]
            per_judge.append(f"{j} {statistics.mean(scored):.2f}" if scored else f"{j} -")
        means = [e["mean"] for e in entries if e["mean"] is not None]
        crit = {c: statistics.mean(e["criteria"][c] for e in entries if e["criteria"]) for c in CRITERIA}
        print(
            f"{model:22s} n={len(entries):2d}  across {statistics.mean(means):.2f}  "
            f"[{', '.join(per_judge)}]  " + " ".join(f"{c}={v:.2f}" for c, v in crit.items())
        )

    print("\n== by case (across-judge mean per model) ==")
    models = sorted(by_model)
    all_cases = sorted({e["case"] for es in by_model.values() for e in es})
    print("case  " + "  ".join(f"{m[:14]:>14s}" for m in models))
    for case in all_cases:
        cells = []
        for m in models:
            found = [e["mean"] for e in by_model[m] if e["case"] == case and e["mean"] is not None]
            cells.append(f"{statistics.mean(found):14.1f}" if found else f"{'-':>14s}")
        print(f"{case:5s} " + "  ".join(cells))

    print("\n== disagreements over a point ==")
    for entry in sorted((e for es in by_model.values() for e in es), key=lambda e: e["transcript"]):
        if len(entry["totals"]) >= 2 and max(entry["totals"]) - min(entry["totals"]) > 1:
            notes = "; ".join(
                f"{j}: {votes[j][entry['transcript']].get('note', '')[:70]}" for j in judges if entry["transcript"] in votes[j]
            )
            print(f"{entry['transcript']} {entry['model'][:16]:16s} {entry['case']:3s} {entry['totals']}  {notes}")

    print("\n== lowest across-judge means ==")
    low = sorted((e for es in by_model.values() for e in es if e["mean"] is not None), key=lambda e: e["mean"])[:8]
    for entry in low:
        note = next((votes[j][entry["transcript"]].get("note", "") for j in judges if entry["transcript"] in votes[j]), "")
        print(f"{entry['transcript']} {entry['model'][:16]:16s} {entry['case']:3s} {entry['mean']:.1f}  {note[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
