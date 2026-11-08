"""평가 하네스(runner/eval) 판정 로직 단위 테스트. 실제 claude·Mock 없이 합성 NDJSON 으로 돈다."""

import json
import uuid

from eval.cases import CASES_BY_ID, judge
from eval.client import run_request
from eval.report import RunResult, render
from eval.trace import Trace

RUN = str(uuid.uuid4())


def _ev(typ: str, **payload) -> str:
    return json.dumps({"event": {"type": typ, "session_id": None, "seq": None, "ts": "2026-11-08T00:00:00Z",
                                 "payload": {"run_id": RUN, **payload}}}, ensure_ascii=False)


def _tc(cid: str, bot: str, tool: str, *, done: bool = False, text: str | None = None, output: dict | None = None) -> str:
    inp = {"text": text, "thread_id": "s"} if text is not None else {}
    return json.dumps({"tool_call": {"id": cid, "run_id": RUN, "bot": bot, "tool": tool, "input": inp,
                                     "done": done, "output": output}}, ensure_ascii=False)


def _ok(raw: str, **structured) -> dict:
    return {"bot": "x", "ok": True, "raw": raw, "structured": structured, "needs_input": False}


def _trace(*lines: str, final: str = "결론입니다.", sid: str = "cs-1") -> Trace:
    return Trace.parse([_ev("run.started", claude_session_id=sid), *lines,
                        _ev("claude.final", text=final, message_id=str(uuid.uuid4()), usage={"num_turns": 3}),
                        _ev("run.finished", status="done")])


def test_parallel_and_sequential_detection():
    par = _trace(
        _tc("1", "mock-a", "ask", text="a"), _tc("2", "mock-b", "ask", text="b"),
        _tc("1", "mock-a", "ask", done=True, output=_ok("x")), _tc("2", "mock-b", "ask", done=True, output=_ok("y")),
    )
    assert par.parallel(par.asks())
    seq = _trace(
        _tc("1", "mock-a", "ask", text="a"), _tc("1", "mock-a", "ask", done=True, output=_ok("x")),
        _tc("2", "mock-b", "ask", text="b"), _tc("2", "mock-b", "ask", done=True, output=_ok("y")),
    )
    assert not seq.parallel(seq.asks())
    assert seq.sequential(seq.asks()[0], seq.asks()[1])


def test_t2_single_line_limit():
    lines = (_tc("1", "mock-a", "ask", text="강남"), _tc("1", "mock-a", "ask", done=True, output=_ok("한옥집")))
    assert judge(CASES_BY_ID["T2"], [_trace(*lines, final="한옥집이 가능합니다.\n주의: 없음\n진행할 곳을 골라 주세요.")]).passed
    long = "\n".join(f"줄 {i}" for i in range(6))
    c = judge(CASES_BY_ID["T2"], [_trace(*lines, final=long)])
    assert c.failed() == ["정리 5줄 이내"]


def test_t4_relay_rejects_whole_raw():
    raw = "강남 한식집 3곳 찾았어요. 1) 한옥집 2) 밥상"
    t = _trace(
        _tc("1", "mock-a", "ask", text="강남 한식"), _tc("1", "mock-a", "ask", done=True, output=_ok(raw)),
        _ev("claude.note", text="릴레이합니다"),
        _tc("2", "mock-b", "ask", text=f"다른 비서가 찾은 결과야: {raw}. 근처 호텔 찾아줘"),
        _tc("2", "mock-b", "ask", done=True, output=_ok("호텔A")),
    )
    assert "b 입력에 a 원문 통째 아님" in judge(CASES_BY_ID["T4"], [t]).failed()


def test_t9_needs_resume_and_tap():
    first = _trace(_tc("1", "mock-a", "ask", text="예약"), _tc("1", "mock-a", "ask", done=True,
                   output=_ok("진행할까요?", confirmation_required={"summary": "s", "action_id": "btn_confirm_1"})),
                   final="확정 내용: 20,000원\n진행할까요?")
    second = _trace(_tc("2", "mock-a", "ask", text="[tap:btn_confirm_1]"),
                    _tc("2", "mock-a", "ask", done=True, output=_ok("확정")), final="예약이 확정됐습니다.")
    assert judge(CASES_BY_ID["T9"], [first, second]).passed
    other = _trace(_tc("2", "mock-a", "ask", text="[tap:btn_confirm_1]"), final="예약이 확정됐습니다.", sid="cs-2")
    assert "2턴: --resume 로 이어짐" in judge(CASES_BY_ID["T9"], [first, other]).failed()


def test_t14_request_injects_split_session():
    body = run_request(CASES_BY_ID["T14"], uuid.uuid4(), "아까 그 식당 예약해", None)
    s = body["session"]
    assert s["claude_session_id"] and s["last_message_at"] and "한옥집" in s["last_final"]
    assert {b["name"]: b["mcp_url"] for b in s["bots"]}["mock-a"] == "http://a-confirm:9000/mcp"
    # 두 번째 턴(resume)에는 분할 재료를 다시 넣지 않는다
    assert "last_message_at" not in run_request(CASES_BY_ID["T9"], uuid.uuid4(), "진행해", "cs-1")["session"]


def test_report_renders_observations():
    case = CASES_BY_ID["T3"]
    t = _trace(
        _tc("s1", "mock-a", "status"), _tc("s1", "mock-a", "status", done=True, output={"available": True}),
        _tc("1", "mock-a", "ask", text="a"), _tc("2", "mock-b", "ask", text="b"),
        _tc("1", "mock-a", "ask", done=True, output=_ok("x")), _tc("2", "mock-b", "ask", done=True, output=_ok("y")),
    )
    r = RunResult(case=case, index=1, traces=[t], checks=judge(case, [t]), seconds=1.0)
    md = render([case], [r], n=1, pass_runs=4, pass_cases=12, stamp="test", elapsed_s=1.0)
    assert "| T3 | 팬아웃 3봇 | 0/1 | ❌" in md
    assert "**병렬 툴 호출**" in md and "1/1" in md
