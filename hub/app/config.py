"""허브 설정. 비밀값은 환경변수(.env)로만 받는다."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 이 값이나 32자 미만 키로는 쿠키를 다루지 않는다(require_strong_secret)
DEV_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    database_url: str = "postgresql+asyncpg://roundtable:roundtable@localhost:5433/roundtable"
    # 세션 쿠키 서명 키. 운영에서는 반드시 .env 로 덮어쓴다.
    secret_key: str = DEV_SECRET
    # 로컬 http 개발에서는 false. 운영(caddy TLS 뒤)에서는 true.
    cookie_secure: bool = True
    cookie_name: str = "mb_session"
    session_max_age_s: int = 7 * 24 * 3600
    # 로그인 실패 잠금 (05-ui 7장: 5회 실패 시 15분)
    login_max_failures: int = 5
    login_lock_s: int = 15 * 60

    # 러너(docs/02-architecture.md 4장). 내부망 주소
    runner_url: str = "http://runner:8700"
    # 허브↔어댑터·러너 공유 토큰(X-Hub-Token). compose secret 파일, 없으면 HUB_ADAPTER_TOKEN
    adapter_token: str = ""
    adapter_token_file: str = "/run/secrets/hub_adapter_token"
    # 봇 등록 파일(03 10장). 실행마다 다시 읽는다
    bots_file: str = str(Path(__file__).resolve().parents[1] / "bots.yaml")
    # 어댑터 헬스체크 폴링 간격(03 5장: 30초). 0 이면 폴링하지 않는다(테스트)
    health_interval_s: float = 30
    # "오늘" 실행 횟수·토큰을 셀 때의 UTC 오프셋(시간). 봇 상태 패널 Claude Code 섹션. 기본 KST
    day_utc_offset_h: int = 9
    # 바깥(피클 등 다른 AI 비서)이 방에 의견을 넣는 입구 POST /api/inbox 의 Bearer 토큰.
    # 비어 있으면 입구를 닫는다(404). 32자 이상 무작위 값
    inbox_token: str = ""
    # 입구 남용 방지: 분당 받는 건수와 글 길이 상한
    inbox_per_minute: int = 10
    inbox_max_chars: int = 4000
    # POST 가 실행 끝을 기다리는 최대 초. 넘으면 202 와 결과 주소(GET)를 돌려준다
    inbox_wait_s: float = 15

    def read_adapter_token(self) -> str:
        if self.adapter_token:
            return self.adapter_token
        try:
            return Path(self.adapter_token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def require_strong_secret(self) -> None:
        """기본값이나 짧은 서명 키로는 쿠키를 발급·검증하지 않는다(위조 방지)."""
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            raise RuntimeError("HUB_SECRET_KEY 를 32자 이상 무작위 값으로 설정하세요")


@lru_cache
def get_settings() -> Settings:
    return Settings()
