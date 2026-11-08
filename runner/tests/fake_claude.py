"""테스트용 가짜 claude. 인자·stdin 을 기록하고 FAKE_CLAUDE_SCRIPT 의 줄을 stdout 으로 낸다.

환경변수
- FAKE_CLAUDE_SCRIPT: 내보낼 stream-json 파일
- FAKE_CLAUDE_LOG: 받은 argv·stdin·mcp.json·HUB_ADAPTER_TOKEN 을 JSON 으로 기록할 파일
- FAKE_CLAUDE_SLEEP: 다 낸 뒤 이만큼 잠든다(타임아웃·중단 시험)
- FAKE_CLAUDE_RESUME_MISSING: --resume 이 오면 세션 없음 오류로 끝낸다
- FAKE_CLAUDE_STDERR / FAKE_CLAUDE_EXIT: 표준오류 문구와 종료 코드
"""

import json
import os
import sys
import time

argv = sys.argv[1:]
stdin = sys.stdin.buffer.read().decode("utf-8")  # 실제 claude 처럼 UTF-8 로 읽는다
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    mcp = None
    if "--mcp-config" in argv:
        with open(argv[argv.index("--mcp-config") + 1], encoding="utf-8") as f:
            mcp = json.load(f)
    prompt_file = None
    if "--append-system-prompt-file" in argv:
        with open(argv[argv.index("--append-system-prompt-file") + 1], encoding="utf-8") as f:
            prompt_file = f.read()
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin, "mcp": mcp, "prompt": prompt_file, "cwd": os.getcwd(),
                            "token": os.environ.get("HUB_ADAPTER_TOKEN"), "tool_search": os.environ.get("ENABLE_TOOL_SEARCH")}, ensure_ascii=False) + "\n")
if "--resume" in argv and os.environ.get("FAKE_CLAUDE_RESUME_MISSING"):
    sys.stderr.write("No conversation found with session ID: " + argv[argv.index("--resume") + 1] + "\n")
    sys.exit(1)
script = os.environ.get("FAKE_CLAUDE_SCRIPT")
if script:
    with open(script, encoding="utf-8") as f:
        for line in f:
            sys.stdout.buffer.write(line.encode("utf-8"))
            sys.stdout.buffer.flush()
if os.environ.get("FAKE_CLAUDE_STDERR"):
    sys.stderr.write(os.environ["FAKE_CLAUDE_STDERR"] + "\n")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
