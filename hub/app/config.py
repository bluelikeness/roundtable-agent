"""허브 설정. 비밀값은 환경변수(.env)로만 받는다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 이 값이나 32자 미만 키로는 쿠키를 다루지 않는다(require_strong_secret)
DEV_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    database_url: str = "postgresql+asyncpg://multibot:multibot@localhost:5433/multibot"
    # 세션 쿠키 서명 키. 운영에서는 반드시 .env 로 덮어쓴다.
    secret_key: str = DEV_SECRET
    # 로컬 http 개발에서는 false. 운영(caddy TLS 뒤)에서는 true.
    cookie_secure: bool = True
    cookie_name: str = "mb_session"
    session_max_age_s: int = 7 * 24 * 3600
    # 로그인 실패 잠금 (05-ui 7장: 5회 실패 시 15분)
    login_max_failures: int = 5
    login_lock_s: int = 15 * 60

    def require_strong_secret(self) -> None:
        """기본값이나 짧은 서명 키로는 쿠키를 발급·검증하지 않는다(위조 방지)."""
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            raise RuntimeError("HUB_SECRET_KEY 를 32자 이상 무작위 값으로 설정하세요")


@lru_cache
def get_settings() -> Settings:
    return Settings()
