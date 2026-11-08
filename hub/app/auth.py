"""운영자 인증: bcrypt 비밀번호 + TOTP, 서명된 세션 쿠키 (docs/05-ui.md 7장)."""

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


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    # 시계 오차를 고려해 앞뒤 1스텝(30초)까지 허용
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
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


class LoginError(Exception):
    def __init__(self, code: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        self.code = code
        self.status_code = status_code


async def authenticate(db: AsyncSession, username: str, password: str, totp: str, settings: Settings) -> Operator:
    """성공하면 운영자를 돌려주고, 실패는 LoginError. 실패 횟수·잠금은 여기서 갱신한다."""
    op = (await db.execute(select(Operator).where(Operator.username == username))).scalar_one_or_none()
    now = datetime.now(UTC)
    if op is None:
        # 존재하지 않는 아이디도 같은 오류로 응답한다
        raise LoginError("invalid_credentials")
    if op.locked_until is not None and op.locked_until > now:
        raise LoginError("locked", status.HTTP_423_LOCKED)

    if not (verify_password(password, op.password_hash) and verify_totp(op.totp_secret, totp)):
        op.failed_attempts += 1
        if op.failed_attempts >= settings.login_max_failures:
            op.locked_until = now + timedelta(seconds=settings.login_lock_s)
            op.failed_attempts = 0
        await db.commit()
        raise LoginError("invalid_credentials")

    op.failed_attempts = 0
    op.locked_until = None
    await db.commit()
    return op


async def current_operator(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Operator:
    """로그인 쿠키를 검증하고, 활동 시 유효기간을 연장(쿠키 재발급)한다."""
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_required")
    try:
        data = _serializer(settings).loads(token, max_age=settings.session_max_age_s)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_required") from None
    op = await db.get(Operator, data.get("op"))
    if op is None or op.auth_version != data.get("v"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_required")
    issue_cookie(response, op, settings)
    return op
