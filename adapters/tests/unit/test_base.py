"""베이스 클래스 단위 테스트. compose 없이 프로세스 안에서 빠르게 돈다."""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from adapters.base import AdapterConfig, BotAdapter, CodeLogin, RawMessage, Structured, Thread
from adapters.mock.adapter import MockAdapter


def cfg(**over) -> AdapterConfig:
    data = {
        "name": "mock-t",
        "description": "단위 테스트용",
        "timeouts": {"quiet_s": 0.1},
        "min_interval_s": 0,
        "mock": {"latency_s": {"min": 0.05, "max": 0.05}, "replies": {"식당": "OO식당 [버튼: 예약]"}},
    }
    data.update(over)
    return AdapterConfig.model_validate(data)


@pytest.fixture
async def mock(tmp_path):
    a = MockAdapter(cfg(), tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    return a


async def test_bad_input_is_internal_not_exception(mock):
    for args in (
        dict(text=None, thread_id="t"),
        dict(text="x" * 4001, thread_id="t"),
        dict(text="hi", thread_id=""),
        dict(text="hi", thread_id="t", timeout_s=9),
        dict(text="hi", thread_id="t", timeout_s=241),
        dict(text="hi", thread_id="t", timeout_s=True),
        dict(text="hi", thread_id="t", reset="yes"),
    ):
        r = await mock.ask(**args)
        assert r.ok is False and r.error == "internal", args


async def test_ok_and_needs_input(mock):
    r = await mock.ask("[mock:ok] 식당", "t1")
    assert r.ok and r.raw == "OO식당 [버튼: 예약]"
    assert r.structured["buttons"] == [{"label": "예약", "action_id": "btn_1", "kind": "callback"}]
    assert r.needs_input is True
    r = await mock.ask("[mock:ok] 안녕", "t1")
    assert r.ok and r.raw == "echo: 안녕" and r.structured == {} and r.needs_input is False
    r = await mock.ask("[mock:ask] 예약", "t1")
    assert r.needs_input is True and r.structured["choices"]


async def test_tap(mock):
    await mock.ask("[mock:ok] 식당", "t1")
    r = await mock.ask("[tap:btn_1] 2명", "t1")
    assert r.ok and r.raw == "'예약' 선택하셨습니다. 2명"
    # action_id 는 직전 응답에서만 유효
    r = await mock.ask("[tap:btn_1]", "t1")
    assert r.ok is False and r.error == "internal"


async def test_hook_exception_becomes_internal(mock):
    async def boom(*a, **k):
        raise RuntimeError("boom")

    mock._send = boom
    r = await mock.ask("[mock:ok] hi", "t1")
    assert r.ok is False and r.error == "internal"
    assert "boom" in mock.logs[-1]["detail"]


async def test_login_required_and_recovery(mock):
    mock._session_path.unlink()
    r = await mock.ask("[mock:ok] hi", "t1")
    assert r.error == "login_required"
    st = await mock.status()
    assert st.available is False and st.reason == "login_required"
    flow = await mock.login_start()
    assert flow["flow"] == "code"
    assert await mock.login_submit({"code": "1"}) == {"ok": True}
    assert (await mock.status()).available is True


async def test_unavailable_after(tmp_path):
    c = cfg()
    c.model_extra["mock"]["unavailable_after"] = 3
    a = MockAdapter(c, tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    assert (await a.ask("[mock:fast] 1", "t")).ok
    assert (await a.ask("[mock:fast] 2", "t")).ok
    assert (await a.ask("[mock:fast] 3", "t")).error == "login_required"


async def test_queue_full_is_rate_limited(tmp_path):
    a = MockAdapter(cfg(queue={"max_depth": 1}), tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    tasks = [asyncio.create_task(a.ask("[mock:ok] q", "t")) for _ in range(3)]
    res = await asyncio.gather(*tasks)
    assert sorted(r.error or "ok" for r in res) == ["ok", "ok", "rate_limited"]


async def test_queue_wait_exceeded_sets_cooldown(tmp_path):
    a = MockAdapter(cfg(queue={"max_wait_s": 0.2, "cooldown_s": 30}), tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    first = asyncio.create_task(a.ask("[mock:slow] 막기", "t1", timeout_s=10))
    await asyncio.sleep(0.05)
    r = await a.ask("[mock:ok] 대기", "t2")
    assert r.error == "rate_limited"
    st = await a.status()
    assert st.reason == "cooldown" and st.available is False
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first


class ProgressAdapter(BotAdapter):
    """진행 메시지만 보내다 늦게 답하는 봇. 진행 메시지가 오면 재전송하지 않아야 한다."""

    progress_patterns = ["^찾는 중"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sends = 0
        self.t_send = 0.0

    async def _ensure_ready(self):
        return None

    async def _send(self, thread: Thread, text: str, tap):
        self.sends += 1
        self.t_send = time.monotonic()

    async def _collect(self, thread: Thread, deadline: float):
        dt = time.monotonic() - self.t_send
        out = [RawMessage(id="p", text="찾는 중…")]
        if dt > 0.7:  # 1회차 대기(잔여의 절반 ≈ 0.5초)를 넘겨서 답한다
            out.append(RawMessage(id="r", text="찾았습니다"))
        return out

    async def _extract(self, msgs):
        return "\n\n".join(m.text for m in msgs), Structured()

    async def _login_flow(self):
        return CodeLogin(hint="", submit=None)


async def test_progress_messages_are_ignored_and_not_retried(tmp_path, monkeypatch):
    # 계약상 timeout_s 하한은 10초지만 단위 테스트는 시간을 줄여 같은 동작을 본다
    monkeypatch.setattr("adapters.base.TIMEOUT_MIN", 1)
    a = ProgressAdapter(AdapterConfig(name="p", description="d", timeouts={"quiet_s": 0.1}), tmp_path)
    a.poll_s = 0.05
    r = await a.ask("찾아줘", "t", timeout_s=1)
    assert r.ok, r
    assert r.raw == "찾았습니다"
    assert a.sends == 1


async def test_threads_persist(tmp_path):
    a = MockAdapter(cfg(), tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    await a.ask("[mock:ok] 식당", "keep")
    b = MockAdapter(cfg(), tmp_path)
    assert b.threads["keep"].message_count == 2
    assert b.threads["keep"].last_action_ids == ["btn_1"]
    r = await b.ask("[tap:btn_1]", "keep")
    assert r.ok and "예약" in r.raw


async def test_stuck_extract_is_bounded_by_timeout(mock, monkeypatch):
    """_extract 가 멈춰도 timeout_s 안에 error=timeout 으로 끝나고 락을 놓는다."""
    monkeypatch.setattr("adapters.base.TIMEOUT_MIN", 1)

    async def stuck(msgs):
        await asyncio.sleep(60)

    orig = mock._extract
    mock._extract = stuck
    t0 = time.monotonic()
    r = await mock.ask("[mock:ok] 멈춤", "t1", timeout_s=1)
    assert r.ok is False and r.error == "timeout"
    assert time.monotonic() - t0 < 1.5
    mock._extract = orig
    assert (await mock.ask("[mock:ok] 다음", "t1")).ok


async def _scripted(tmp_path, **mock_over):
    base = {"latency_s": {"min": 0.05, "max": 0.05}, "replies": {"식당": "한옥집 19:00 가능"}}
    base.update(mock_over)
    a = MockAdapter(cfg(mock=base), tmp_path)
    a.poll_s = 0.02
    await a._on_start()
    return a


async def test_scripted_ask_back_once_per_thread(tmp_path):
    # 평가용: 스레드마다 처음 한 번만 되묻고 이후에는 일반 응답
    a = await _scripted(tmp_path, ask_back={"text": "몇 명이세요?", "times": 1})
    r = await a.ask("강남 식당", "t1")
    assert r.ok and r.needs_input and r.raw == "몇 명이세요?"
    r = await a.ask("2명", "t1")
    assert r.ok and r.raw == "echo: 2명"
    r = await a.ask("식당", "t2")
    assert r.raw == "몇 명이세요?"


async def test_scripted_confirm_and_tap(tmp_path):
    a = await _scripted(
        tmp_path,
        confirm={"text": "한옥집 19:00 2명 예약금 20,000원. 진행할까요?", "summary": "한옥집 19:00 2명 20,000원",
                 "action_id": "btn_confirm_1", "done_text": "예약 완료"},
    )
    r = await a.ask("한옥집 예약", "t1")
    assert r.structured["confirmation_required"] == {"summary": "한옥집 19:00 2명 20,000원", "action_id": "btn_confirm_1"}
    r = await a.ask("[tap:btn_confirm_1]", "t1")
    assert r.ok and r.raw == "예약 완료" and "confirmation_required" not in r.structured


def test_ask_description_marks_any_file_type():
    """accepts_files 에 "*" 가 있으면 모든 종류를 받는다고 적고, 앱이 받지 않으면 unsupported 라고 알린다."""
    from types import SimpleNamespace

    from adapters.mcp_server import ask_description

    any_kind = ask_description(SimpleNamespace(description="봇", accepts_files=("*",)))
    assert "모든 종류" in any_kind and "unsupported" in any_kind
    some = ask_description(SimpleNamespace(description="봇", accepts_files=("image/", "application/pdf")))
    assert "image/*, application/pdf" in some
    assert "받지 않는다" in ask_description(SimpleNamespace(description="봇", accepts_files=()))
