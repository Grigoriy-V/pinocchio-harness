"""The scenario runner's own parts, without running a scenario.

Nothing here reaches a model, a network service or a real workspace: what is
tested is the selection, the seal, the seeding and the report. Whether the
agent behaves is what the instrument measures, and that measurement is a live
run with its own human gate.
"""

from __future__ import annotations

from tools.prompt_scenarios import (
    SCENARIOS,
    Result,
    Scenario,
    identity,
    plant,
    render,
    sealed,
    select,
)


def test_the_regression_is_a_scenario() -> None:
    """The request that regressed live is in the fixed list, or the instrument
    measures everything except the thing it was built for."""

    castle = next(scenario for scenario in SCENARIOS if scenario.name == "castle")
    assert "write_file" in castle.expected_tools
    assert "use_page" in castle.expected_tools


def test_a_conversational_scenario_expects_no_tools() -> None:
    chat = next(scenario for scenario in SCENARIOS if scenario.name == "chat")
    assert chat.expected_tools == ()


def test_a_third_party_scenario_is_left_out_unless_asked_for() -> None:
    names = {scenario.name for scenario in select()}
    assert "web" not in names
    assert "web" in {scenario.name for scenario in select(external=True)}


def test_only_selects_by_name() -> None:
    assert [scenario.name for scenario in select(["castle"])] == ["castle"]


def test_the_seal_clears_a_deployed_database(monkeypatch, tmp_path) -> None:
    """Even when the environment points at one."""

    monkeypatch.setenv("AGENT_DATABASE_URL", "postgresql://example/db")
    settings = sealed(tmp_path)
    assert settings.database_url == ""
    assert settings.alt_database_url == ""
    assert str(tmp_path) in settings.database
    assert str(tmp_path) in settings.workspace


def test_a_scenario_starts_from_the_workspace_it_declares(tmp_path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "left-over.txt").write_text("from an earlier scenario", encoding="utf-8")

    plant(workspace, Scenario(name="s", request="r", seed=(("page.html", "<p>hi</p>"),)))

    assert not (workspace / "left-over.txt").exists()
    assert (workspace / "page.html").read_text(encoding="utf-8") == "<p>hi</p>"


def test_expected_tools_are_met_when_they_appear() -> None:
    scenario = Scenario(name="s", request="r", expected_tools=("write_file",))
    result = Result(scenario=scenario, run_id="r1")
    assert not result.met
    result.tools = [{"tool": "write_file", "status": "success", "path": "a.html"}]
    assert result.met


def test_a_conversational_turn_that_spends_a_tool_is_off_shape() -> None:
    result = Result(scenario=Scenario(name="s", request="r"), run_id="r1")
    assert result.met
    result.tools = [{"tool": "list_files", "status": "success"}]
    assert not result.met


def test_one_prompt_has_one_identity() -> None:
    assert identity("a prompt") == identity("a prompt")
    assert identity("a prompt") != identity("a prompt ")


def test_the_report_carries_the_answer_and_the_calls() -> None:
    scenario = Scenario(
        name="castle",
        request="Создай HTML с замком.",
        expected_tools=("write_file",),
        look_for="сделал ли он файл",
    )
    result = Result(
        scenario=scenario,
        run_id="r1",
        model_calls=2,
        input_tokens=1200,
        output_tokens=300,
        total_ms=8820,
        tools=[
            {"tool": "write_file", "status": "success", "path": "castle.html", "duration_ms": 4}
        ],
        answer="Готово, файл castle.html.",
        outcome="answer_delivered",
        derived_usd=0.0094,
    )

    report = render({"label": "current", "revision": "abc1234"}, [result])

    assert "# Prompt scenarios — current" in report
    assert "abc1234" in report
    assert "Готово, файл castle.html." in report
    assert "write_file castle.html success" in report
    assert "сделал ли он файл" in report
    assert "$0.0094" in report


def test_a_failed_scenario_is_a_row_rather_than_a_missing_one() -> None:
    result = Result(
        scenario=Scenario(name="chat", request="r"),
        run_id="r1",
        outcome="failed",
        error="BackendError: refused",
    )

    report = render({"label": "current"}, [result])

    assert "BackendError: refused" in report
    assert "(no text)" in report
