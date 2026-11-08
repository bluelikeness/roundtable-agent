"""봇 관리 REST (docs/05-ui.md 3·4·5장). 전부 허브 로그인이 필요하다.

어댑터 관리 HTTP(03 5장)를 허브가 대신 부른다. 브라우저는 어댑터 주소·토큰을 모른다.

- GET  /api/bots/<bot>/images/<id>   이미지 프록시(structured.images[]·cards[].image_url 의 /images/<id>)
- POST /api/bots/<bot>/login/start   {flow, hint, vnc_url}. 시트가 flow 로 CodeLogin·VncLogin 을 고른다
- POST /api/bots/<bot>/login/submit  코드 입력형 다음 단계
- POST /api/bots/<bot>/login/complete
- POST /api/bots/<bot>/probe         "상태 새로고침"
- POST /api/bots/<bot>/enable|disable
- POST /api/bots/<bot>/reset         /reset <봇>: {session_id} 면 그 세션의 봇 대화만, 없으면 전체
- GET  /api/bots/<bot>/logs          최근 ask 기록
- POST /api/sessions/<id>/restart    /restart: 다음 실행을 새 Claude 세션으로
- GET  /api/claude/status            봇 상태 패널 하단 Claude Code 섹션
- POST /internal/bot-status          어댑터 웹훅(X-Hub-Token). 웹(nginx)은 /internal 을 프록시하지 않는다
"""

import hmac
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_operator
from app.bot_monitor import AdminError, BotMonitor
from app.config import Settings, get_settings
from app.db import get_db
from app.events import BotStatusPayload
from app.models import ChatSession, Run

router = APIRouter(prefix="/api", dependencies=[Depends(current_operator)])
internal = APIRouter(prefix="/internal")

# 어댑터 image_id(base.store_image: 16자리 hex). 경로 조작을 막으려고 허브에서도 좁힌다
IMAGE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
# 브라우저에서 같은 출처로 열리므로 스크립트를 품을 수 있는 형식(SVG·HTML)은 내보내지 않는다
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}


def monitor_of(request: Request) -> BotMonitor:
    m = getattr(request.app.state, "bot_monitor", None)
    if m is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bot_monitor_unavailable")
    return m


def _http(e: AdminError) -> HTTPException:
    return HTTPException(e.status, e.code)


# --- 이미지 프록시 ---


