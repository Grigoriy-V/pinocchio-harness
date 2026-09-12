"""The harness as an MCP server (roadmap 25), driven in memory.

Offline: the server is connected to a client session over memory streams,
never a process; the read tools run the repository's own scripts against a
telemetry file made here; the priced tools are refused or captured, never run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult

from app.telemetry.base import TurnRun
from app.telemetry.sqlite import SqliteTelemetry
from tools import mcp_server

GATED = {"run_scenarios", "run_turn"}
READ_ONLY = {
    "runs", "run", "harness_seconds", "thread", "scenarios", "judge_unblind", "work_log_search", "doctor",
}
ALL = GATED | READ_ONLY | {"export_trajectories", "judge_pack", "work_log_add"}


def accept(ok: bool):
    async def callback(context, params):
        return ElicitResult(action="accept", content={"ok": ok})

    return callback


async def decline(context, params):
    return ElicitResult(action="decline")


def text_of(result) -> str:
    return "\n".join(part.text for part in result.content if getattr(part, "text", None))


@pytest.fixture
def telemetry_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "telemetry.sqlite3"
    store = SqliteTelemetry(str(path))
    run = TurnRun(run_id="mcp-test-run-1", user_id="probe", thread_id="chat-a", source="test")
    store.start_turn(run)
    run.status = "completed"
    run.outcome = "answer_delivered"
    store.finish_turn(run)
    store.close()
    # The scripts open what the app would open; the environment wins over
    # `.env`, so the deployed database named there is not what they read.
    monkeypatch.setenv("AGENT_TELEMETRY", "1")
    monkeypatch.setenv("AGENT_TELEMETRY_DATABASE", str(path))
    monkeypatch.setenv("AGENT_DATABASE_URL", "")
    return path


async def test_tool_list_carries_the_contract_and_the_gate():
    async with create_connected_server_and_client_session(mcp_server.mcp) as session:
        listed = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert set(listed) == ALL
    for name, tool in listed.items():
        assert "Returns:" in (tool.description or ""), name
        assert "Leaves:" in (tool.description or ""), name
        assert tool.annotations is not None, name
        if name in GATED:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.openWorldHint is True
            assert (tool.meta or {}).get("anthropic/requiresUserInteraction") is True
        else:
            assert not (tool.meta or {}).get("anthropic/requiresUserInteraction")
        if name in READ_ONLY:
            assert tool.annotations.readOnlyHint is True


async def test_scenarios_are_listed_without_a_model():
    async with create_connected_server_and_client_session(mcp_server.mcp) as session:
        result = await session.call_tool("scenarios", {})
    assert not result.isError
    data = result.structuredContent
    assert set(data["families"]) == set("DLNTUVX")
    cases = {(c["letter"], c["index"]): c for c in data["cases"]}
    assert cases[("D", 1)]["held_out"] is True
    assert cases[("D", 1)]["sequence"] == 210
    assert any(c["interjection"] for c in data["cases"] if c["letter"] == "X")
    assert data["mini"] and data["wider"]


async def test_runs_reads_the_telemetry_the_scripts_open(telemetry_file: Path):
    async with create_connected_server_and_client_session(mcp_server.mcp) as session:
        listing = await session.call_tool("runs", {"last": 5})
        one = await session.call_tool("run", {"run_id": "mcp-test-run-1"})
    assert listing.structuredContent["ok"] is True
    assert "mcp-test-run-1" in listing.structuredContent["text"]
    assert "tools/show_run.py --last 5" in listing.structuredContent["command"]
    assert one.structuredContent["ok"] is True
    assert "mcp-test-run-1" in one.structuredContent["text"]
    assert str(telemetry_file) not in one.structuredContent["text"]


async def test_a_priced_tool_does_not_run_without_an_accepted_yes(monkeypatch: pytest.MonkeyPatch):
    async def never(*args, **kwargs):
        raise AssertionError("a script ran without permission")

    monkeypatch.setattr(mcp_server, "run_script", never)
    monkeypatch.setattr(mcp_server, "one_turn", never)

    async with create_connected_server_and_client_session(
        mcp_server.mcp, elicitation_callback=decline
    ) as session:
        declined = await session.call_tool("run_scenarios", {"letters": "D"})
        turn = await session.call_tool("run_turn", {"text": "hello"})
    assert declined.structuredContent["ok"] is False
    assert declined.structuredContent["text"].startswith("not run")
    assert turn.structuredContent["ok"] is False
    assert turn.structuredContent["reason"].startswith("not run")

    async with create_connected_server_and_client_session(
        mcp_server.mcp, elicitation_callback=accept(False)
    ) as session:
        unsaid = await session.call_tool("run_scenarios", {"letters": "D"})
    assert unsaid.structuredContent["text"] == "not run: the person did not say yes"

    # A client that cannot ask at all is a no, not a run.
    async with create_connected_server_and_client_session(mcp_server.mcp) as session:
        mute = await session.call_tool("run_scenarios", {"letters": "D"})
    assert mute.structuredContent["ok"] is False
    assert mute.structuredContent["text"].startswith("not run")


async def test_an_accepted_yes_runs_exactly_what_was_asked(monkeypatch: pytest.MonkeyPatch):
    seen: list[list[str]] = []

    async def capture(argv, *, env=None, on_line=None):
        seen.append(argv)
        if on_line is not None:
            await on_line("  PASS  an answer was given")
        return mcp_server.Output(ok=True, text="all scenarios passed", command=" ".join(argv))

    monkeypatch.setattr(mcp_server, "run_script", capture)
    async with create_connected_server_and_client_session(
        mcp_server.mcp, elicitation_callback=accept(True)
    ) as session:
        result = await session.call_tool(
            "run_scenarios", {"letters": "xvd", "model": "base", "deployed": True, "parallel": 2}
        )
    assert result.structuredContent["ok"] is True
    scenario_calls = [argv for argv in seen if argv[0] == "scripts/loop_live.py"]
    assert scenario_calls == [
        ["scripts/loop_live.py", "D", "V", "X", "--model", "base", "--deployed", "--parallel", "2"]
    ]


async def test_the_question_names_the_scope(monkeypatch: pytest.MonkeyPatch):
    questions: list[str] = []

    async def remember(context, params):
        questions.append(params.message)
        return ElicitResult(action="cancel")

    async def profile(argv, **kwargs):
        return mcp_server.Output(ok=True, text="grigoriy98smile", command="")

    monkeypatch.setattr(mcp_server, "run_script", profile)
    async with create_connected_server_and_client_session(
        mcp_server.mcp, elicitation_callback=remember
    ) as session:
        await session.call_tool("run_scenarios", {"letters": "DVX", "model": "base", "deployed": True})
        await session.call_tool("run_turn", {"text": "what time is it", "model": "or"})
    assert "DVX" in questions[0] and "base" in questions[0]
    assert "workspace grigoriy98smile" in questions[0]
    assert "Price" in questions[0]
    assert "what time is it" in questions[1] and "or" in questions[1]


async def test_the_protocol_owns_stdout_and_prints_go_to_stderr(monkeypatch: pytest.MonkeyPatch, capsys):
    import io
    import sys

    real = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", real)
    protocol = mcp_server.claim_stdout()
    assert sys.stdout is sys.stderr
    print("a trace line the harness logs")
    await protocol.write("protocol\n")
    await protocol.flush()
    real.buffer.seek(0)
    # The newline as the SDK's own wrapper writes it on this platform.
    assert real.buffer.read().rstrip() == b"protocol"
    assert "a trace line" in capsys.readouterr().err


async def test_a_deployed_turn_goes_through_the_ask_function(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, str]] = []

    def fake_deployed(text: str, model: str):
        seen.append((text, model))
        return mcp_server.TurnResult(ok=True, answer="12:34", run_id="deployed-ask-x-1")

    async def never(*args, **kwargs):
        raise AssertionError("the local room ran for a deployed turn")

    async def profile(argv, **kwargs):
        return mcp_server.Output(ok=True, text="grigoriy98smile", command="")

    monkeypatch.setattr(mcp_server, "deployed_turn", fake_deployed)
    monkeypatch.setattr(mcp_server, "one_turn", never)
    monkeypatch.setattr(mcp_server, "run_script", profile)
    questions: list[str] = []

    async def remember_and_accept(context, params):
        questions.append(params.message)
        return ElicitResult(action="accept", content={"ok": True})

    async with create_connected_server_and_client_session(
        mcp_server.mcp, elicitation_callback=remember_and_accept
    ) as session:
        result = await session.call_tool("run_turn", {"text": "what time is it", "model": "or", "deployed": True})
    assert seen == [("what time is it", "or")]
    assert result.structuredContent["run_id"] == "deployed-ask-x-1"
    assert "deployed on Modal workspace grigoriy98smile" in questions[0]
    assert "control container" in questions[0]
