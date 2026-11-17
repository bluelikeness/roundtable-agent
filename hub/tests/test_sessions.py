"""세션 CRUD 와 메시지 조회."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models import ChatSession, Message, MessageRole, Run, ToolCall


async def test_create_get_list(authed):
    r = await authed.post("/api/sessions", json={"title": "점심 식당"})
    assert r.status_code == 201
    s = r.json()
    assert s["title"] == "점심 식당" and s["archived"] is False and s["last_message_at"] is None
    uuid.UUID(s["id"])

    assert (await authed.get(f"/api/sessions/{s['id']}")).json()["id"] == s["id"]
    assert [x["id"] for x in (await authed.get("/api/sessions")).json()] == [s["id"]]

    # 제목 없이도 만들 수 있다(첫 메시지 앞 20자로 나중에 채움)
    r2 = await authed.post("/api/sessions", json={})
    assert r2.status_code == 201 and r2.json()["title"] is None


async def test_list_order_by_latest_activity(authed, db):
    now = datetime.now(UTC)
    old = ChatSession(title="old", last_message_at=now - timedelta(hours=2))
    recent = ChatSession(title="recent", last_message_at=now)
    empty = ChatSession(title="empty")  # 메시지 없음 → 뒤로
    db.add_all([old, recent, empty])
    await db.commit()
    titles = [x["title"] for x in (await authed.get("/api/sessions")).json()]
    assert titles == ["recent", "old", "empty"]


async def test_patch_title_and_archive(authed):
    sid = (await authed.post("/api/sessions", json={"title": "a"})).json()["id"]
    r = await authed.patch(f"/api/sessions/{sid}", json={"title": "b"})
    assert r.json()["title"] == "b" and r.json()["archived"] is False

    r = await authed.patch(f"/api/sessions/{sid}", json={"archived": True})
    assert r.json()["archived"] is True and r.json()["title"] == "b"
    assert (await authed.get("/api/sessions")).json() == []
    assert [x["id"] for x in (await authed.get("/api/sessions?archived=true")).json()] == [sid]


async def test_delete_cascades(authed, db):
    sid = (await authed.post("/api/sessions", json={})).json()["id"]
    m = Message(session_id=uuid.UUID(sid), role=MessageRole.user, raw="hi", seq=1)
    db.add(m)
    await db.flush()
    run = Run(session_id=uuid.UUID(sid), user_message_id=m.id)
    db.add(run)
    await db.flush()
    db.add(ToolCall(id="toolu_1", run_id=run.id, bot_name="mock-a"))
    await db.commit()

    # 영구 삭제는 휴지통을 거친다
    assert (await authed.delete(f"/api/sessions/{sid}")).status_code == 409
    assert (await authed.patch(f"/api/sessions/{sid}", json={"trashed": True})).status_code == 200
    assert (await authed.delete(f"/api/sessions/{sid}")).status_code == 204
    assert (await authed.get(f"/api/sessions/{sid}")).status_code == 404
    for model in (Message, Run, ToolCall):
        assert (await db.execute(select(func.count()).select_from(model))).scalar_one() == 0


async def test_not_found(authed):
    missing = uuid.uuid4()
    assert (await authed.get(f"/api/sessions/{missing}")).status_code == 404
    assert (await authed.patch(f"/api/sessions/{missing}", json={"title": "x"})).status_code == 404
    assert (await authed.delete(f"/api/sessions/{missing}")).status_code == 404
    assert (await authed.get(f"/api/sessions/{missing}/messages")).status_code == 404
    assert (await authed.get("/api/sessions/not-a-uuid")).status_code == 422


async def test_messages_ordered_and_since_seq(authed, db):
    sid = uuid.UUID((await authed.post("/api/sessions", json={})).json()["id"])
    db.add_all(
        [
            Message(session_id=sid, role=MessageRole.bot, bot_name="mock-a", raw="원문", seq=2,
                    structured={"buttons": [{"label": "예약", "action_id": "btn_1"}]}),
            Message(session_id=sid, role=MessageRole.user, raw="질문", seq=1),
            Message(session_id=sid, role=MessageRole.claude, raw="정리", seq=3),
        ]
    )
    await db.commit()

    msgs = (await authed.get(f"/api/sessions/{sid}/messages")).json()
    assert [m["seq"] for m in msgs] == [1, 2, 3]
    assert [m["role"] for m in msgs] == ["user", "bot", "claude"]
    assert msgs[1]["bot_name"] == "mock-a"
    assert msgs[1]["structured"]["buttons"][0]["action_id"] == "btn_1"

    since = (await authed.get(f"/api/sessions/{sid}/messages?since_seq=2")).json()
    assert [m["seq"] for m in since] == [3]


async def test_messages_isolated_per_session(authed, db):
    a = uuid.UUID((await authed.post("/api/sessions", json={})).json()["id"])
    b = uuid.UUID((await authed.post("/api/sessions", json={})).json()["id"])
    db.add_all([Message(session_id=a, role=MessageRole.user, raw="a", seq=1),
                Message(session_id=b, role=MessageRole.user, raw="b", seq=1)])
    await db.commit()
    assert [m["raw"] for m in (await authed.get(f"/api/sessions/{a}/messages")).json()] == ["a"]


async def test_groups_and_moving_sessions(authed):
    g1 = (await authed.post("/api/groups", json={"name": " 여행 "})).json()
    g2 = (await authed.post("/api/groups", json={"name": "업무"})).json()
    assert g1["name"] == "여행" and g2["position"] > g1["position"]
    assert [g["name"] for g in (await authed.get("/api/groups")).json()] == ["여행", "업무"]

    sid = (await authed.post("/api/sessions", json={"title": "제주", "group_id": g1["id"]})).json()["id"]
    r = await authed.patch(f"/api/sessions/{sid}", json={"group_id": g2["id"]})
    assert r.json()["group_id"] == g2["id"]
    assert (await authed.patch(f"/api/sessions/{sid}", json={"group_id": str(uuid.uuid4())})).status_code == 404
    # null 이면 그룹 없음
    assert (await authed.patch(f"/api/sessions/{sid}", json={"group_id": None})).json()["group_id"] is None

    await authed.patch(f"/api/sessions/{sid}", json={"group_id": g1["id"]})
    assert (await authed.patch(f"/api/groups/{g1['id']}", json={"name": "국내 여행"})).json()["name"] == "국내 여행"
    # 그룹을 지우면 세션은 그룹 없음으로 남는다
    assert (await authed.delete(f"/api/groups/{g1['id']}")).status_code == 204
    s = (await authed.get(f"/api/sessions/{sid}")).json()
    assert s["group_id"] is None and s["title"] == "제주"
    assert [g["name"] for g in (await authed.get("/api/groups")).json()] == ["업무"]


async def test_trash_restore_and_empty(authed):
    a = (await authed.post("/api/sessions", json={"title": "a"})).json()["id"]
    b = (await authed.post("/api/sessions", json={"title": "b"})).json()["id"]
    r = await authed.patch(f"/api/sessions/{a}", json={"trashed": True})
    assert r.json()["trashed_at"] is not None
    assert [x["id"] for x in (await authed.get("/api/sessions")).json()] == [b]
    assert [x["id"] for x in (await authed.get("/api/sessions?trashed=true")).json()] == [a]
    # 되살리기
    assert (await authed.patch(f"/api/sessions/{a}", json={"trashed": False})).json()["trashed_at"] is None
    assert len((await authed.get("/api/sessions")).json()) == 2
    # 비우기는 휴지통만
    await authed.patch(f"/api/sessions/{b}", json={"trashed": True})
    assert (await authed.delete("/api/trash")).status_code == 204
    assert (await authed.get(f"/api/sessions/{b}")).status_code == 404
    assert (await authed.get(f"/api/sessions/{a}")).status_code == 200


async def test_session_model(authed):
    sid = (await authed.post("/api/sessions", json={"model": "opus"})).json()["id"]
    assert (await authed.get(f"/api/sessions/{sid}")).json()["model"] == "opus"
    assert (await authed.patch(f"/api/sessions/{sid}", json={"model": "sonnet"})).json()["model"] == "sonnet"
    assert (await authed.patch(f"/api/sessions/{sid}", json={"model": None})).json()["model"] is None
    assert (await authed.patch(f"/api/sessions/{sid}", json={"model": "gpt"})).status_code == 422
