"""허브 테스트 공통. 실제 PostgreSQL(테스트 전용 DB)에 마이그레이션을 올려 쓴다.

DB 주소는 HUB_TEST_DATABASE_URL (make test-hub 가 .env 로 채운다).
"""

import os

TEST_DB_URL = os.environ.get("HUB_TEST_DATABASE_URL")
if not TEST_DB_URL:
    raise RuntimeError("HUB_TEST_DATABASE_URL 이 필요합니다. `make test-hub` 로 실행하세요.")
if not TEST_DB_URL.rstrip("/").endswith("_test"):
    # 실수로 개발 DB 를 비우지 않도록 막는다
    raise RuntimeError("테스트 DB 이름은 _test 로 끝나야 합니다")

# app 을 import 하기 전에 설정을 테스트용으로 고정한다
os.environ["HUB_DATABASE_URL"] = TEST_DB_URL
os.environ["HUB_SECRET_KEY"] = "test-secret-key"
os.environ["HUB_COOKIE_SECURE"] = "false"

from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import hash_password, new_totp_secret  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Operator  # noqa: E402

HUB_DIR = Path(__file__).resolve().parents[1]
USERNAME = "operator"
PASSWORD = "correct horse battery staple"
TABLES = "tool_call, run, message, bot_thread, session, bot, operator"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """테스트 DB 를 비우고 마이그레이션을 처음부터 올린다(마이그레이션 자체도 검증)."""
    cfg = Config(str(HUB_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(HUB_DIR / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={TEST_DB_URL}"]})()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def clean_tables():
    async with get_sessionmaker()() as db:
        await db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await db.commit()
    yield


@pytest.fixture
async def db():
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def operator(db) -> tuple[Operator, str]:
    secret = new_totp_secret()
    op = Operator(username=USERNAME, password_hash=hash_password(PASSWORD), totp_secret=secret)
    db.add(op)
    await db.commit()
    return op, secret


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        yield c


def totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()


@pytest.fixture
async def authed(client, operator):
    """로그인된 클라이언트."""
    _, secret = operator
    r = await client.post(
        "/api/auth/login", json={"username": USERNAME, "password": PASSWORD, "totp": totp_now(secret)}
    )
    assert r.status_code == 200, r.text
    return client
