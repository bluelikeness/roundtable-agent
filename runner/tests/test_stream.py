"""stream-json 고정 샘플 → 허브 이벤트 변환 (02 4장 변환 표)."""

import uuid
from datetime import UTC, datetime, timedelta

from runner.shared import ToolCallRecord
from runner.stream import StreamParser
from tests.conftest import FIXTURES, SESSION_ID

RUN_ID = uuid.UUID("99999999-0000-0000-0000-000000000001")


def _parser() -> StreamParser:
    t = [datetime(2026, 11, 8, tzinfo=UTC)]

    def now():
        t[0] += timedelta(milliseconds=100)
        return t[0]

    return StreamParser(session_id=SESSION_ID, run_id=RUN_ID, now=now)


def _feed(name: str) -> tuple[StreamParser, list]:
    p = _parser()
    out = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        out += p.feed(line)
    return p, out


def _events(items):
    return [i for i in items if not isinstance(i, ToolCallRecord)]


def test_fanout_event_sequence():
    p, items = _feed("fanout.jsonl")
    types = [e.type for e in _events(items)]
    assert types == [
        "run.started",
        "claude.note",  # 첫 text 는 tool_use 앞에서 note 로 나간다
        "bot.pending",  # mock-a ask
        "bot.pending",  # mock-b ask (status 호출은 이벤트가 없다)
        "bot.reply",
        "bot.reply",
        "claude.final",  # 마지막 text 는 note 가 아니라 final 하나로
        "run.finished",
    ]
    assert p.finished and p.claude_session_id == "cs-abc"


def test_envelope_fields_left_for_hub():
    _, items = _feed("fanout.jsonl")
    for e in _events(items):
        assert e.session_id == SESSION_ID
        assert e.seq is None  # 허브가 매긴다
        assert e.payload.run_id == RUN_ID


def test_payloads():
    _, items = _feed("fanout.jsonl")
    ev: dict[str, list] = {}
    for e in _events(items):
        ev.setdefault(e.type, []).append(e.payload)
    assert ev["run.started"][0].claude_session_id == "cs-abc"
    assert ev["claude.note"][0].text == "두 봇에 날씨를 물어봅니다."
    a, b = ev["bot.pending"]
    assert (a.bot, a.tool_call_id, a.queue_position) == ("mock-a", "toolu_a1", 0)
    assert (b.bot, b.tool_call_id) == ("mock-b", "toolu_b1")
    ra, rb = ev["bot.reply"]
    assert ra.ok and ra.raw == "내일 서울 맑음, 최고 14도" and ra.latency_ms == 2300 and ra.error is None
    assert ra.structured == {}
    assert not rb.ok and rb.error == "timeout" and rb.raw == ""
    assert ra.message_id != rb.message_id
    final = ev["claude.final"][0]
    assert final.text.startswith("내일 서울은 맑고")
    assert final.usage["input_tokens"] == 1200 and final.usage["total_cost_usd"] == 0.0712
    assert final.usage["num_turns"] == 4
    assert ev["run.finished"][0].status == "done"


def test_tool_call_records_include_status():
    _, items = _feed("fanout.jsonl")
    recs = [i for i in items if isinstance(i, ToolCallRecord)]
    started = {(r.id, r.tool) for r in recs if not r.done}
    done = {r.id: r for r in recs if r.done}
    assert started == {("toolu_s1", "status"), ("toolu_a1", "ask"), ("toolu_b1", "ask")}
    assert done["toolu_s1"].output["available"] is True
    assert done["toolu_a1"].output["raw"] == "내일 서울 맑음, 최고 14도"
    assert done["toolu_b1"].error == "timeout"
    first = next(r for r in recs if r.id == "toolu_a1")
    assert first.input == {"text": "내일 서울 날씨", "thread_id": str(SESSION_ID)}


def test_record_precedes_pending_event():
    """허브가 bot.pending 을 받을 때 tool_call 행이 이미 있도록 기록이 먼저 나온다."""
    _, items = _feed("fanout.jsonl")
    rec_at = next(n for n, i in enumerate(items) if isinstance(i, ToolCallRecord) and i.id == "toolu_a1")
    pending_at = next(
        n for n, i in enumerate(items)
        if not isinstance(i, ToolCallRecord) and i.type == "bot.pending" and i.payload.tool_call_id == "toolu_a1"
    )
    assert rec_at < pending_at


def test_error_result_and_tool_error():
    p, items = _feed("max_turns.jsonl")
    evs = _events(items)
    assert [e.type for e in evs] == ["run.started", "claude.note", "bot.pending", "bot.reply", "run.finished"]
    reply = evs[3].payload
    assert not reply.ok and reply.error == "internal" and reply.raw == ""
    fin = evs[-1].payload
    assert fin.status == "failed" and fin.error == "error_max_turns"
    assert p.finished


