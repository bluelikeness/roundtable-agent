"""러너 설정. 환경변수 RUNNER_* 로 덮어쓴다."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PKG_DIR = Path(__file__).resolve().parent


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNNER_", extra="ignore")

    # claude 실행 파일. 테스트는 가짜 스크립트로 바꾼다
    claude_bin: list[str] = ["claude"]
    # Claude Code 작업 디렉터리. 비어 있고 CLAUDE.md 가 없어야 한다.
    # --resume 은 이 경로 기준으로 세션 파일을 찾으므로 실행마다 바꾸지 않는다.
    work_dir: Path = Path("/srv/runner/work")
    # 실행별 mcp.json·시스템 프롬프트를 두는 곳. 실행이 끝나면 지운다
    runs_dir: Path = Path("/tmp/multibot-runs")
    # 오케스트레이터 시스템 프롬프트 템플릿(docs/04-orchestrator.md 8장)
    prompt_file: Path = PKG_DIR.parent / "orchestrator.md"
    # Claude Code --max-turns. 0 이면 넘기지 않는다(상한 없음, 2026-11-16 결정: 턴 수로 끊지 않고
    # 600초 실행 상한·지침의 종료 판단에 맡긴다. 길어진 맥락은 Claude Code 자동 압축이 처리한다)
    max_turns: int = 0
    # 프로세스 전체 상한(02 4장). 넘으면 SIGTERM
    timeout_s: float = 600
    # MCP 툴 호출 하나의 상한(mcp.json 서버별 timeout). Claude Code 기본 60초를 덮는다
    tool_timeout_s: float = 270
    # SIGTERM 뒤 이만큼 기다렸다가 SIGKILL
    term_grace_s: float = 5
    disallowed_tools: str = "Bash,Read,Write,Edit,WebSearch,WebFetch"
    # 허브↔어댑터·러너 공유 토큰(X-Hub-Token). compose secret 파일
    token_file: Path = Path("/run/secrets/hub_adapter_token")
    # 토론 라운드 상한·자동 확정 상한(04 8장 주입 변수, 값 조정은 P1-4)
    debate_max_rounds: int = 3
    auto_confirm_limit: int = 0

    def read_token(self) -> str:
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


@lru_cache
def get_settings() -> RunnerSettings:
    return RunnerSettings()
