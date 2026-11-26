"""허브 API 진입점."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.bot_monitor import BotMonitor
from app.bots_api import internal as internal_router
from app.bots_api import router as bots_router
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.files import internal as files_internal_router
from app.files import purge_loop
from app.files import router as files_router
from app.inbox import router as inbox_router
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
    monitor = BotMonitor(get_sessionmaker(), settings, manager.broadcast_global)
    await monitor.start()
    app.state.bot_monitor = monitor
    # 보관 기간 지난 첨부·봇 파일 정리(하루에 한 번)
    purger = asyncio.create_task(purge_loop(get_sessionmaker(), settings), name="files:purge")
    try:
        yield
    finally:
        purger.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await purger
        await monitor.aclose()
        await manager.shutdown()
        if isinstance(runner, RemoteRunner):
            await runner.aclose()
        await dispose_engine()


app = FastAPI(title="Roundtable-Agent hub", lifespan=lifespan)
app.include_router(router)
app.include_router(ws_router)
app.include_router(bots_router)
app.include_router(internal_router)
app.include_router(inbox_router)
app.include_router(files_router)
app.include_router(files_internal_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