@router.get("/bots/{bot}/images/{image_id}")
async def bot_image(bot: str, image_id: str, monitor: BotMonitor = Depends(monitor_of)) -> Response:
    if not IMAGE_ID_RE.match(image_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image_not_found")
    try:
        r = await monitor.call(bot, "GET", f"/images/{image_id}")
    except AdminError as e:
        raise _http(e) from None
    if r.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image_not_found")
    if r.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "adapter_error")
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in IMAGE_TYPES:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "unsupported_image_type")
    return Response(
        r.content,
        media_type=ctype,
        headers={
            # image_id 는 내용이 바뀌지 않는다. 로그인 뒤에만 보이므로 private
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


# --- 로그인 시트 ---


class LoginStartOut(BaseModel):
    flow: str
    hint: str | None = None
    # noVNC 프록시는 허브가 맡는다(P2). 그 전까지는 null. 어댑터 내부 주소는 내보내지 않는다
    vnc_url: str | None = None


class LoginSubmitIn(BaseModel):
    code: str | None = Field(None, max_length=64)
    password: str | None = Field(None, max_length=256)


@router.post("/bots/{bot}/login/start", response_model=LoginStartOut)
async def login_start(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> LoginStartOut:
    try:
        data = await monitor.call_json(bot, "POST", "/login/start", json={})
    except AdminError as e:
        raise _http(e) from None
    flow = data.get("flow") if isinstance(data, dict) else None
    if flow not in ("code", "vnc"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "adapter_bad_response")
    hint = data.get("hint")
    return LoginStartOut(flow=flow, hint=hint if isinstance(hint, str) else None)


@router.post("/bots/{bot}/login/submit")
async def login_submit(bot: str, body: LoginSubmitIn, monitor: BotMonitor = Depends(monitor_of)) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    if len(payload) != 1:
        raise HTTPException(422, "code_or_password_required")
    try:
        data = await monitor.call_json(bot, "POST", "/login/submit", json=payload)
    except AdminError as e:
        raise _http(e) from None
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "adapter_bad_response")
    out: dict[str, Any] = {"ok": bool(data.get("ok"))}
    if isinstance(data.get("next"), str):
        out["next"] = data["next"]
    if out["ok"]:
        await monitor.refresh(bot)
    return out


@router.post("/bots/{bot}/login/complete")
async def login_complete(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> dict[str, bool]:
    try:
        data = await monitor.call_json(bot, "POST", "/login/complete", json={})
    except AdminError as e:
        raise _http(e) from None
    await monitor.refresh(bot)
    return {"ok": bool(isinstance(data, dict) and data.get("ok"))}


# --- 상태·토글·초기화·로그 ---


@router.post("/bots/{bot}/probe", response_model=BotStatusPayload)
async def probe(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> BotStatusPayload:
    try:
        return await monitor.refresh(bot, probe=True)
    except AdminError as e:
        raise _http(e) from None


@router.post("/bots/{bot}/enable", response_model=BotStatusPayload)
async def enable(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> BotStatusPayload:
    try:
        return await monitor.set_enabled(bot, True)
    except AdminError as e:
        raise _http(e) from None


@router.post("/bots/{bot}/disable", response_model=BotStatusPayload)
async def disable(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> BotStatusPayload:
    try:
        return await monitor.set_enabled(bot, False)
    except AdminError as e:
        raise _http(e) from None


class ResetIn(BaseModel):
    # 허브 세션 = 어댑터 thread_id(orchestrator.md: thread_id 는 항상 session_id)
    session_id: uuid.UUID | None = None


@router.post("/bots/{bot}/reset")
async def reset(bot: str, body: ResetIn, monitor: BotMonitor = Depends(monitor_of)) -> dict[str, bool]:
    payload = {"thread_id": str(body.session_id)} if body.session_id else {}
    try:
        data = await monitor.call_json(bot, "POST", "/reset", json=payload)
    except AdminError as e:
        raise _http(e) from None
    return {"ok": bool(isinstance(data, dict) and data.get("ok"))}


@router.get("/bots/{bot}/logs")
async def logs(bot: str, monitor: BotMonitor = Depends(monitor_of)) -> Any:
    try:
        return await monitor.call_json(bot, "GET", "/logs")
    except AdminError as e:
        raise _http(e) from None


# --- /restart ---


@router.post("/sessions/{session_id}/restart")
async def restart_session(session_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    s = await db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    manager = getattr(request.app.state, "run_manager", None)
    if manager is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run_manager_unavailable")
    # 이어받을 Claude 세션이 없으면 다음 실행이 어차피 새 세션이다
    if s.claude_session_id:
        manager.request_restart(session_id)
    return {"ok": True, "had_session": bool(s.claude_session_id)}


# --- Claude Code 섹션 ---


class ClaudeStatusOut(BaseModel):
    runner_ok: bool
    runs_today: int
    tokens_today: int


def _tokens(usage: dict[str, Any] | None) -> int:
    if not usage:
        return 0
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return sum(v for k in keys if isinstance(v := usage.get(k), int))


@router.get("/claude/status", response_model=ClaudeStatusOut)
async def claude_status(
    request: Request, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ClaudeStatusOut:
    tz = timezone(timedelta(hours=settings.day_utc_offset_h))
    start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    usages = (await db.execute(select(Run.usage).where(Run.created_at >= start))).scalars().all()
    runner_ok = False
    runner = getattr(request.app.state, "run_manager", None)
    ping = getattr(getattr(runner, "runner", None), "ping", None)
    if ping is not None:
        runner_ok = await ping()
    return ClaudeStatusOut(runner_ok=runner_ok, runs_today=len(usages), tokens_today=sum(_tokens(u) for u in usages))


# --- 어댑터 웹훅 ---


class BotStatusHook(BaseModel):
    bot: str
    available: bool
    reason: str | None = Field(None, max_length=64)
    at: str | None = None


@internal.post("/bot-status", status_code=status.HTTP_204_NO_CONTENT)
async def bot_status_hook(
    body: BotStatusHook,
    request: Request,
    x_hub_token: str = Header(""),
    settings: Settings = Depends(get_settings),
) -> None:
    token = settings.read_adapter_token()
    if not token or not hmac.compare_digest(x_hub_token.encode(), token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_token")
    monitor = monitor_of(request)
    if body.bot not in {b.name for b in monitor.bots()}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bot_not_found")
    await monitor.apply(body.bot, available=body.available, reason=body.reason)
