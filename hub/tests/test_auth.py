"""로그인·TOTP·잠금·쿠키 (docs/05-ui.md 7장)."""

from datetime import UTC, datetime, timedelta

import pyotp

from app.models import Operator
from tests.conftest import PASSWORD, USERNAME, totp_now


async def _login(client, password=PASSWORD, totp="000000", username=USERNAME):
    return await client.post("/api/auth/login", json={"username": username, "password": password, "totp": totp})


def _wrong_totp(secret: str) -> str:
    good = {pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30 * k)) for k in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in good)


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_login_success_sets_cookie(client, operator):
    _, secret = operator
    r = await _login(client, totp=totp_now(secret))
    assert r.status_code == 200
    assert r.json() == {"username": USERNAME}
    set_cookie = r.headers["set-cookie"].lower()
    assert "mb_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "max-age=604800" in set_cookie  # 7일

    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["username"] == USERNAME
    # 활동 시 쿠키 재발급(유효기간 연장)
    assert "mb_session=" in me.headers.get("set-cookie", "")


async def test_wrong_password_or_totp_or_user(client, operator):
    _, secret = operator
    assert (await _login(client, password="wrong", totp=totp_now(secret))).status_code == 401
    assert (await _login(client, totp=_wrong_totp(secret))).status_code == 401
    r = await _login(client, username="nobody", totp=totp_now(secret))
    assert r.status_code == 401 and r.json()["detail"] == "invalid_credentials"
    assert "set-cookie" not in r.headers


async def test_lock_after_five_failures(client, operator, db):
    _, secret = operator
    for _ in range(5):
        assert (await _login(client, password="wrong", totp=totp_now(secret))).status_code == 401
    # 잠긴 동안에는 맞는 값도 거부
    r = await _login(client, totp=totp_now(secret))
    assert r.status_code == 423 and r.json()["detail"] == "locked"

    # 잠금 시각이 지나면 다시 로그인 가능
    op = await db.get(Operator, operator[0].id)
    await db.refresh(op)
    assert op.locked_until is not None
    assert op.locked_until - datetime.now(UTC) > timedelta(minutes=14)
    op.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    assert (await _login(client, totp=totp_now(secret))).status_code == 200


async def test_failures_reset_on_success(client, operator, db):
    _, secret = operator
    for _ in range(4):
        await _login(client, password="wrong", totp=totp_now(secret))
    assert (await _login(client, totp=totp_now(secret))).status_code == 200
    op = await db.get(Operator, operator[0].id)
    await db.refresh(op)
    assert op.failed_attempts == 0


async def test_requires_login(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/sessions")).status_code == 401
    client.cookies.set("mb_session", "tampered.value.sig")
    assert (await client.get("/api/sessions")).status_code == 401


async def test_auth_version_bump_invalidates_cookie(authed, db, operator):
    op = await db.get(Operator, operator[0].id)
    await db.refresh(op)
    op.auth_version += 1
    await db.commit()
    assert (await authed.get("/api/auth/me")).status_code == 401


async def test_logout(authed):
    r = await authed.post("/api/auth/logout")
    assert r.status_code == 204
    assert (await authed.get("/api/auth/me")).status_code == 401


async def test_secure_cookie_flag(client, operator, monkeypatch):
    from app.config import get_settings

    _, secret = operator
    get_settings.cache_clear()
    monkeypatch.setenv("HUB_COOKIE_SECURE", "true")
    try:
        r = await _login(client, totp=totp_now(secret))
        assert "secure" in r.headers["set-cookie"].lower()
    finally:
        monkeypatch.setenv("HUB_COOKIE_SECURE", "false")
        get_settings.cache_clear()
