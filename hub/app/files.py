"""허브 파일 보관함 (docs/03-adapters.md 2장 첨부, docs/05-ui.md 3·6장).

방에 올린 파일과 봇이 만든 파일을 같은 ID(f_…)로 보관한다. Claude 는 ID 만 들고 다니며
ask 의 attachments 로 다음 봇에게 넘긴다. 어댑터는 그 ID 로 본문을 받아 간다.

- POST /api/files            운영자 업로드. 본문 그대로, Content-Type 이 MIME, 이름은 X-File-Name(URL 인코딩)
- GET  /api/files/{id}       운영자 보기·내려받기(이미지·영상·소리는 바로 보기, 그 밖은 내려받기)
- POST /internal/files       어댑터가 봇이 만든 파일을 올린다(X-Hub-Token, X-Bot)
- GET  /internal/files/{id}  어댑터가 첨부를 받아 간다(X-Hub-Token)

웹(nginx)은 /internal 을 프록시하지 않는다.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import current_operator
from app.config import Settings, get_settings
from app.db import get_db
from app.models import HubFile

log = logging.getLogger("hub.files")

router = APIRouter(prefix="/api/files", dependencies=[Depends(current_operator)])
internal = APIRouter(prefix="/internal/files")

FILE_ID_RE = re.compile(r"^f_[A-Za-z0-9_-]{16,64}$")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$")
# 브라우저에서 바로 열어도 스크립트가 돌지 않는 종류만 inline. svg·html 은 내려받기로 준다
_INLINE = re.compile(r"^(image/(png|jpeg|gif|webp|avif|heic)|video/[a-z0-9.+-]+|audio/[a-z0-9.+-]+)$")
# 한 메시지·한 ask 에 붙이는 최대 개수
MAX_ATTACHMENTS = 5


class FileOut(BaseModel):
    """첨부·structured.files[] 한 항목. url 은 붙이지 않는다(웹은 /api/files/<id>)."""

    id: str
    name: str
    mime: str
    size: int


def file_out(f: HubFile) -> dict[str, Any]:
    return FileOut(id=f.id, name=f.name, mime=f.mime, size=f.size).model_dump()


def _clean_name(raw: str | None) -> str:
    name = unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().lstrip(".")
    return name[:200] or "file"


def _clean_mime(raw: str | None) -> str:
    mime = (raw or "").split(";", 1)[0].strip().lower()
    return mime if _MIME_RE.match(mime) else "application/octet-stream"


def _path(settings: Settings, file_id: str) -> Path:
    return Path(settings.files_dir) / file_id


async def store(
    db: AsyncSession, settings: Settings, request: Request, *, name: str | None, mime: str | None, source: str
) -> HubFile:
    """요청 본문을 디스크에 흘려 쓰고 행을 만든다. 상한을 넘으면 413."""
    limit = settings.file_max_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    file_id = "f_" + secrets.token_urlsafe(16)
    path = _path(settings, file_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    row = HubFile(
        id=file_id, name=_clean_name(name), mime=_clean_mime(mime), size=size, sha256=digest.hexdigest(), source=source
    )
    db.add(row)
    await db.commit()
    log.info("파일 보관: %s %s %d bytes (%s)", file_id, row.mime, size, source)
    return row


async def load_many(db: AsyncSession, ids: list[str]) -> list[HubFile]:
    """ID 순서대로 돌려준다. 없거나 모양이 틀린 ID 가 있으면 ValueError(그 ID)."""
    for i in ids:
        if not FILE_ID_RE.match(i):
            raise ValueError(i)
    rows = {r.id: r for r in (await db.execute(select(HubFile).where(HubFile.id.in_(ids)))).scalars()}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise ValueError(missing[0])
    return [rows[i] for i in ids]


def describe_for_claude(files: list[dict[str, Any]]) -> str:
    """러너에 넘기는 글 끝에 붙이는 첨부 목록. Claude 는 이 ID 를 ask 의 attachments 로 넘긴다."""
    lines = [f"- {f['id']} · {f['name']} · {f['mime']} · {_human(f['size'])}" for f in files]
    return "[첨부 파일] (봇에게 넘기려면 ask 의 attachments 에 ID 를 넣는다)\n" + "\n".join(lines)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}B"


def _serve(settings: Settings, row: HubFile) -> FileResponse:
    path = _path(settings, row.id)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    inline = bool(_INLINE.match(row.mime))
    return FileResponse(
        path,
        media_type=row.mime,
        filename=row.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            # 같은 주소에서 열려도 스크립트·폼이 돌지 않게
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; media-src 'self'",
            "Cache-Control": "private, max-age=86400",
        },
    )


async def _get(db: AsyncSession, file_id: str) -> HubFile:
    row = await db.get(HubFile, file_id) if FILE_ID_RE.match(file_id) else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    return row


# --- 운영자 ---


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    x_file_name: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    row = await store(db, settings, request, name=x_file_name, mime=request.headers.get("content-type"), source="upload")
    return file_out(row)


@router.get("/{file_id}")
async def download(
    file_id: str, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    return _serve(settings, await _get(db, file_id))


# --- 어댑터 ---


def _check_token(settings: Settings, given: str) -> None:
    token = settings.read_adapter_token()
    if not token or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_token")


@internal.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def bot_upload(
    request: Request,
    x_hub_token: str = Header(""),
    x_bot: str = Header("bot", max_length=64),
    x_file_name: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _check_token(settings, x_hub_token)
    bot = re.sub(r"[^a-z0-9_-]", "", x_bot.lower())[:64] or "bot"
    row = await store(
        db, settings, request, name=x_file_name, mime=request.headers.get("content-type"), source=f"bot:{bot}"
    )
    return file_out(row)


@internal.get("/{file_id}")
async def bot_download(
    file_id: str,
    x_hub_token: str = Header(""),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _check_token(settings, x_hub_token)
    return _serve(settings, await _get(db, file_id))


# --- 보관 기간 ---


async def purge_old(sm: async_sessionmaker[AsyncSession], settings: Settings) -> int:
    """보관 일수가 지난 파일을 지운다. 지운 수를 돌려준다."""
    if settings.file_retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=settings.file_retention_days)
    async with sm() as db:
        ids = list((await db.execute(select(HubFile.id).where(HubFile.created_at < cutoff))).scalars())
        if not ids:
            return 0
        await db.execute(delete(HubFile).where(HubFile.id.in_(ids)))
        await db.commit()
    for i in ids:
        with contextlib.suppress(FileNotFoundError):
            _path(settings, i).unlink()
    log.info("보관 기간 지난 파일 %d개 지움", len(ids))
    return len(ids)


async def purge_loop(sm: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    while True:
        try:
            await purge_old(sm, settings)
        except Exception:  # noqa: BLE001 - 정리 실패로 허브가 멈추지 않게
            log.exception("파일 정리 실패")
        await asyncio.sleep(24 * 3600)
