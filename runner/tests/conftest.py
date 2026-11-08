"""러너 테스트 공통: 가짜 claude 로 ClaudeCodeRunner 를 돌린다."""

import sys
import uuid
from pathlib import Path

import pytest

from runner.config import RunnerSettings
from runner.shared import BotEndpoint, RunnerSession

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
SESSION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
TOKEN = "test-token-not-a-secret"
FAKE_ENV = ("FAKE_CLAUDE_SCRIPT", "FAKE_CLAUDE_SLEEP", "FAKE_CLAUDE_STDERR", "FAKE_CLAUDE_EXIT", "FAKE_CLAUDE_RESUME_MISSING")


@pytest.fixture
def claude_log(tmp_path, monkeypatch) -> Path:
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    for k in FAKE_ENV:
        monkeypatch.delenv(k, raising=False)
    return log


@pytest.fixture
def settings(tmp_path, claude_log) -> RunnerSettings:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    return RunnerSettings(
        claude_bin=[sys.executable, str(TESTS / "fake_claude.py")],
        work_dir=tmp_path / "work",
        runs_dir=tmp_path / "runs",
        token_file=token_file,
        timeout_s=20,
        term_grace_s=2,
    )


def make_session(**kw) -> RunnerSession:
    base = dict(
        session_id=SESSION_ID,
        run_id=uuid.uuid4(),
        bots=[
            BotEndpoint(name="mock-a", mcp_url="http://mock-a:9000/mcp"),
            BotEndpoint(name="mock-b", mcp_url="http://mock-b:9000/mcp"),
        ],
    )
    base.update(kw)
    return RunnerSession(**base)
