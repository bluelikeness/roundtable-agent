"""러너 HTTP 클라이언트. 의존성 없이 urllib 로 NDJSON 을 줄 단위로 읽는다."""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from eval.cases import Case, session_overrides
from eval.trace import Trace

MCP_PORT = 9000
# 러너 전체 타임아웃(600초) + 여유
HTTP_TIMEOUT_S = 700


def read_token(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def run_request(case: Case, session_id: uuid.UUID, text: str, claude_session_id: str | None) -> dict[str, Any]:
    session: dict[str, Any] = {
        "session_id": str(session_id),
        "run_id": str(uuid.uuid4()),
        "bots": [{"name": name, "mcp_url": f"http://{svc}:{MCP_PORT}/mcp"} for name, svc in case.bots.items()],
        "model": "sonnet",
    }
    if claude_session_id:
        session["claude_session_id"] = claude_session_id
    session.update(session_overrides(case) if claude_session_id is None else {})
    return {"session": session, "text": text}


def post_run(runner: str, token: str, body: dict[str, Any]) -> list[str]:
    req = urllib.request.Request(
        f"http://{runner}:8700/runs",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Hub-Token": token},
        method="POST",
    )
    lines: list[str] = []
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        for raw in resp:
            lines.append(raw.decode("utf-8"))
    return lines


def run_case(case: Case, token: str) -> tuple[list[Trace], list[list[str]]]:
    """케이스 한 번(턴 여러 개면 --resume 으로 잇는다). 턴별 Trace 와 원본 NDJSON."""
    session_id = uuid.uuid4()
    traces: list[Trace] = []
    raws: list[list[str]] = []
    claude_sid: str | None = None
    for text in case.turns:
        body = run_request(case, session_id, text, claude_sid)
        try:
            lines = post_run(case.runner, token, body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            t = Trace(transport_error=f"{type(e).__name__}: {e}")
            traces.append(t)
            raws.append([])
            break
        t = Trace.parse(lines)
        traces.append(t)
        raws.append(lines)
        claude_sid = t.claude_session_id
        if not claude_sid:
            break
    while len(traces) < len(case.turns):  # 앞 턴이 실패하면 뒤 턴은 빈 Trace 로 채워 판정에서 실패시킨다
        traces.append(Trace(transport_error="앞 턴 실패로 실행 안 함"))
        raws.append([])
    return traces, raws
