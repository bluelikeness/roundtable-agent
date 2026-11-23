"""열린 방 팀장 실행기 (docs/proposals/open-rooms.md 3.4, R3).

팀장도 다른 참가자처럼 자기 키로 /api/p/* 만 쓴다. 허브 러너·어댑터 계약은 건드리지 않는다.
흐름: 사람이 주제를 올리면 → 팀장이 다음 발언자 한 명을 @이름 으로 지목 → 그 AI 가 답하면 → 다음 지목 …
max_turns 만큼 돌면 결론을 정리하고, 사람이 새 글을 쓸 때까지 쉰다.
방을 'AI는 부를 때만' 으로 두면, 지목받지 않은 AI 의 글은 허브가 409 로 막는다.

누구를 부를지는 LLM(기본: 이 PC 의 `claude -p`, 구독 사용)이 정하고, 답이 형식에 안 맞으면
아직 덜 말한 AI 를 차례대로 부른다.

실행:
  ROOM_URL=https://<허브> ROOM_KEY=rk_... uv run python -m app.room_leader
  선택: LEADER_MAX_TURNS=6  LEADER_POLL_S=20  LEADER_REPLY_WAIT_S=300  LEADER_LLM_CMD="claude -p"
"""

import asyncio
import logging
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.rooms import mentioned

log = logging.getLogger("room_leader")

Llm = Callable[[str], Awaitable[str]]
CONCLUDE = "결론:"


@dataclass
class LeaderState:
    last_id: int = 0
    me: str = ""
    transcript: list[dict] = field(default_factory=list)
    waiting_for: str | None = None
    waiting_since: float = 0.0
    turns: int = 0
    # 사람 글이 새로 와야 다시 움직인다(결론 뒤 쉬는 중)
    idle: bool = True
    order: list[str] = field(default_factory=list)
    synced: bool = False


def pick_target(answer: str, members: list[str]) -> str | None:
    """LLM 답 첫 줄에서 지목한 AI 한 명. 둘 이상이거나 없으면 None."""
    first = answer.strip().splitlines()[0] if answer.strip() else ""
    # 허브와 같은 판정(@클로 가 @클로드 에 걸리지 않게)
    hits = [n for n in members if mentioned(first, n)]
    return hits[0] if len(hits) == 1 else None


def next_by_turns(members: list[str], spoken: list[str]) -> str:
    """덜 말한 AI 부터, 같으면 오래 전에 말한 순."""

    def key(n: str) -> tuple[int, int]:
        count = spoken.count(n)
        last = max((i for i, w in enumerate(spoken) if w == n), default=-1)
        return (count, last)

    return min(members, key=key)


def build_prompt(
    topic_room: str, me: str, members: list[str], transcript: list[dict], conclude: bool
) -> str:
    convo = "\n".join(f"{m['who']}: {m['text'][:800]}" for m in transcript[-30:])
    if conclude:
        task = (
            f"토론이 충분히 돌았다. 지금까지 나온 의견을 3~5줄로 정리하고 결론을 내라. "
            f"첫 줄은 반드시 '{CONCLUDE}' 로 시작한다. 아무도 @ 로 부르지 않는다."
        )
    else:
        task = (
            "다음에 말할 AI 한 명을 골라 짧게 질문하거나 맡길 일을 준다. "
            f"첫 줄은 반드시 '@이름 ' 으로 시작하고, 이름은 다음 중 하나만: {', '.join(members)}. "
            "한 번에 한 명만 부른다. 2문장 이내."
        )
    return (
        f"너는 대화방 「{topic_room}」의 팀장 「{me}」다. 사람과 여러 AI 가 같이 토론한다. "
        "AI 들이 한꺼번에 떠들지 않게 순서를 정하고, 끝에는 결론을 낸다.\n\n"
        f"최근 대화(아래가 최신):\n{convo}\n\n할 일: {task}"
    )


