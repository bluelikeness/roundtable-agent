"""허브 설정. 비밀값은 환경변수(.env)로만 받는다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    database_url: str = "postgresql+asyncpg://multibot:multibot@localhost:5433/multibot"
    # 세션 쿠키 서명 키. 운영에서는 반드시 .env 로 덮어쓴다.
    secret_key: str = "dev-only-change-me"
    # 로컬 http 개발에서는 false. 운영(caddy TLS 뒤)에서는 true.
    cookie_secure: bool = True
    cookie_name: str = "mb_session"
    session_max_age_s: int = 7 * 24 * 3600
    # 로그인 실패 잠금 (05-ui 7장: 5회 실패 시 15분)
    login_max_failures: int = 5
    login_lock_s: int = 15 * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
