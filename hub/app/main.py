"""허브 API 진입점."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.db import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await dispose_engine()


app = FastAPI(title="multibot hub", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
