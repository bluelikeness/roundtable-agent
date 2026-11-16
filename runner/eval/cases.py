"""T1~T15 (docs/04-orchestrator.md 10장). 입력·Mock 배치·판정.

판정은 스트림에서 기계적으로 본다. 한 케이스의 모든 검사가 통과해야 그 실행이 통과다.
정리 형식: 결론 줄(첫 줄이 표·제목이 아님), 줄 수 상한(단일 5줄, 그 외 15줄 — 04 7장).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from eval.trace import Trace, mentions

STD = {"mock-a": "mock-a", "mock-b": "mock-b", "mock-c": "mock-c"}
SINGLE_LINES = 5
MULTI_LINES = 15

FANOUT_TEXT = "셋 다한테 도쿄 왕복 최저가 알아봐줘"
DINNER_TEXT = "강남 한식 내일 7시 2명"
BOOK_TEXT = "mock-a로 강남 한옥집 내일 저녁 7시 2명 예약해줘"
CONFIRM_RAW = "한옥집 내일 19:00 2명 예약을 확정하려면 예약금 20,000원 결제가 필요합니다. 진행할까요?"
CONFIRM_ACTION = "btn_confirm_1"


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool]] = []

    def add(self, label: str, ok: object) -> None:
        self.items.append((label, bool(ok)))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.items)

    def failed(self) -> list[str]:
        return [label for label, ok in self.items if not ok]


@dataclass
class Case:
    id: str
    title: str
    turns: list[str]
    judge: Callable[[list[Trace], Checks], None]
    # 봇 이름 → compose 서비스. 시나리오 변형 Mock 도 봇 이름은 원래 이름(mcp__mock-a__ask)
    bots: dict[str, str] = field(default_factory=lambda: dict(STD))
    runner: str = "runner"
    single: bool = False  # 단일 모드: 정리 5줄
    session: dict[str, Any] = field(default_factory=dict)  # RunnerSession 덮어쓰기(T14)


def common(t: Trace, c: Checks, single: bool) -> None:
    c.add("run.finished done", t.status == "done")
    c.add("최종 정리 있음", t.final.strip())
    lines = t.final_lines()
    c.add("결론 줄(첫 줄이 표·제목 아님)", lines and not lines[0].lstrip().startswith(("|", "#")))
    limit = SINGLE_LINES if single else MULTI_LINES
    c.add(f"정리 {limit}줄 이내", len(lines) <= limit)


def _last(traces: list[Trace]) -> Trace:
    return traces[-1]


def _no_tap(t: Trace) -> bool:
    return not any("[tap:" in x.text for x in t.asks())


# --- 판정 -------------------------------------------------------------------


def t1(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("툴 호출 없음", not t.calls)
    c.add("봇 목록 언급(mock-a·b·c)", all(n in t.final for n in ("mock-a", "mock-b", "mock-c")))


def t2(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("ask 는 mock-a 1회만", [x.bot for x in t.asks()] == ["mock-a"])
    c.add("status ≤ 1", len(t.statuses()) <= 1)
    c.add("툴 호출 ≤ 4", len(t.calls) <= 4)


def _fanout(t: Trace, c: Checks, bots: tuple[str, ...]) -> None:
    for b in bots:
        c.add(f"{b} ask 1회", len(t.asks(b)) == 1)
    asks = [x for x in t.asks() if x.bot in bots]
    c.add("ask 병렬(한 턴)", t.parallel(asks))
    c.add("비교표", t.has_table())


def t3(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    for b in ("mock-a", "mock-b", "mock-c"):
        c.add(f"{b} status 호출", t.statuses(b))
    _fanout(t, c, ("mock-a", "mock-b", "mock-c"))
    c.add("정리에 mock-c 포함", "mock-c" in t.final)


def t4(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    a, b = t.asks("mock-a"), t.asks("mock-b")
    c.add("mock-a ask", a)
    c.add("mock-b ask", b)
    c.add("mock-c 호출 없음", not t.asks("mock-c"))
    if a and b:
        c.add("a → b 순차", t.sequential(a[0], b[0]))
        a_raw = str((a[0].output or {}).get("raw", ""))
        c.add("b 입력에 a 의 식당명", mentions(b[0].text, "한옥집", "밥상", "소반"))
        c.add("b 입력에 a 원문 통째 아님", a_raw and a_raw.strip() not in b[0].text)
        c.add("b 입력에 출처(다른 비서)", mentions(b[0].text, "다른 (AI )?비서", "다른 봇", "다른 어시스턴트"))
    c.add("릴레이 노트 1줄 이상", t.notes)


def _debate(t: Trace, c: Checks, max_asks: int) -> None:
    c.add("mock-a·mock-b 모두 ask", t.asks("mock-a") and t.asks("mock-b"))
    c.add("mock-c 호출 없음", not t.asks("mock-c"))
    c.add(f"ask ≤ {max_asks}", len(t.asks()) <= max_asks)


def _answers_other(t: Trace, bot: str, other: str) -> bool:
    """bot 의 어떤 ask 가 other 의 답이 온 뒤에 나갔고, other 의 이름을 담았나(상대 발언을 받아 답하게 했나)."""
    return any(
        other in x.text and any(t.sequential(o, x) for o in t.asks(other)) for x in t.asks(bot)
    )


def t5(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    _debate(t, c, 6)
    c.add(
        "상대 발언을 받아 답하게 함(a 답 뒤 b 에, 또는 b 답 뒤 a 에)",
        _answers_other(t, "mock-b", "mock-a") or _answers_other(t, "mock-a", "mock-b"),
    )
    c.add("입장 표", t.has_table())


def t6(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    a = t.asks("mock-a")
    c.add("mock-a ask 2회(되물음 즉시 응답)", len(a) == 2)
    if len(a) == 2:
        c.add("두 번째 입력에 2명", mentions(a[1].text, r"2\s*명", r"\b2\b", "두 명"))
    c.add("다른 봇 호출 없음", t.ask_bots() <= {"mock-a"})
    c.add("노트에 자동 응답 기록", any(mentions(n, r"2\s*명", "인원") for n in t.notes))


def t7(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("mock-a ask 1회(재호출 없음)", len(t.asks("mock-a")) == 1)
    c.add("다른 봇 호출 없음", t.ask_bots() <= {"mock-a"})
    c.add("봇 질문(예산) 전달", "예산" in t.final)


def t8(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("mock-a ask", t.asks("mock-a"))
    c.add("[tap:] 없음", _no_tap(t))
    c.add("'진행할까요?'", "진행할까요" in t.final)
    c.add("확정 내용(금액)", mentions(t.final, "20,000", "2만", "20000"))


def t9(ts: list[Trace], c: Checks) -> None:
    first, t = ts[0], ts[-1]
    c.add("1턴: [tap:] 없음", _no_tap(first))
    c.add("2턴: --resume 로 이어짐", t.claude_session_id and t.claude_session_id == first.claude_session_id)
    c.add(f"2턴: [tap:{CONFIRM_ACTION}]", any(f"[tap:{CONFIRM_ACTION}]" in x.text for x in t.asks("mock-a")))
    c.add("결과 보고(확정·완료)", mentions(t.final, "확정", "완료"))


def t10(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("mock-b status 호출", t.statuses("mock-b"))
    c.add("mock-b ask 없음", not t.asks("mock-b"))
    c.add("mock-a·mock-c ask", t.asks("mock-a") and t.asks("mock-c"))
    c.add(
        "정리에 mock-b 응답 불가",
        "mock-b" in t.final and mentions(t.final, "불가", "로그인", "login", "사용할 수 없", "unavailable", "이용할 수 없"),
    )


def t11(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("mock-a ask 1회(재시도 없음)", len(t.asks("mock-a")) == 1)
    c.add("mock-b·mock-c ask", t.asks("mock-b") and t.asks("mock-c"))
    c.add("비교표", t.has_table())
    c.add(
        "정리에 mock-a 실패",
        "mock-a" in t.final and mentions(t.final, "실패", "오류", "timeout", "응답 (없|불가|못)", "시간 초과"),
    )


def t12(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    c.add("mock-a ask 없음", not t.asks("mock-a"))
    c.add("다른 봇 ask", t.asks())


def t13(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    _debate(t, c, 10)
    c.add("상한(5라운드) 안내", mentions(t.final + "\n" + "\n".join(t.notes), r"5\s*라운드", r"최대\s*5", r"상한"))


def t14(ts: list[Trace], c: Checks) -> None:
    t = _last(ts)
    a = t.asks("mock-a")
    c.add("mock-a ask", a)
    c.add("만료 action_id 로 [tap:] 안 함", _no_tap(t))
    c.add("입력에 식당명(요약 반영)", any("한옥집" in x.text for x in a))
    c.add("'진행할까요?'", "진행할까요" in t.final)


def t15(ts: list[Trace], c: Checks) -> None:
    """회의: 봇 지명 없이 "회의해" → 가용 봇 전부 1라운드, 2라운드부터 서로의 주장을 전한다."""
    t = _last(ts)
    for bot in STD:
        c.add(f"{bot} 참가(ask)", t.asks(bot))
    others = {b: [o for o in STD if o != b] for b in STD}
    relayed = [
        b for b in ("mock-a", "mock-b") if any(any(o in x.text for o in others[b]) for x in t.asks(b)[1:])
    ]
    c.add("2라운드 입력에 다른 봇 이름·주장(mock-a 또는 mock-b)", relayed)
    c.add("ask ≤ 9", len(t.asks()) <= 9)
    c.add("봇×쟁점 표", t.has_table())


def t14_session() -> dict[str, Any]:
    """25시간 전 마지막 메시지 → 러너가 새 Claude 세션 + <session_summary> 주입(04 9장).

    claude_session_id 는 분할 판단용 자리값이다(resume 하지 않으므로 실제 세션이 없어도 된다).
    """
    return {
        "claude_session_id": "eval-expired-session",
        "last_message_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        "last_final": "mock-a가 강남 한옥집 내일 19:00 2명 예약을 준비했습니다. 예약금 20,000원 결제가 필요합니다. 진행할까요?",
        "last_bot_raws": {"mock-a": CONFIRM_RAW},
    }


CASES: list[Case] = [
    Case("T1", "단독 답변", ["너 뭐 할 수 있어?"], t1),
    Case("T2", "단일 봇", [DINNER_TEXT], t2, single=True),
    Case("T3", "팬아웃 3봇", [FANOUT_TEXT], t3),
    Case("T4", "릴레이 a→b", ["mock-a가 찾은 강남 한식당을 mock-b한테 넘겨서 근처 호텔 찾게 해"], t4),
    Case("T5", "토론 2봇", ["mock-a랑 mock-b 서로 비판하게 해서 이번 주말 부산 1박 여행 계획 하나로 합의 보게 해"], t5),
    Case("T6", "되물음 자동 응답", [DINNER_TEXT], t6, bots={**STD, "mock-a": "a-people"}, single=True),
    Case("T7", "되물음 사용자 전달", [DINNER_TEXT], t7, bots={**STD, "mock-a": "a-budget"}, single=True),
    Case("T8", "확정 요청", [BOOK_TEXT], t8, bots={**STD, "mock-a": "a-confirm"}, single=True),
    Case("T9", "확정 진행(2턴)", [BOOK_TEXT, "진행해"], t9, bots={**STD, "mock-a": "a-confirm"}, single=True),
    Case("T10", "봇 불가", [FANOUT_TEXT], t10, bots={**STD, "mock-b": "b-down"}),
    Case("T11", "봇 실패", [FANOUT_TEXT], t11, bots={**STD, "mock-a": "a-fail"}),
    Case("T12", "지명 제외", ["mock-a 말고 다른 봇들한테 강남 한식 내일 7시 2명 찾아줘"], t12),
    Case("T13", "토론 상한", ["mock-a랑 mock-b 서로 비판하게 해서 부산 1박 여행 계획 합의 보게 해. 10라운드 해"], t13, runner="runner-r5"),
    Case("T15", "회의(지명 없음)", ["회의해서 부산 1박 여행 계획에 대해 의견 모아줘. 서로 의견 주고받게 해"], t15),
    Case("T14", "분할 후 이어받기", ["아까 그 식당 예약해"], t14, bots={**STD, "mock-a": "a-confirm"}, single=True),
]
CASES_BY_ID = {c.id: c for c in CASES}


def session_overrides(case: Case) -> dict[str, Any]:
    return t14_session() if case.id == "T14" else dict(case.session)


def judge(case: Case, traces: list[Trace]) -> Checks:
    c = Checks()
    common(traces[-1], c, case.single)
    case.judge(traces, c)
    return c
