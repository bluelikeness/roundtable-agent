"""ClaudeCodeRunner: 가짜 claude 로 spawn·인자·타임아웃·중단·분할·재시도를 확인한다."""

import asyncio
import json
import sys

import pytest

from runner.shared import ToolCallRecord
from runner.spawn import ClaudeCodeRunner
from tests.conftest import FIXTURES, SESSION_ID, TOKEN, TESTS, make_session


def _calls(log) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


async def _run(runner, session, text="내일 날씨 알려줘"):
    return [ev async for ev in runner.start(session, text)]


async def test_first_run_args_and_events(settings, claude_log, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(FIXTURES / "fanout.jsonl"))
    records: list[ToolCallRecord] = []

    async def sink(r):
        records.append(r)

    runner = ClaudeCodeRunner(settings, on_tool_call=sink)
    session = make_session()
    events = await _run(runner, session, "--help 같은 메시지")

    assert [e.type for e in events][0] == "run.started"
    assert events[-1].type == "run.finished" and events[-1].payload.status == "done"
    assert len(records) == 6  # 시작 3 + 결과 3

    (call,) = _calls(claude_log)
    argv = call["argv"]
    assert argv[0] == "-p" and "--resume" not in argv
    assert call["stdin"] == "--help 같은 메시지"  # 프롬프트는 stdin
    for flag, value in [
        ("--output-format", "stream-json"),
        ("--model", "sonnet"),
        ("--disallowedTools", "Bash,Read,Write,Edit,WebSearch,WebFetch"),
        ("--allowedTools", "mcp__mock-a__ask,mcp__mock-a__status,mcp__mock-b__ask,mcp__mock-b__status"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    assert "--verbose" in argv and "--strict-mcp-config" in argv
    assert "--max-turns" not in argv  # 턴 상한 없음이 기본
    assert argv[argv.index("--tools") + 1] == ""  # 내장 툴 없음
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
    assert call["tool_search"] == "false"
    assert set(call["mcp"]["mcpServers"]) == {"mock-a", "mock-b"}
    assert {m["timeout"] for m in call["mcp"]["mcpServers"].values()} == {270_000}
    # 토큰은 파일이 아니라 환경변수로만
    assert TOKEN not in json.dumps(call["mcp"])
    assert call["token"] == TOKEN
    assert f"`thread_id`는 항상 `{SESSION_ID}`." in call["prompt"]
    assert "{{" not in call["prompt"]
    assert call["cwd"] == str(settings.work_dir)
    # 실행별 디렉터리는 끝나면 지운다
    assert not (settings.runs_dir / str(session.run_id)).exists()


async def test_resume_passes_session_id(settings, claude_log, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(FIXTURES / "fanout.jsonl"))
    events = await _run(ClaudeCodeRunner(settings), make_session(claude_session_id="cs-abc", tool_calls=3))
    argv = _calls(claude_log)[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "cs-abc"
    assert "session.system" not in [e.type for e in events]


async def test_split_injects_summary(settings, claude_log, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(FIXTURES / "fanout.jsonl"))
    session = make_session(claude_session_id="old", tool_calls=21, last_final="이전 정리", last_bot_raws={"mock-a": "맑음"})
    events = await _run(ClaudeCodeRunner(settings), session, "이어서")
    assert events[0].type == "session.system" and "봇 호출 20건 초과" in events[0].payload.text
    call = _calls(claude_log)[0]
    assert "--resume" not in call["argv"]
    assert call["stdin"].startswith("<session_summary>") and call["stdin"].endswith("</session_summary>\n\n이어서")


async def test_missing_resume_session_retries_fresh(settings, claude_log, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(FIXTURES / "fanout.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_RESUME_MISSING", "1")
    events = await _run(ClaudeCodeRunner(settings), make_session(claude_session_id="gone", last_final="이전 정리"))
    calls = _calls(claude_log)
    assert len(calls) == 2 and "--resume" in calls[0]["argv"] and "--resume" not in calls[1]["argv"]
    assert calls[1]["stdin"].startswith("<session_summary>")
    assert events[0].type == "session.system"
    assert events[-1].payload.status == "done"


async def test_timeout_terminates_and_closes_pending(settings, monkeypatch, tmp_path):
    partial = tmp_path / "partial.jsonl"
    lines = (FIXTURES / "fanout.jsonl").read_text(encoding="utf-8").splitlines()
    partial.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")  # mock-a ask 직후
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(partial))
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "60")
    runner = ClaudeCodeRunner(settings.model_copy(update={"timeout_s": 1.5}))
    started = asyncio.get_running_loop().time()
    events = await _run(runner, make_session())
    assert asyncio.get_running_loop().time() - started < 15
    assert [e.type for e in events][-2:] == ["bot.reply", "run.finished"]
    assert events[-2].payload.error == "timeout"
    assert events[-1].payload.status == "timeout"


async def test_stop_sends_sigterm(settings, monkeypatch, tmp_path):
    partial = tmp_path / "partial.jsonl"
    partial.write_text((FIXTURES / "fanout.jsonl").read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(partial))
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "60")
    runner = ClaudeCodeRunner(settings)
    session = make_session()
    events = []

    async def consume():
        async for ev in runner.start(session, "x"):
            events.append(ev)
            if ev.type == "run.started":
                assert await runner.stop(session.run_id)

    await asyncio.wait_for(consume(), timeout=15)
    assert events[-1].type == "run.finished"
    assert (events[-1].payload.status, events[-1].payload.error) == ("failed", "stopped")
    assert not await runner.stop(session.run_id)  # 끝난 run 은 없다


async def test_nonzero_exit_without_result(settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", "Invalid API key")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    events = await _run(ClaudeCodeRunner(settings), make_session())
    assert [e.type for e in events] == ["run.finished"]
    assert (events[0].payload.status, events[0].payload.error) == ("failed", "exit_1")


async def test_spawn_failure(settings):
    runner = ClaudeCodeRunner(settings.model_copy(update={"claude_bin": [str(TESTS / "no-such-claude")]}))
    events = await _run(runner, make_session())
    assert events[-1].payload.error == "spawn_failed"


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM 무시 프로세스는 POSIX 에서만 시험")
async def test_kill_after_grace(settings, monkeypatch, tmp_path):
    stubborn = tmp_path / "stubborn.py"
    stubborn.write_text(
        "import signal,sys,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdout.write('{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"s\"}\\n');sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    runner = ClaudeCodeRunner(settings.model_copy(update={"claude_bin": [sys.executable, str(stubborn)], "timeout_s": 1, "term_grace_s": 1}))
    events = await asyncio.wait_for(_run(runner, make_session()), timeout=15)
    assert events[-1].payload.status == "timeout"
