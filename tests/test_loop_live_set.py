"""The live suite's own wiring, without a model: which letters run, and how
two runs are laid side by side (item 15, 2026-09-07)."""

from __future__ import annotations

from scripts.loop_live import MINI, WIDER, Result, chosen, side_by_side


def test_the_default_is_the_mini_set_and_letters_pick_from_both() -> None:
    assert chosen([]) == frozenset(MINI)
    assert chosen(["--deployed"]) == frozenset(MINI)
    assert chosen(["b", "M"]) == frozenset("BM")
    assert chosen(["R", "S"]) == frozenset("RS")
    assert chosen(["z"]) == frozenset(MINI), "an unknown letter is not a scenario"
    assert not set(MINI) & set(WIDER)


def row(letter: str, failed: int = 0, seconds: float = 10.0) -> Result:
    return Result(letter, f"{letter} name", failed, seconds, 2, 1, 5000, 100)


def test_side_by_side_puts_the_same_scenario_on_one_line() -> None:
    table = side_by_side([row("A"), row("B", failed=1, seconds=12.0)], [row("A", seconds=13.5), row("W")])
    lines = table.splitlines()
    assert lines[2].startswith("A  PASS") and "+3.5" in lines[2]
    assert lines[3].startswith("B  FAIL 1") and lines[3].rstrip().endswith("-")
    assert lines[4].startswith("W  -")


def test_model_and_temperature_reach_the_environment_for_the_local_path(monkeypatch) -> None:
    from scripts.loop_live import apply_model

    # Set, not deleted: `delenv` of a name that is absent records nothing,
    # and the value `apply_model` writes then outlived the test and turned
    # every later `ModelSettings()` into the `tuned` set (2026-09-12).
    monkeypatch.setenv("MODEL", "")
    monkeypatch.setenv("MODEL_TUNED_TEMPERATURE", "")
    apply_model(["--model", "tuned", "--temperature", "0.7", "D"])
    import os

    assert os.environ["MODEL"] == "tuned"
    assert os.environ["MODEL_TUNED_TEMPERATURE"] == "0.7"
