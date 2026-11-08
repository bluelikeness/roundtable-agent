"""허브 API 진입점."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.runner_client import RemoteRunner
from app.runs import RunManager
from app.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # 서명 키가 약하면 기동 단계에서 바로 멈춘다
    settings.require_strong_secret()
    # 테스트는 app.state.runner 에 가짜 러너를 미리 넣는다
    runner = getattr(app.state, "runner", None) or RemoteRunner(settings.runner_url, settings.read_adapter_token())
    manager = RunManager(get_sessionmaker(), runner, settings)
    await manager.recover()
    app.state.run_manager = manager
    try:
        yield
    finally:
        await manager.shutdown()
        if isinstance(runner, RemoteRunner):
            await runner.aclose()
        await dispose_engine()


app = FastAPI(title="multibot hub", lifespan=lifespan)
app.include_router(router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
