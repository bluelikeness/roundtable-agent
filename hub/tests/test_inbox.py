"""바깥 입구 POST /api/inbox: 토큰, 방 찾기, 사용자 메시지로 넣어 실행."""

import asyncio

import pytest
from sqlalchemy import select

from app import inbox as inbox_mod
from app.config import Settings, get_settings
from app.main import app
from app.models import ChatSession, Message, Run
from tests.test_ws import fake_runner  # noqa: F401  러너 대역 픽스처를 그대로 쓴다

TOKEN = "inbox-test-token-0123456789abcdefghij"


@pytest.fixture
def inbox_on():
    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{**base.model_dump(), "inbox_token": TOKEN, "inbox_per_minute": 3}
    )
    inbox_mod._recent.clear()
    yield
    app.dependency_overrides.pop(get_settings, None)
    inbox_mod._recent.clear()


async def _rooms(
    db, *titles: str, archived: str | None = None
) -> dict[str, ChatSession]:
    out = {}
    for t in titles:
        s = ChatSession(title=t)
        db.add(s)
        out[t] = s
    if archived:
        db.add(ChatSession(title=archived, archived=True))
    await db.commit()
    return out


def _h(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_closed_without_token(client):
    r = await client.post("/api/inbox", json={"room": "a", "text": "b"}, headers=_h())
    assert r.status_code == 404


async def test_rejects_wrong_token(client, inbox_on):
    r = await client.post(
        "/api/inbox", json={"room": "a", "text": "b"}, headers=_h("wrong")
    )
    assert r.status_code == 401
    r = await client.post("/api/inbox", json={"room": "a", "text": "b"})
    assert r.status_code == 401


async def test_posts_into_room_and_runs(client, db, inbox_on, fake_runner):  # noqa: F811
    rooms = await _rooms(db, "주말 회의방", "여행 계획")
    r = await client.post(
        "/api/inbox",
        json={"room": "주말회의방", "text": "토요일이 좋아요", "from": "피클"},
        headers=_h(),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["room"] == "주말 회의방"
    assert body["session_id"] == str(rooms["주말 회의방"].id)
    msg = (
        (
            await db.execute(
                select(Message).where(Message.session_id == rooms["주말 회의방"].id)
            )
        )
        .scalars()
        .first()
    )
    assert msg.raw == "(피클에서 보낸 의견) 토요일이 좋아요"
    for _ in range(50):
        if fake_runner.sessions:
            break
        await asyncio.sleep(0.02)
    assert (
        fake_runner.sessions
        and fake_runner.sessions[0].session_id == rooms["주말 회의방"].id
    )
    assert (await db.execute(select(Run))).scalars().first() is not None


async def test_partial_match_and_not_found(client, db, inbox_on, fake_runner):  # noqa: F811
    await _rooms(db, "주말 회의방", "주말 여행", "독서", archived="보관된 방")
    r = await client.post("/api/inbox", json={"room": "독", "text": "x"}, headers=_h())
    assert r.status_code == 202
    # 부분 일치가 둘 이상이면 고르지 않고 후보를 돌려준다
    r = await client.post(
        "/api/inbox", json={"room": "주말", "text": "x"}, headers=_h()
    )
    assert r.status_code == 404
    assert sorted(r.json()["rooms"]) == ["주말 여행", "주말 회의방"]
    # 보관된 방은 찾지 않는다
    r = await client.post(
        "/api/inbox", json={"room": "보관된 방", "text": "x"}, headers=_h()
    )
    assert r.status_code == 404
    assert "보관된 방" not in r.json()["rooms"]


async def test_limits(client, db, inbox_on, fake_runner):  # noqa: F811
    await _rooms(db, "방")
    r = await client.post(
        "/api/inbox", json={"room": "방", "text": "x" * 5000}, headers=_h()
    )
    assert r.status_code == 413
    r = await client.post(
        "/api/inbox", json={"room": "방", "text": "   "}, headers=_h()
    )
    assert r.status_code == 422
    codes = [
        (
            await client.post(
                "/api/inbox", json={"room": "방", "text": f"{i}"}, headers=_h()
            )
        ).status_code
        for i in range(4)
    ]
    assert codes == [202, 202, 202, 429]