def test_success_subtype_with_is_error_is_failure():
    """미로그인 등: result 가 subtype=success 지만 is_error=true. 문구는 note 로 남긴다."""
    p = _parser()
    p.feed('{"type":"system","subtype":"init","session_id":"s"}')
    p.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"Not logged in"}]}}')
    evs = _events(p.feed('{"type":"result","subtype":"success","is_error":true,"result":"Not logged in"}'))
    assert [e.type for e in evs] == ["claude.note", "run.finished"]
    assert evs[0].payload.text == "Not logged in"
    assert (evs[1].payload.status, evs[1].payload.error) == ("failed", "claude_error")


def test_finish_closes_pending_bots_on_timeout():
    p = _parser()
    lines = (FIXTURES / "fanout.jsonl").read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[:6]:  # mock-a·mock-b ask 가 나간 직후 끊김
        out += p.feed(line)
    out += p.finish("timeout", "timeout")
    evs = _events(out)
    replies = [e.payload for e in evs if e.type == "bot.reply"]
    assert {r.bot for r in replies} == {"mock-a", "mock-b"}
    assert all(not r.ok and r.error == "timeout" for r in replies)
    assert evs[-1].type == "run.finished" and evs[-1].payload.status == "timeout"
    assert p.finish("failed", "x") == []  # 두 번 닫지 않는다


def test_garbage_and_unknown_lines_ignored():
    p = _parser()
    assert p.feed("not json") == []
    assert p.feed("[1,2]") == []
    assert p.feed('{"type":"stream_event"}') == []
    bash = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t","name":"Bash","input":{}}]}}'
    assert p.feed(bash) == []


def test_web_search_shows_query_as_note():
    p = _parser()
    p.feed('{"type":"system","subtype":"init","session_id":"s"}')
    out = p.feed(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"최신 소식을 찾아봅니다."},'
        '{"type":"tool_use","id":"w1","name":"WebSearch","input":{"query":"서울 내일 날씨"}}]}}'
    )
    assert [e.type for e in out] == ["claude.note", "claude.note"]
    assert out[1].payload.text == "웹 검색: 서울 내일 날씨"
    # 검색 결과는 봇 답이 아니므로 이벤트가 없다
    res = '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"w1","content":"결과"}]}}'
    assert p.feed(res) == []


def test_unparseable_ask_result_is_internal_error():
    p = _parser()
    p.feed('{"type":"system","subtype":"init","session_id":"s"}')
    p.feed('{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"mcp__mock-a__ask","input":{}}]}}')
    out = _events(
        p.feed('{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"plain text"}]}}')
    )
    assert out[0].payload.ok is False and out[0].payload.error == "internal"


def test_queue_position_counts_open_asks_per_bot():
    p = _parser()
    p.feed('{"type":"system","subtype":"init","session_id":"s"}')
    out = _events(
        p.feed(
            '{"type":"assistant","message":{"content":['
            '{"type":"tool_use","id":"t1","name":"mcp__mock-a__ask","input":{}},'
            '{"type":"tool_use","id":"t2","name":"mcp__mock-a__ask","input":{}},'
            '{"type":"tool_use","id":"t3","name":"mcp__mock-b__ask","input":{}}]}}'
        )
    )
    assert [(e.payload.bot, e.payload.queue_position) for e in out] == [("mock-a", 0), ("mock-a", 1), ("mock-b", 0)]


def test_real_claude_2_1_310_sample():
    """실제 claude 2.1.310 출력(계정 정보를 지운 사본). Mock 이 되묻고 선택지를 준 경우.

    ui_invalidate·thinking_tokens·rate_limit_event·thinking 블록은 건너뛴다.
    """
    p, items = _feed("real_needs_input.jsonl")
    evs = _events(items)
    assert [e.type for e in evs] == [
        "run.started", "claude.note", "bot.pending", "bot.reply", "claude.final", "run.finished",
    ]
    assert p.claude_session_id == "cs-real"
    reply = evs[3].payload
    assert reply.bot == "mock-a" and reply.ok and reply.needs_input and reply.raw == "몇 시로 할까요?"
    assert reply.structured["choices"][:1] == ["18:00"]
    assert evs[4].payload.text.startswith("mock-a에게 물어봤는데")
    assert evs[4].payload.usage["total_cost_usd"] == 0.0181859
    recs = [i for i in items if isinstance(i, ToolCallRecord)]
    assert {(r.tool, r.done) for r in recs} == {("status", False), ("status", True), ("ask", False), ("ask", True)}
