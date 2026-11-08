"""봇 등록(bots.yaml)과 실행 직전 가용 봇 선택 (02 6장 bot 상태 머신, 03 10장).

Claude 에 노출되는 봇은 enabled·available 둘 다 참인 봇만이다. bot 테이블에 행이 없는 봇은
헬스체크 결과가 아직 없는 것이라 일단 노출한다. 헬스체크 폴링은 app/bot_monitor.py 가 한다.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bot
from app.runner_protocol import BotEndpoint


class BotConfig(BaseModel):
    name: str
    mcp_url: str
    admin_url: str | None = None
    aliases: list[str] = Field(default_factory=list)
    color: str | None = None


def load_bots(path: str | Path) -> list[BotConfig]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    bots = [BotConfig.model_validate(b) for b in data.get("bots") or []]
    for b in bots:
        BotEndpoint(name=b.name, mcp_url=b.mcp_url)  # 이름 규칙 검사
    return bots


async def available_endpoints(db: AsyncSession, bots: list[BotConfig]) -> list[BotEndpoint]:
    rows = {b.name: b for b in (await db.execute(select(Bot))).scalars()}
    out = []
    for b in bots:
        row = rows.get(b.name)
        if row is not None and not (row.enabled and row.available):
            continue
        out.append(BotEndpoint(name=b.name, mcp_url=b.mcp_url))
    return out
