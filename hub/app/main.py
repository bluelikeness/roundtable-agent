"""허브 API 진입점."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import get_settings
from app.db import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 서명 키가 약하면 기동 단계에서 바로 멈춘다
    get_settings().require_strong_secret()
    yield
    await dispose_engine()


app = FastAPI(title="multibot hub", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
