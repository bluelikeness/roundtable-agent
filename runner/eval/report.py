"""평가 리포트(markdown). 케이스 합격표 + 실행별 실패 검사 + 06 로드맵 P1-4 열린 질문 관찰."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from eval.cases import Case, Checks
from eval.trace import Trace

# 코딩 도구 성향 흔적(04 11장 미결): 정리·노트에 개발 도구 말투가 섞이는지.
# "파일·셸·웹 도구는 없다"처럼 프롬프트를 되풀이하는 말은 세지 않는다
CODING_RE = re.compile(
    r"코딩|프로그래밍|코드(를|로|가| 작성| 실행)|파일을|저장소|레포|repository|bash|터미널|커밋|디렉터리|```",
    re.IGNORECASE,
)
CONFIRM_ASK_RE = re.compile(r"진행할까요\s*\??")
FANOUT_CASES = ("T3", "T10", "T11")
CONFIRM_CASES = ("T8", "T9", "T14")


@dataclass
class RunResult:
    case: Case
    index: int
    traces: list[Trace]
    checks: Checks
    seconds: float

    @property
    def last(self) -> Trace:
        return self.traces[-1]


def _pct(k: int, n: int) -> str:
    return f"{k}/{n}" if n else "-"


def _calls(t: Trace) -> str:
    seq = []
    for c in t.calls:
        name = f"{c.bot.removeprefix('mock-')}.{c.tool}"
        if c.tool == "ask" and c.output is not None and not c.output.get("ok", True):
            name += "(실패)"
        seq.append(name)
    return " → ".join(seq) or "(없음)"


def render(cases: list[Case], results: list[RunResult], *, n: int, pass_runs: int, pass_cases: int,
           stamp: str, elapsed_s: float) -> str:
    by_case: dict[str, list[RunResult]] = {c.id: sorted((r for r in results if r.case.id == c.id), key=lambda r: r.index)
                                           for c in cases}
    need = min(pass_runs, n)
    out: list[str] = []
    passed_cases = 0
    rows = []
    for c in cases:
        rs = by_case[c.id]
        k = sum(r.checks.passed for r in rs)
        ok = k >= need
        passed_cases += ok
        fails = Counter(label for r in rs for label in r.checks.failed())
        top = ", ".join(f"{label}×{cnt}" for label, cnt in fails.most_common(3)) or "-"
        turns = [r.last.num_turns() for r in rs if r.last.num_turns() is not None]
        avg_turns = f"{sum(turns) / len(turns):.1f}" if turns else "-"
        avg_s = f"{sum(r.seconds for r in rs) / len(rs):.0f}" if rs else "-"
        rows.append(f"| {c.id} | {c.title} | {k}/{len(rs)} | {'✅' if ok else '❌'} | {avg_turns} | {avg_s} | {top} |")

    goal = pass_cases if len(cases) >= 14 else len(cases)
    out.append(f"# 오케스트레이터 평가 리포트 {stamp}")
    out.append("")
    out.append(f"- 케이스 {len(cases)}개 × {n}회, 소요 {elapsed_s / 60:.1f}분")
    out.append(f"- 합격 케이스 **{passed_cases}/{len(cases)}** (케이스 기준 {need}/{n} 이상, 목표 {goal}개 이상) → "
               f"{'**통과**' if passed_cases >= goal else '**미달**'}")
    out.append("")
    out.append("| # | 케이스 | 통과 | 합격 | 평균 턴 | 평균 초 | 주요 실패 검사 |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    out.extend(rows)
    out.append("")

    out.append("## 관찰 (06 로드맵 P1-4 열린 질문)")
    out.append("")
    out.extend(_observations(by_case))
    out.append("")

    out.append("## 실행별 상세")
    out.append("")
    for c in cases:
        out.append(f"### {c.id} {c.title}")
        out.append("")
        for r in by_case[c.id]:
            mark = "PASS" if r.checks.passed else "FAIL"
            for ti, t in enumerate(r.traces, 1):
                prefix = f"#{r.index}" + (f" 턴{ti}" if len(r.traces) > 1 else "")
                err = f" ⚠ {t.transport_error}" if t.transport_error else ""
                status = t.status or "-"
                if t.error:
                    status += f"({t.error})"
                out.append(f"- {prefix} {mark if ti == len(r.traces) else ''} `{_calls(t)}` "
                           f"· 정리 {len(t.final_lines())}줄 · {status}{err}")
            if not r.checks.passed:
                out.append(f"  - 실패: {', '.join(r.checks.failed())}")
        out.append("")
    return "\n".join(out) + "\n"


def _observations(by_case: dict[str, list[RunResult]]) -> list[str]:
    lines: list[str] = []
    every = [t for rs in by_case.values() for r in rs for t in r.traces if t.status]

    coding = [t for t in every if CODING_RE.search(t.final + "\n".join(t.notes))]
    lines.append(f"- **코딩 성향 잔존**: 정리·노트에 개발 도구 말투(코드·파일·셸 등)가 나온 실행 {_pct(len(coding), len(every))}.")
    if "T1" in by_case:
        t1 = [r.last for r in by_case["T1"] if r.last.status]
        bad = [t for t in t1 if t.calls or CODING_RE.search(t.final)]
        lines.append(f"  T1(자기 소개)에서 툴 호출이나 코딩 언급이 있었던 실행 {_pct(len(bad), len(t1))}.")

    fan = [r.last for cid in FANOUT_CASES for r in by_case.get(cid, []) if r.last.status]
    par = [t for t in fan if len(t.asks()) >= 2 and t.parallel(t.asks())]
    multi = [t for t in fan if len(t.asks()) >= 2]
    st_par = [t for t in fan if len(t.statuses()) >= 2 and t.parallel(t.statuses())]
    st_multi = [t for t in fan if len(t.statuses()) >= 2]
    lines.append(f"- **병렬 툴 호출**: 팬아웃({', '.join(FANOUT_CASES)})에서 ask 2개 이상이 한 턴에 나간 실행 "
                 f"{_pct(len(par), len(multi))}, status 가 한 턴에 나간 실행 {_pct(len(st_par), len(st_multi))}.")

    fan_status = [t for t in fan if t.statuses()]
    single_ids = [cid for cid in ("T2", "T6", "T7", "T8", "T12") if cid in by_case]
    single = [r.traces[0] for cid in single_ids for r in by_case[cid] if r.traces[0].status]
    single_status = [t for t in single if t.statuses()]
    t10 = [r.last for r in by_case.get("T10", []) if r.last.status]
    t10_saved = [t for t in t10 if t.statuses("mock-b") and not t.asks("mock-b")]
    lines.append(f"- **status 선호출**: 팬아웃 실행 중 status 호출 {_pct(len(fan_status), len(fan))}, "
                 f"단일 계열({', '.join(single_ids)}) 중 {_pct(len(single_status), len(single))}. "
                 f"T10 에서 status 로 불가 봇 ask 를 피한 실행 {_pct(len(t10_saved), len(t10))}.")

    conf: list[tuple[bool, bool]] = []  # (정리에 '진행할까요?' 있음, 봇 결과에 confirmation_required 있음)
    for cid in CONFIRM_CASES:
        for r in by_case.get(cid, []):
            t = r.traces[0] if cid == "T9" else r.last
            if not t.status:
                continue
            marker = any((c.output or {}).get("structured", {}).get("confirmation_required") for c in t.asks())
            conf.append((bool(CONFIRM_ASK_RE.search(t.final)), marker))
    both = sum(1 for a, b in conf if a and b)
    regex_only = sum(1 for a, b in conf if a and not b)
    marker_only = sum(1 for a, b in conf if b and not a)
    lines.append(f"- **[진행] 감지**: 확정 단계 실행({', '.join(CONFIRM_CASES)} 확정 턴) {len(conf)}건 중 정규식(`진행할까요?`)과 "
                 f"구조화 마커(`structured.confirmation_required`)가 함께 잡힌 건 {both}, 정규식만 {regex_only}, 마커만 {marker_only}.")
    return lines
