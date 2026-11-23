"""열린 방 R3: '부를 때만' 모드와 팀장 실행기.

완료 기준(docs/proposals/open-rooms.md 5장): AI 3개 토론에서 지목 순서대로만 답함 5회 중 4회.
허브가 지목받지 않은 AI 의 글을 409 로 막으므로, 쉬지 않고 말하려는 AI 셋으로 돌려도 순서가 지켜져야 한다.
"""

import httpx
import pytest

from app import rooms as rooms_mod
from app.main import app
from app.room_leader import Leader, next_by_turns, pick_target
from app.rooms import calls


@pytest.fixture(autouse=True)
def _reset_rate():
    rooms_mod._reads.clear()
    rooms_mod._sends.clear()
    rooms_mod._joins.clear()
    yield
    rooms_mod._reads.clear()
    rooms_mod._sends.clear()
    rooms_mod._joins.clear()


def _k(key: str) -> dict[str, str]:
    return {"x-key": key}


async def _setup(authed, call_only: bool = True, names=("클로", "제미", "뮤즈")):
    r = await authed.post("/api/rooms", json={"name": "토론방"})
    rid = r.json()["id"]
    if call_only:
        r = await authed.patch(f"/api/rooms/{rid}", json={"call_only": True})
        assert r.json()["call_only"] is True
    keys = {}
    for n in names:
        r = await authed.post(
            f"/api/rooms/{rid}/participants", json={"name": n, "kind": "ai"}
        )
        keys[n] = r.json()["key"]
    r = await authed.post(
        f"/api/rooms/{rid}/participants",
        json={"name": "팀장", "kind": "ai", "role": "leader"},
    )
    keys["팀장"] = r.json()["key"]
    return rid, keys


def test_mentions():
    assert calls("@클로 어때?", "클로")
    assert calls("자, @클로, 말해 봐", "클로")
    assert not calls("@클로드 어때?", "클로")
    assert calls("@모두 의견 주세요", "제미")
    assert not calls("클로 어때?", "클로")


def test_pick_target_and_turns():
    members = ["클로", "제미", "뮤즈"]
    assert pick_target("@제미 반론 부탁해\n다음은…", members) == "제미"
    assert pick_target("@제미 @뮤즈 둘 다", members) is None
    assert pick_target("아무도 안 부름", members) is None
    assert pick_target("@클로드 는 없음", members) is None
    assert next_by_turns(members, []) == "클로"
    assert next_by_turns(members, ["클로"]) == "제미"
    assert next_by_turns(members, ["클로", "제미", "뮤즈", "클로"]) == "제미"


async def test_call_only_blocks_uncalled_ai(authed):
    rid, keys = await _setup(authed)
    # 부르지 않았으면 409
    r = await authed.post(
        "/api/p/send", json={"text": "제가 먼저!"}, headers=_k(keys["클로"])
    )
    assert r.status_code == 409 and r.json()["detail"] == "not_called"
    # 팀장은 언제나 말할 수 있다
    assert (
        await authed.post(
            "/api/p/send", json={"text": "@클로 시작해"}, headers=_k(keys["팀장"])
        )
    ).status_code == 200
    r = await authed.get("/api/p/msgs", headers=_k(keys["클로"]))
    body = r.json()
    assert body["call_only"] is True and body["my_turn"] is True
    assert body["msgs"][-1]["to_me"] is True
    assert (
        await authed.post("/api/p/send", json={"text": "네"}, headers=_k(keys["클로"]))
    ).status_code == 200
    # 한 번 답하면 다시 부를 때까지 못 한다
    r = await authed.get("/api/p/msgs", headers=_k(keys["클로"]))
    assert r.json()["my_turn"] is False
    assert (
        await authed.post("/api/p/send", json={"text": "또!"}, headers=_k(keys["클로"]))
    ).status_code == 409
    # 제미는 불린 적 없다
    assert (
        await authed.post(
            "/api/p/send", json={"text": "저도"}, headers=_k(keys["제미"])
        )
    ).status_code == 409
    # @모두 면 다 답할 수 있다
    await authed.post(f"/api/rooms/{rid}/messages", json={"text": "@모두 한마디씩"})
    assert (
        await authed.post(
            "/api/p/send", json={"text": "제미 의견"}, headers=_k(keys["제미"])
        )
    ).status_code == 200
    # 끄면 자유롭게
    await authed.patch(f"/api/rooms/{rid}", json={"call_only": False})
    assert (
        await authed.post(
            "/api/p/send", json={"text": "자유"}, headers=_k(keys["뮤즈"])
        )
    ).status_code == 200


