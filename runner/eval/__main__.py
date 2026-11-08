"""python -m eval [--cases T1,T3] [--n 5] [--concurrency 4]

케이스마다 n 회 실행해 판정하고 리포트(out/report-<시각>.md, out/latest.md)와
원본 NDJSON(out/<시각>/<케이스>-<회차>.ndjson)을 남긴다.
합격: 케이스별 n 회 중 PASS_RUNS 회 이상 통과. 전체: 14개 중 PASS_CASES 개 이상(06 로드맵 P1-4).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from eval.cases import CASES, CASES_BY_ID, Case, judge
from eval.client import read_token, run_case
from eval.report import RunResult, render

PASS_RUNS = 4
PASS_CASES = 12


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval")
    ap.add_argument("--cases", default="", help="쉼표로 구분한 케이스 ID. 비우면 전부")
    ap.add_argument("--n", type=int, default=5, help="케이스당 실행 횟수")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default=os.environ.get("EVAL_OUT", "runner/eval/out"))
    ap.add_argument("--token-file", default="/run/secrets/hub_adapter_token")
    args = ap.parse_args(argv)

    cases: list[Case] = [CASES_BY_ID[c.strip()] for c in args.cases.split(",") if c.strip()] or CASES
    token = read_token(Path(args.token_file))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    raw_dir = out / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)

    jobs = [(case, i) for i in range(1, args.n + 1) for case in cases]
    results: list[RunResult] = []
    t0 = time.monotonic()

    def one(job: tuple[Case, int]) -> RunResult:
        case, i = job
        started = time.monotonic()
        traces, raws = run_case(case, token)
        for turn, lines in enumerate(raws, 1):
            suffix = f"-t{turn}" if len(raws) > 1 else ""
            (raw_dir / f"{case.id}-{i}{suffix}.ndjson").write_text("".join(lines), encoding="utf-8")
        checks = judge(case, traces)
        r = RunResult(case=case, index=i, traces=traces, checks=checks, seconds=time.monotonic() - started)
        mark = "PASS" if checks.passed else "FAIL " + "; ".join(checks.failed())
        print(f"[{case.id}#{i}] {r.seconds:5.0f}s {mark}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, jobs))

    report = render(cases, results, n=args.n, pass_runs=PASS_RUNS, pass_cases=PASS_CASES,
                    stamp=stamp, elapsed_s=time.monotonic() - t0)
    (out / f"report-{stamp}.md").write_text(report, encoding="utf-8")
    (out / "latest.md").write_text(report, encoding="utf-8")
    print(report)
    passed_cases = sum(
        1 for c in cases if sum(r.checks.passed for r in results if r.case.id == c.id) >= min(PASS_RUNS, args.n)
    )
    need = PASS_CASES if len(cases) == len(CASES) else len(cases)
    return 0 if passed_cases >= need else 1


if __name__ == "__main__":
    sys.exit(main())
