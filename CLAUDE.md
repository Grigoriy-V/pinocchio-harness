@AGENTS.md

## Claude-specific notes

`AGENTS.md` above contains the authoritative working and safety rules.
Nothing in this file may add, weaken, or replace a rule in it.

- `.claude/settings.json` duplicates some hard rules as a mechanical backstop.
  It is not the source of any rule.
- Use `--agent claude` when writing a work-log record.
- Judge subagents (Sonnet, blind, several at once) score trajectories by
  the rubric in `reports/2026-09-11_gemma_finetune_experiment.md` §2, packed
  by `tools/judge_pack.py` (the human, 2026-09-11).
