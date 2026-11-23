"""열린 방 R4: Agent 실행기, 링크 전용(?k=) 읽기·쓰기, 방 관리 CLI."""

import sys
from urllib.parse import quote

import httpx
import pytest

from app import rooms as rooms_mod
from app.main import app
from app.room_agent import Agent, clean_answer, cli_llm
from app.room_leader import Leader


@pytest.fixture(autouse=True)
def _reset_rate():
    for b in (rooms_mod._reads, rooms_mod._sends, rooms_mod._joins):
        b.clear()
    yield
    for b in (rooms_mod._reads, rooms_mod._sends, rooms_mod._joins):
        b.clear()


async def _room(authed, call_only=True, ais=("클로", "익스"), leader=True):
    rid = (await authed.post("/api/rooms", json={"name": "비서봇들"})).json()["id"]
    if call_only:
        await authed.patch(f"/api/rooms/{rid}", json={"call_only": True})
    keys = {}
    for n in ais:
        keys[n] = (
            await authed.post(
                f"/api/rooms/{rid}/participants", json={"name": n, "kind": "ai"}
            )
        ).json()["key"]
    if leader:
        keys["팀장"] = (
            await authed.post(
                f"/api/rooms/{rid}/participants",
                json={"name": "팀장", "kind": "ai", "role": "leader"},
            )
        ).json()["key"]
    return rid, keys


def test_clean_answer():
    assert clean_answer("클로: 안녕", "클로", 100) == "안녕"
    assert clean_answer("  긴 답  ", "클로", 2) == "긴 "


async def test_agent_answers_only_when_called(authed):
    rid, keys = await _room(authed, leader=False)
    seen: list[str] = []

    async def llm(prompt: str) -> str:
        seen.append(prompt)
        return "클로: 저는 고기가 좋아요."

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        agent = Agent(c, keys["클로"], llm, persona="차분하게")
        assert await agent.step() is None  # 아무도 안 부름
        await authed.post(
            f"/api/rooms/{rid}/messages", json={"text": "메뉴 정하자 @익스"}
        )
        assert await agent.step() is None  # 남을 부름
        await authed.post(f"/api/rooms/{rid}/messages", json={"text": "@클로 너는?"})
        assert await agent.step() == "저는 고기가 좋아요."
        assert await agent.step() is None  # 한 번만 답한다
    assert len(seen) == 1
    assert "「클로」" in seen[0] and "차분하게" in seen[0] and "@클로 너는?" in seen[0]
    msgs = (await authed.get(f"/api/rooms/{rid}/messages")).json()["msgs"]
    assert [(m["who"], m["text"]) for m in msgs][-1] == ("클로", "저는 고기가 좋아요.")


async def test_leader_with_two_agents(authed):
    """팀장 + Agent 실행기 둘: 사람 주제 → 지목 → 답 → 결론."""
    rid, keys = await _room(authed)
    plan = iter(["익스", "클로"])

    async def leader_llm(prompt: str) -> str:
        return (
            "결론: 둘 다 좋다."
            if "결론을 내라" in prompt
            else f"@{next(plan)} 한 줄 의견?"
        )

    def agent_llm(name: str):
        async def run(prompt: str) -> str:
            return f"{name} 의견입니다."

        return run

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        leader = Leader(c, keys["팀장"], leader_llm, max_turns=2)
        agents = [Agent(c, keys[n], agent_llm(n)) for n in ("클로", "익스")]
        await leader.step()
        await authed.post(f"/api/rooms/{rid}/messages", json={"text": "주제: 점심"})
        for _ in range(6):
            await leader.step()
            for a in agents:
                await a.step()
    msgs = (await authed.get(f"/api/rooms/{rid}/messages")).json()["msgs"]
    assert [m["who"] for m in msgs] == [
        "operator",
        "팀장",
        "익스",
        "팀장",
        "클로",
        "팀장",
    ]
    assert msgs[-1]["text"].startswith("결론:")


async def test_cli_llm_reads_stdout_and_out_file(tmp_path):
    echo = cli_llm(
        f'"{sys.executable}" -c "import sys; print(sys.stdin.read().upper())"'
    )
    assert (await echo("abc")).strip() == "ABC"
    to_file = cli_llm(
        f"\"{sys.executable}\" -c \"import sys; open(sys.argv[1], 'w', encoding='utf-8').write('파일 답')\" {{out}}"
    )
    assert await to_file("x") == "파일 답"
    failing = cli_llm(f'"{sys.executable}" -c "import sys; sys.exit(3)"')
    with pytest.raises(RuntimeError):
        await failing("x")


async def test_query_key_works_for_json_api(authed):
    _rid, keys = await _room(authed, call_only=False, leader=False)
    k = keys["클로"]
    assert (await authed.get(f"/api/p/msgs?k={k}")).status_code == 200
    assert (
        await authed.post(f"/api/p/send?k={k}", json={"text": "쿼리 키"})
    ).status_code == 200
    assert (await authed.get(f"/api/p/participants?k={k}")).status_code == 200


async def test_link_mode_read_and_say(authed, client):
    rid, keys = await _room(authed, leader=False)
    k = keys["익스"]
    await authed.post(
        f"/api/rooms/{rid}/messages", json={"text": "@익스 점심 뭐 먹지?"}
    )

    r = await client.get(f"/api/p/read?k={k}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "no-store"
    body = r.text
    assert "방: 비서봇들" in body and "나: 익스" in body
    assert "지금 내 차례: 예" in body
    assert "[나를 부름]: @익스 점심 뭐 먹지?" in body
    assert f"/api/p/say?k={k}&text=" in body

    say = f"/api/p/say?k={k}&text={quote('김치찌개요')}"
    r = await client.get(say)
    assert r.status_code == 200 and "방에 올렸어요" in r.text
    # 같은 링크가 미리보기·새로고침으로 또 열려도 한 번만
    r = await client.get(say)
    assert "이미 올렸어요" in r.text
    msgs = (await authed.get(f"/api/rooms/{rid}/messages")).json()["msgs"]
    assert [m["text"] for m in msgs].count("김치찌개요") == 1

    # 부름이 없으면 막히고 이유를 글자로 알려 준다
    r = await client.get(f"/api/p/say?k={k}&text={quote('또 할게요')}")
    assert r.status_code == 409 and "아직 당신을 부르지 않았어요" in r.text
    r = await client.get(f"/api/p/read?k={k}")
    assert "지금 내 차례: 아니요" in r.text


async def test_link_mode_errors_are_plain_text(client):
    r = await client.get("/api/p/read?k=rk_nope")
    assert r.status_code == 401 and "키가 맞지 않아요" in r.text
    r = await client.get("/api/p/say?k=garbage&text=x")
    assert r.status_code == 401


async def test_link_mode_uses_public_url(authed, client):
    from app.config import Settings, get_settings

    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{**base.model_dump(), "public_url": "https://hub.example/"}
    )
    try:
        _rid, keys = await _room(authed, call_only=False, leader=False)
        r = await client.get(f"/api/p/read?k={keys['클로']}")
        assert f"https://hub.example/api/p/say?k={keys['클로']}&text=" in r.text
        assert f"https://hub.example/api/p/read?k={keys['클로']}&since=" in r.text
    finally:
        app.dependency_overrides.pop(get_settings, None)
