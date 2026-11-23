"""열린 방 R1 계약 (docs/proposals/open-rooms.md 5장):
키별 신원, 재발급 후 옛 키 401, 속도 제한 429, 닫힌 방 410, 잠깐 빼기 403."""

import pytest
from sqlalchemy import select

from app import rooms as rooms_mod
from app.config import Settings, get_settings
from app.main import app
from app.models import Participant


@pytest.fixture(autouse=True)
def _reset_rate():
    rooms_mod._reads.clear()
    rooms_mod._sends.clear()
    rooms_mod._joins.clear()
    yield
    rooms_mod._reads.clear()
    rooms_mod._sends.clear()
    rooms_mod._joins.clear()
    app.dependency_overrides.pop(get_settings, None)


def _limits(reads: int = 100, sends: int = 100, max_chars: int = 4000):
    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{
            **base.model_dump(),
            "room_reads_per_minute": reads,
            "room_sends_per_minute": sends,
            "room_max_chars": max_chars,
        }
    )


async def _room(authed, name: str = "토론방") -> str:
    r = await authed.post("/api/rooms", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add(authed, room_id: str, name: str, kind: str = "ai", **kw) -> dict:
    r = await authed.post(
        f"/api/rooms/{room_id}/participants", json={"name": name, "kind": kind, **kw}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _k(key: str) -> dict[str, str]:
    return {"x-key": key}


async def test_operator_routes_need_login(client):
    assert (await client.get("/api/rooms")).status_code == 401
    assert (await client.post("/api/rooms", json={"name": "x"})).status_code == 401


async def test_key_is_identity(authed, db):
    rid = await _room(authed)
    klo = await _add(authed, rid, "클로")
    mu = await _add(authed, rid, "뮤즈", check_every_s=30)
    assert klo["key"].startswith("rk_") and klo["key"] != mu["key"]

    # 키 원문은 DB 에 없다
    rows = (await db.execute(select(Participant))).scalars().all()
    assert all(r.key_hash != klo["key"] and len(r.key_hash) == 64 for r in rows)

    r = await authed.post(
        "/api/p/send",
        json={"text": "안녕하세요", "who": "뮤즈"},
        headers=_k(klo["key"]),
    )
    assert r.status_code == 200, r.text
    first_id = r.json()["id"]

    r = await authed.get("/api/p/msgs?since=0", headers=_k(mu["key"]))
    assert r.status_code == 200
    body = r.json()
    assert (
        body["me"] == "뮤즈"
        and body["check_every_s"] == 30
        and body["room"] == "토론방"
    )
    # 본문에 다른 이름을 적어도 보낸 이는 키의 주인
    assert [(m["who"], m["kind"], m["text"]) for m in body["msgs"]] == [
        ("클로", "ai", "안녕하세요")
    ]

    await authed.post("/api/p/send", json={"text": "반가워요"}, headers=_k(mu["key"]))
    r = await authed.get(f"/api/p/msgs?since={first_id}", headers=_k(klo["key"]))
    assert [m["who"] for m in r.json()["msgs"]] == ["뮤즈"]

    # 운영자도 같은 글을 본다, 참가자 목록에는 키가 없다
    r = await authed.get(f"/api/rooms/{rid}/messages")
    assert [m["text"] for m in r.json()["msgs"]] == ["안녕하세요", "반가워요"]
    r = await authed.get(f"/api/rooms/{rid}/participants")
    assert {p["name"] for p in r.json()} == {"클로", "뮤즈"}
    assert all("key" not in p for p in r.json())
    assert all(p["last_seen_at"] for p in r.json())


async def test_bad_key_401(client, authed):
    rid = await _room(authed)
    await _add(authed, rid, "클로")
    assert (await client.get("/api/p/msgs")).status_code == 401
    assert (await client.get("/api/p/msgs", headers=_k("rk_nope"))).status_code == 401
    assert (
        await client.post("/api/p/send", json={"text": "x"}, headers=_k("garbage"))
    ).status_code == 401


async def test_rooms_are_isolated(authed):
    a = await _room(authed, "A방")
    b = await _room(authed, "B방")
    pa = await _add(authed, a, "클로")
    pb = await _add(authed, b, "클로")  # 방이 다르면 같은 이름 가능
    await authed.post("/api/p/send", json={"text": "A 전용"}, headers=_k(pa["key"]))
    r = await authed.get("/api/p/msgs", headers=_k(pb["key"]))
    assert r.json()["room"] == "B방" and r.json()["msgs"] == []


async def test_duplicate_name_409(authed):
    rid = await _room(authed)
    await _add(authed, rid, "클로")
    r = await authed.post(
        f"/api/rooms/{rid}/participants", json={"name": "클로", "kind": "human"}
    )
    assert r.status_code == 409 and r.json()["detail"] == "name_taken"


async def test_rotate_invalidates_old_key(authed):
    rid = await _room(authed)
    p = await _add(authed, rid, "제미")
    r = await authed.post(f"/api/rooms/{rid}/participants/{p['id']}/rotate")
    assert r.status_code == 200
    new = r.json()["key"]
    assert new != p["key"]
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 401
    assert (await authed.get("/api/p/msgs", headers=_k(new))).status_code == 200


async def test_remove_keeps_messages(authed):
    rid = await _room(authed)
    p = await _add(authed, rid, "익스")
    await authed.post("/api/p/send", json={"text": "남는 글"}, headers=_k(p["key"]))
    assert (
        await authed.delete(f"/api/rooms/{rid}/participants/{p['id']}")
    ).status_code == 204
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 401
    r = await authed.get(f"/api/rooms/{rid}/messages")
    assert [(m["who"], m["text"]) for m in r.json()["msgs"]] == [("익스", "남는 글")]


async def test_pause_403_and_resume(authed):
    rid = await _room(authed)
    p = await _add(authed, rid, "키리")
    r = await authed.patch(
        f"/api/rooms/{rid}/participants/{p['id']}",
        json={"enabled": False, "role": "leader"},
    )
    assert r.status_code == 200 and r.json()["role"] == "leader"
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 403
    await authed.patch(
        f"/api/rooms/{rid}/participants/{p['id']}", json={"enabled": True}
    )
    r = await authed.get("/api/p/msgs", headers=_k(p["key"]))
    assert r.status_code == 200 and r.json()["role"] == "leader"


async def test_closed_room_410(authed):
    rid = await _room(authed)
    p = await _add(authed, rid, "채이")
    assert (await authed.delete(f"/api/rooms/{rid}")).json()["closed_at"]
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 410
    assert (
        await authed.post("/api/p/send", json={"text": "x"}, headers=_k(p["key"]))
    ).status_code == 410
    r = await authed.post(
        f"/api/rooms/{rid}/participants", json={"name": "새", "kind": "ai"}
    )
    assert r.status_code == 409


async def test_send_rate_limit_429(authed):
    _limits(sends=2)
    rid = await _room(authed)
    p = await _add(authed, rid, "부이")
    for i in range(2):
        assert (
            await authed.post(
                "/api/p/send", json={"text": f"{i}"}, headers=_k(p["key"])
            )
        ).status_code == 200
    r = await authed.post("/api/p/send", json={"text": "셋째"}, headers=_k(p["key"]))
    assert r.status_code == 429
    assert r.json()["retry_after_s"] >= 1 and int(r.headers["Retry-After"]) >= 1
    # 다른 참가자는 영향 없음
    q = await _add(authed, rid, "큐이")
    assert (
        await authed.post("/api/p/send", json={"text": "ok"}, headers=_k(q["key"]))
    ).status_code == 200


async def test_read_rate_limit_429(authed):
    _limits(reads=2)
    rid = await _room(authed)
    p = await _add(authed, rid, "피클이")
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 200
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 200
    assert (await authed.get("/api/p/msgs", headers=_k(p["key"]))).status_code == 429


async def test_text_validation(authed):
    _limits(max_chars=10)
    rid = await _room(authed)
    p = await _add(authed, rid, "클로")
    assert (
        await authed.post("/api/p/send", json={"text": "   "}, headers=_k(p["key"]))
    ).status_code == 422
    assert (
        await authed.post("/api/p/send", json={"text": "x" * 11}, headers=_k(p["key"]))
    ).status_code == 413


async def test_history_window(authed):
    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{
            **base.model_dump(),
            "room_history": 3,
            "room_sends_per_minute": 100,
            "room_reads_per_minute": 100,
        }
    )
    rid = await _room(authed)
    p = await _add(authed, rid, "클로")
    for i in range(5):
        await authed.post("/api/p/send", json={"text": str(i)}, headers=_k(p["key"]))
    r = await authed.get("/api/p/msgs?since=0", headers=_k(p["key"]))
    assert [m["text"] for m in r.json()["msgs"]] == ["2", "3", "4"]


# --- R2: 초대 링크, 운영자 글쓰기, 참가자 목록 ---


async def _invite(authed, rid: str) -> str:
    r = await authed.post(f"/api/rooms/{rid}/invite")
    assert r.status_code == 200, r.text
    return r.json()["invite"]


async def test_invite_join_and_chat(client, authed):
    rid = await _room(authed, "모임방")
    ai = await _add(authed, rid, "클로")
    inv = await _invite(authed, rid)
    assert inv.startswith("ri_")
    rooms = (await authed.get("/api/rooms")).json()
    assert rooms[0]["invite_on"] is True

    # 초대받은 사람은 로그인 없이 방 이름·참가자를 보고 이름만 써서 들어온다
    await client.post("/api/auth/logout")
    r = await client.get(f"/api/join/{inv}")
    assert r.status_code == 200
    assert r.json()["room"] == "모임방" and r.json()["people"] == [
        {"name": "클로", "kind": "ai"}
    ]
    r = await client.post(f"/api/join/{inv}", json={"name": "철수"})
    assert r.status_code == 201, r.text
    human = r.json()["key"]
    assert r.json()["me"] == "철수"

    await client.post("/api/p/send", json={"text": "안녕 클로"}, headers=_k(human))
    r = await client.get("/api/p/msgs", headers=_k(ai["key"]))
    assert [(m["who"], m["kind"]) for m in r.json()["msgs"]] == [("철수", "human")]
    r = await client.get("/api/p/participants", headers=_k(human))
    assert {(p["name"], p["kind"]) for p in r.json()} == {
        ("클로", "ai"),
        ("철수", "human"),
    }

    # 같은 이름은 409
    r = await client.post(f"/api/join/{inv}", json={"name": "철수"})
    assert r.status_code == 409


async def test_operator_send_shows_as_operator(authed):
    rid = await _room(authed)
    ai = await _add(authed, rid, "클로")
    r = await authed.post(f"/api/rooms/{rid}/messages", json={"text": "@클로 시작해"})
    assert r.status_code == 200
    r = await authed.get("/api/p/msgs", headers=_k(ai["key"]))
    assert [(m["who"], m["kind"], m["text"]) for m in r.json()["msgs"]] == [
        ("operator", "human", "@클로 시작해")
    ]


async def test_invite_rotate_and_stop(client, authed):
    rid = await _room(authed)
    old = await _invite(authed, rid)
    new = await _invite(authed, rid)
    assert (await client.get(f"/api/join/{old}")).status_code == 404
    assert (await client.get(f"/api/join/{new}")).status_code == 200
    r = await authed.delete(f"/api/rooms/{rid}/invite")
    assert r.json()["invite_on"] is False
    assert (await client.get(f"/api/join/{new}")).status_code == 404
    assert (await client.get("/api/join/garbage")).status_code == 404


async def test_join_closed_room_410(client, authed):
    rid = await _room(authed)
    inv = await _invite(authed, rid)
    await authed.delete(f"/api/rooms/{rid}")
    assert (
        await client.post(f"/api/join/{inv}", json={"name": "영희"})
    ).status_code == 410


async def test_room_full_and_join_rate(client, authed):
    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{**base.model_dump(), "room_max_participants": 2, "room_joins_per_minute": 3}
    )
    rid = await _room(authed)
    inv = await _invite(authed, rid)
    assert (
        await client.post(f"/api/join/{inv}", json={"name": "a"})
    ).status_code == 201
    assert (
        await client.post(f"/api/join/{inv}", json={"name": "b"})
    ).status_code == 201
    r = await client.post(f"/api/join/{inv}", json={"name": "c"})
    assert r.status_code == 409 and r.json()["detail"] == "room_full"
    r = await authed.post(
        f"/api/rooms/{rid}/participants", json={"name": "d", "kind": "ai"}
    )
    assert r.status_code == 409
    # 분당 입장 시도 상한(3회 시도 후 넷째는 429)
    assert (
        await client.post(f"/api/join/{inv}", json={"name": "e"})
    ).status_code == 429
