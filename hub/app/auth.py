"""운영자 인증: bcrypt 비밀번호 + TOTP, 서명된 세션 쿠키 (docs/05-ui.md 7장)."""

import asyncio
import hmac
from datetime import UTC, datetime, timedelta

import bcrypt
import pyotp
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Operator

_COOKIE_SALT = "mb-operator-session"
# 없는 아이디에도 같은 시간만큼 bcrypt 를 돌려 응답 시간으로 아이디 존재 여부가 드러나지 않게 한다
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


async def verify_password_async(password: str, password_hash: str) -> bool:
    # bcrypt 는 수백 ms 걸리는 CPU 작업이라 이벤트 루프를 막지 않게 스레드에서 돈다
    return await asyncio.to_thread(verify_password, password, password_hash)


def new_totp_secret() -> str:
    return pyotp.random_base32()


def match_totp_step(secret: str, code: str, now: datetime | None = None) -> int | None:
    """코드가 맞으면 그 코드의 시간 단계(30초 단위 번호)를, 아니면 None.

    시계 오차를 고려해 앞뒤 1단계까지 허용한다. 재사용 방지는 호출하는 쪽이 단계 번호로 한다.
    """
    totp = pyotp.TOTP(secret)
    now = now or datetime.now(UTC)
    code = code.strip()
    for offset in (-1, 0, 1):
        t = now + timedelta(seconds=totp.interval * offset)
        if hmac.compare_digest(totp.at(t), code):
            return totp.timecode(t)
    return None


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    settings.require_strong_secret()
    return URLSafeTimedSerializer(settings.secret_key, salt=_COOKIE_SALT)


def issue_cookie(response: Response, op: Operator, settings: Settings) -> None:
    token = _serializer(settings).dumps({"op": op.id, "v": op.auth_version})
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_max_age_s,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="strict"
    )


def read_cookie(request: Request, settings: Settings) -> dict | None:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    try:
        return _serializer(settings).loads(token, max_age=settings.session_max_age_s)
    except (BadSignature, SignatureExpired):
        return None


class LoginError(Exception):
    def __init__(self, code: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        self.code = code
        self.status_code = status_code


async def authenticate(db: AsyncSession, username: str, password: str, totp: str, settings: Settings) -> Operator:
    """성공하면 운영자를 돌려주고, 실패는 LoginError. 실패 횟수·잠금·TOTP 사용 기록은 여기서 갱신한다.

    운영자 행을 FOR UPDATE 로 잠가 동시 로그인 시도가 실패 횟수를 덮어쓰지 못하게 한다.
    잠금 여부(423)는 비밀번호와 TOTP 가 모두 맞을 때만 알려 준다. 틀린 값으로는 아이디 존재·잠금을 알 수 없다.
    """
    op = (
        await db.execute(select(Operator).where(Operator.username == username).with_for_update())
    ).scalar_one_or_none()
    if op is None:
        await verify_password_async(password, _DUMMY_HASH)
        await db.rollback()
        raise LoginError("invalid_credentials")

    now = datetime.now(UTC)
    locked = op.locked_until is not None and op.locked_until > now
    password_ok = await verify_password_async(password, op.password_hash)
    step = match_totp_step(op.totp_secret, totp, now) if password_ok else None
    # 이미 쓴 TOTP 단계(또는 그 이전)는 다시 받지 않는다
    totp_ok = step is not None and (op.last_totp_step is None or step > op.last_totp_step)

    if not (password_ok and totp_ok):
        if not locked:
            op.failed_attempts += 1
            if op.failed_attempts >= settings.login_max_failures:
                op.locked_until = now + timedelta(seconds=settings.login_lock_s)
                op.failed_attempts = 0
        await db.commit()
        raise LoginError("invalid_credentials")

    if locked:
        # 맞는 값이어도 잠금 중에는 거부한다. 이때 사용한 TOTP 단계는 소모하지 않는다
        await db.rollback()
        raise LoginError("locked", status.HTTP_423_LOCKED)

    op.failed_attempts = 0
    op.locked_until = None
    op.last_totp_step = step
    await db.commit()
    return op


async def current_operator(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Operator:
    """로그인 쿠키를 검증하고, 활동 시 유효기간을 연장(쿠키 재발급)한다."""
    data = read_cookie(request, settings)
    if data is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_required")
    op = await db.get(Operator, data.get("op"))
    if op is None or op.auth_version != data.get("v"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_required")
    issue_cookie(response, op, settings)
    return op


async def revoke_sessions(db: AsyncSession, op_id: int) -> None:
    """auth_version 을 올려 이 운영자의 모든 로그인 쿠키를 무효화한다(모든 기기 로그아웃)."""
    op = (await db.execute(select(Operator).where(Operator.id == op_id).with_for_update())).scalar_one_or_none()
    if op is not None:
        op.auth_version += 1
    await db.commit()
