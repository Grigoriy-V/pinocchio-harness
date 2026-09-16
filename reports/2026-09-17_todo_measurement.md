# The todo measurement (roadmap 32, the human's word 2026-09-17)

**Question:** with `todo_write` offered always (the switch of 2026-09-03
gone), does GLM 5.3 Flash open a list where the work has steps, and does the
offer cost anything where it does not?

**Runs** (local, GLM 5.3 Flash through OpenRouter; logs and each room's
`telemetry.sqlite3` under `reports/prompt_runs/2026-09-17_todo/`, the mini
set's under `reports/prompt_runs/2026-09-17_item32_live/`):

1. The short half: the mini set A B C F W H E M of the roadmap 32 live gate,
   11 turns with `todo_write` offered: **0 calls to `todo_write`**, 41/41
   checks, model calls per scenario the same as on 2026-09-16 without it.
2. The long half: three requests with several steps each (`loop_live` Y, Z,
   1, written for this measurement; outcome checks only), once with the
   tool and once with `--no-todo` (the control):

| request | with the list: calls / tools / s / checks | without: calls / tools / s / checks | `todo_write` |
|---|---|---|---|
| Y a package, three tests run green, a README | 4 / 4 / 33.0 / 6 of 6 | 4 / 4 / 31.3 / 6 of 6 | 0 |
| Z a CSV to a JSON to a page, opened and checked | 7 / 6 / 58.5 / 5 of 5 | 7 / 6 / 57.7 / 5 of 5 | 0 |
| 1 three failing tests fixed, each fix written down | 7 / 7 / 44.8 / 6 of 6 | 6 / 6 / 59.3 / 6 of 6 | 0 |

(The with-list run's last checks were computed from its room after the
console's cp1251 encoding ended the script on a "−" in the answer;
`loop_live` now prints with `errors="replace"`.)

**Finding:** in 14 turns with the list offered, 4 of them work of 4–7 tool
calls with three to four named outcomes, the model never opened a list; the
offer cost nothing (the same calls, the same seconds, the same outcomes as
the control). The description ("Your own list of steps for work with
several steps; a single-step request has none…") and the brief's line
(price and consequence) leave the choice to the model, and this model
chooses no list at this size of work. Nothing here is the harness's: the
tool runs and validates (offline tests), the list is read back at the
ending (off by default since 2026-09-03), the brief is generated from the
toolbox.

**Not measured:** a request of G's size (a four-file application with
eight requirements) with the list offered; whether a larger model opens a
list here; blind judges (the transcripts with and without differ only by
the model's own variation, since the list never appeared, so a judge would
measure nothing about the tool).

**Options for the human's word** (none is a decision yet):

- (a) leave it as it is: offered always, as the references offer theirs,
  at no measured cost; the model decides, and this model decides no.
- (b) one more turn: G with the list offered, the one request in the suite
  where a list would carry eight stated requirements; if it opens one,
  ISS-0016's "ticked in bulk" and the person's correction of a plan are
  worth building; if it does not, (a) stands.
- (c) drop `todo_write` from the default set for this model; the references
  do not, and a model change would bring it back.

`set_goal` (roadmap 8): unmeasured here beside a list, because no list
appeared; the goal was called in none of the six long turns either (the
tools lists above hold no `set_goal`), which is the same finding about the
same model.
