"""오케스트레이터 평가 (docs/04-orchestrator.md 10장 T1~T14). `make eval` 로 돈다.

러너 HTTP(POST /runs)를 직접 불러 실제 `claude -p` 를 실행하고, NDJSON 스트림의
tool_call 기록과 claude.note/claude.final 로 툴 호출 패턴·정리 형식을 기계적으로 판정한다.
허브·DB·웹은 거치지 않는다. 봇은 평가 전용 Mock(runner/eval/mocks)이다.
"""
