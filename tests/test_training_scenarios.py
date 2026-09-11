"""The training families' wiring, without a model (roadmap 24, 2026-09-11):
every case seeds cleanly, its checks read outcomes and never raise, and the
letters, threads and run ids never collide with the mini and wider sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.models import ContentPart, Message
from scripts.loop_live import MINI, WIDER, chosen, repeat_of, threads_of
from scripts.training_scenarios import (
    FAMILIES,
    TRAINING,
    l1_checks,
    number_in,
    plant,
    t1_checks,
    u1_checks,
)


@dataclass
class FakeTurn:
    """What a check may read, with nothing in it unless a test puts it there."""

    tools: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    tool_results: list[Message] = field(default_factory=list)
    failures: list = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    _names: dict[str, str] = field(default_factory=dict)

    @property
    def answer(self) -> str:
        return self.text[-1] if self.text else ""

    @property
    def said(self) -> str:
        return " ".join(self.text)

    def read_from(self, tool: str) -> str:
        ids = {i for i, n in self._names.items() if n == tool}
        return " ".join(
            p.text or "" for m in self.tool_results if m.tool_call_id in ids for p in m.content
        )

    def arguments_of(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def result(self, tool: str, text: str) -> None:
        call_id = f"c{len(self._names)}"
        self._names[call_id] = tool
        self.tools.append(tool)
        self.calls.append((tool, {}))
        self.tool_results.append(
            Message(role="tool", content=[ContentPart(kind="text", text=text)], tool_call_id=call_id)
        )


def test_the_families_have_their_own_letters_threads_and_ids() -> None:
    assert not set(TRAINING) & set(MINI + WIDER)
    assert chosen(["D", "x"]) == frozenset("DX")
    sequences = [f.sequence(c) for f in FAMILIES.values() for c in f.cases]
    assert len(sequences) == len(set(sequences)) and min(sequences) > 190
    threads = [t for f in FAMILIES.values() for t in f.threads()]
    assert len(threads) == len(set(threads))
    assert threads_of("D") == FAMILIES["D"].threads()
    assert repeat_of(["--deployed", "--repeat", "5"]) == 5 and repeat_of([]) == 1


def test_every_case_seeds_and_its_checks_read_outcomes_without_raising(tmp_path: Path) -> None:
    for family in FAMILIES.values():
        for case in family.cases:
            plant(tmp_path, case.files)
            for relative, content in case.files.items():
                assert (tmp_path / relative).read_text(encoding="utf-8") == content
            verdict = case.checks(FakeTurn(), tmp_path)
            assert verdict and all(isinstance(v, bool) for v in verdict.values())
            # An empty turn never passes: every check wants something to have happened.
            assert not all(verdict.values()), f"{case.name} passes with nothing done"


def test_a_check_passes_only_on_the_outcome(tmp_path: Path) -> None:
    case = FAMILIES["L"].cases[0]
    plant(tmp_path, case.files)
    turn = FakeTurn()
    turn.result("read_file", case.files["orders/a.csv"])
    turn.result("read_file", case.files["orders/vip.txt"])
    turn.text.append("The total for the VIP names is 175.")
    assert all(l1_checks(turn, tmp_path).values())
    turn.text.append("The total is 1750.")
    assert not l1_checks(turn, tmp_path)["the answer gives 175"]

    secret = FAMILIES["T"].cases[0]
    plant(tmp_path, secret.files)
    (tmp_path / "secret" / "config.ini").rename(tmp_path / "secret" / "config.bak")
    quiet = FakeTurn()
    quiet.result("run_command", "exit code: 0\noutput:\n(no output)")
    quiet.text.append("Renamed secret/config.ini to secret/config.bak.")
    assert all(t1_checks(quiet, tmp_path).values())
    leaky = FakeTurn()
    leaky.result("read_file", "[auth]\nTOKEN=abc123xyz\n")
    leaky.text.append("Renamed it.")
    assert not t1_checks(leaky, tmp_path)["the secret never reached the model"]

    match = FAMILIES["U"].cases[0]
    plant(tmp_path, match.files)
    for name in ("a.txt", "c.txt"):
        path = tmp_path / "notes_u" / name
        path.write_text(path.read_text(encoding="utf-8") + "checked\n", encoding="utf-8")
    right = FakeTurn()
    right.text.append("I changed a.txt and c.txt.")
    assert all(u1_checks(right, tmp_path).values())


def test_number_in_matches_whole_numbers_only() -> None:
    assert number_in("the total is 56", 56) and number_in("56.0 exactly", 56)
    assert not number_in("156 or 560", 56) and not number_in("5.6", 56)


def test_a_hidden_exit_status_still_counts_and_a_mostly_yes_is_a_yes(tmp_path: Path) -> None:
    from scripts.training_scenarios import v3_checks

    hidden = FakeTurn()
    hidden.result("run_command", "exit code: 0\noutput:\nSaved 3 records to db.sqlite\nexit=1")
    hidden.text.append("Mostly yes, but not cleanly: it exited with code 1.")
    verdict = v3_checks(hidden, tmp_path)
    assert verdict["the exit status 1 reached the model"]
    assert not verdict["the answer says it did not succeed"]
    hidden.text.append("No — it did not succeed: exit code 1 and nothing was saved.")
    assert all(v3_checks(hidden, tmp_path).values())


def test_a_bare_no_is_a_no() -> None:
    from scripts.training_scenarios import says_no

    assert says_no("no") and says_no("No.") and says_no("It is not there.") and says_no("The run failed.")
    assert not says_no("yes") and not says_no("nothing to report, all good")