class ChattyAi:
    """쉬지 않고 말하려는 AI: 매 주기마다 보내 보고, 허브가 409 로 막으면 그만."""

    def __init__(self, client: httpx.AsyncClient, name: str, key: str):
        self.client, self.name, self.h = client, name, _k(key)

    async def step(self) -> bool:
        r = await self.client.post(
            "/api/p/send", json={"text": f"{self.name}의 의견입니다"}, headers=self.h
        )
        assert r.status_code in (200, 409), r.text
        return r.status_code == 200


def scripted_llm(plan: list[str]):
    """팀장 LLM 대역: 지목 순서를 미리 정해 두고, 결론 요청이면 결론."""
    it = iter(plan)

    async def llm(prompt: str) -> str:
        if "결론을 내라" in prompt:
            return "결론: 셋 다 토요일 오전이 좋다고 했다."
        return f"@{next(it)} 이 주제에 대해 한 줄로 말해 줘."

    return llm


@pytest.mark.parametrize(
    "plan",
    [
        ["클로", "제미", "뮤즈"],
        ["뮤즈", "클로", "제미"],
        ["제미", "제미", "클로"],
        ["클로", "뮤즈", "클로"],
        ["뮤즈", "제미", "클로"],
    ],
)
async def test_debate_follows_leader_order(authed, plan):
    """5회 모두: AI 들은 지목한 순서대로만 글이 올라가고, 끝에 팀장이 결론을 낸다."""
    rid, keys = await _setup(authed)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        leader = Leader(c, keys["팀장"], scripted_llm(plan), max_turns=len(plan))
        ais = [ChattyAi(c, n, keys[n]) for n in ("클로", "제미", "뮤즈")]
        await leader.step()  # 처음 읽기는 맞추기만
        await authed.post(
            f"/api/rooms/{rid}/messages", json={"text": "주말 모임 언제가 좋을까?"}
        )
        for _ in range(len(plan) + 3):
            await leader.step()
            for ai in ais:
                await ai.step()

        r = await authed.get(f"/api/rooms/{rid}/messages")
        msgs = r.json()["msgs"]
        spoken = [m["who"] for m in msgs if m["who"] in ("클로", "제미", "뮤즈")]
        assert spoken == plan
        assert msgs[-1]["who"] == "팀장" and msgs[-1]["text"].startswith("결론:")
        assert "@" not in msgs[-1]["text"]
        assert leader.s.order == plan

        # 결론 뒤에는 사람이 새로 쓸 때까지 조용하다
        before = len(msgs)
        for _ in range(3):
            await leader.step()
            for ai in ais:
                await ai.step()
        assert (
            len((await authed.get(f"/api/rooms/{rid}/messages")).json()["msgs"])
            == before
        )


async def test_leader_falls_back_when_llm_is_off_format(authed):
    rid, keys = await _setup(authed)

    async def bad_llm(prompt: str) -> str:
        return (
            "음… 누가 좋을까요" if "결론을 내라" not in prompt else "정리하면 다 좋다"
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        leader = Leader(c, keys["팀장"], bad_llm, max_turns=3)
        ais = [ChattyAi(c, n, keys[n]) for n in ("클로", "제미", "뮤즈")]
        await leader.step()
        await authed.post(f"/api/rooms/{rid}/messages", json={"text": "시작"})
        for _ in range(6):
            await leader.step()
            for ai in ais:
                await ai.step()
    msgs = (await authed.get(f"/api/rooms/{rid}/messages")).json()["msgs"]
    spoken = [m["who"] for m in msgs if m["who"] in ("클로", "제미", "뮤즈")]
    # 형식이 틀리면 덜 말한 AI 부터 돌아가며 부른다
    assert spoken == ["클로", "제미", "뮤즈"]
    assert msgs[-1]["text"].startswith("결론:")


async def test_leader_moves_on_when_ai_is_silent(authed):
    rid, keys = await _setup(authed, names=("클로", "제미"))
    now = [0.0]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as c:
        leader = Leader(
            c,
            keys["팀장"],
            scripted_llm(["클로", "제미"]),
            max_turns=5,
            reply_wait_s=60,
            clock=lambda: now[0],
        )
        await leader.step()
        await authed.post(f"/api/rooms/{rid}/messages", json={"text": "시작"})
        assert (await leader.step()).startswith("@클로")
        assert await leader.step() is None  # 클로 답 기다리는 중
        now[0] = 61  # 클로가 끝내 답이 없음
        assert (await leader.step()).startswith("@제미")