class Leader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        key: str,
        llm: Llm,
        max_turns: int = 6,
        reply_wait_s: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.h = {"x-key": key}
        self.llm = llm
        self.max_turns = max_turns
        self.reply_wait_s = reply_wait_s
        self.clock = clock
        self.s = LeaderState()

    async def _members(self) -> list[str]:
        r = await self.client.get("/api/p/participants", headers=self.h)
        r.raise_for_status()
        return [
            p["name"]
            for p in r.json()
            if p["kind"] == "ai" and p["role"] != "leader" and p["enabled"]
        ]

    async def _post(self, text: str) -> None:
        r = await self.client.post("/api/p/send", json={"text": text}, headers=self.h)
        r.raise_for_status()
        self.s.last_id = max(self.s.last_id, r.json()["id"])
        self.s.transcript.append({"who": self.s.me, "kind": "ai", "text": text})

    async def step(self) -> str | None:
        """한 번 읽고 필요하면 한 번 말한다. 보낸 글(없으면 None)."""
        r = await self.client.get(
            "/api/p/msgs", params={"since": self.s.last_id}, headers=self.h
        )
        if r.status_code == 429:
            return None
        r.raise_for_status()
        out = r.json()
        self.s.me = out["me"]
        room = out["room"]
        new = [m for m in out["msgs"] if m["id"] > self.s.last_id]
        if new:
            self.s.last_id = new[-1]["id"]
        self.s.transcript.extend(new)
        if not self.s.synced:
            # 처음 읽기는 지난 대화를 맞추기만 한다(예전 글로 토론을 다시 시작하지 않게)
            self.s.synced = True
            return None
        others = [m for m in new if m["who"] != self.s.me]

        # 사람이 새로 쓰면 새 판을 시작한다
        if any(m["kind"] == "human" for m in others):
            self.s.idle = False
            self.s.turns = 0
            self.s.waiting_for = None
        if self.s.idle:
            return None

        if self.s.waiting_for:
            answered = any(m["who"] == self.s.waiting_for for m in others)
            timed_out = self.clock() - self.s.waiting_since > self.reply_wait_s
            if not answered and not timed_out:
                return None
            if answered:
                self.s.order.append(self.s.waiting_for)
            self.s.waiting_for = None

        members = await self._members()
        if not members:
            return None

        if self.s.turns >= self.max_turns:
            text = (
                await self.llm(
                    build_prompt(
                        room, self.s.me, members, self.s.transcript, conclude=True
                    )
                )
            ).strip()
            if not text.startswith(CONCLUDE):
                text = f"{CONCLUDE} {text}"
            # 결론에서 아무도 부르지 않게 @ 를 지운다
            await self._post(text.replace("@", ""))
            self.s.idle = True
            return text

        answer = (
            await self.llm(
                build_prompt(
                    room, self.s.me, members, self.s.transcript, conclude=False
                )
            )
        ).strip()
        target = pick_target(answer, members)
        if target is None:
            target = next_by_turns(members, self.s.order)
            answer = f"@{target} 지금까지 얘기에 대해 의견을 말해 줘."
        await self._post(answer)
        self.s.waiting_for = target
        self.s.waiting_since = self.clock()
        self.s.turns += 1
        return answer


def claude_cli(cmd: str = "claude -p") -> Llm:
    """이 PC 의 Claude Code 헤드리스로 답을 받는다(API 키 없이 구독 사용)."""
    argv = shlex.split(cmd)

    async def run(prompt: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=180
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"LLM 실패({proc.returncode}): {err.decode(errors='replace')[:200]}"
            )
        return out.decode(errors="replace")

    return run


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    url, key = os.environ["ROOM_URL"].rstrip("/"), os.environ["ROOM_KEY"]
    poll = max(15.0, float(os.environ.get("LEADER_POLL_S", "20")))
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        leader = Leader(
            client,
            key,
            claude_cli(os.environ.get("LEADER_LLM_CMD", "claude -p")),
            max_turns=int(os.environ.get("LEADER_MAX_TURNS", "6")),
            reply_wait_s=float(os.environ.get("LEADER_REPLY_WAIT_S", "300")),
        )
        log.info("팀장 시작: %s (%.0f초마다 확인)", url, poll)
        while True:
            try:
                sent = await leader.step()
                if sent:
                    log.info("보냄: %s", sent.replace("\n", " ")[:80])
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403, 410):
                    log.error(
                        "멈춤: %s %s", e.response.status_code, e.response.text[:100]
                    )
                    return
                log.warning("잠깐 실패: %s", e)
            except Exception as e:  # noqa: BLE001 — 실행기는 죽지 않고 다음 주기에 다시 한다
                log.warning("잠깐 실패: %s", e)
            await asyncio.sleep(poll)


if __name__ == "__main__":
    asyncio.run(main())
