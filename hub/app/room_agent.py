"""열린 방 Agent 실행기: 이 PC 의 AI CLI(claude·codex 등)를 방의 AI 참가자 한 명으로 붙인다 (R4).

자기 키로 /api/p/* 만 쓴다. 누가 나를 @이름(또는 @모두) 으로 부르면(my_turn) 최근 대화를 넘겨 답을 받아 올린다.
부르지 않으면 말하지 않는다('부를 때만' 이 꺼진 방에서도 같은 규칙).

실행 예 (hub 폴더):
  # Claude Code 구독
  ROOM_URL=https://<허브> ROOM_KEY=rk_... uv run python -m app.room_agent
  # Codex: {out} 자리에 마지막 답을 쓰게 한다
  AGENT_LLM_CMD="codex exec --skip-git-repo-check -s read-only -o {out} -" ROOM_URL=... ROOM_KEY=... uv run python -m app.room_agent
선택: AGENT_POLL_S(기본 20, 최소 15) AGENT_PERSONA(성격·말투 한두 줄) AGENT_MAX_CHARS(기본 1200)
"""

import asyncio
import logging
import os
import shlex
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

log = logging.getLogger("room_agent")

Llm = Callable[[str], Awaitable[str]]


def build_prompt(room: str, me: str, transcript: list[dict], persona: str = "") -> str:
    convo = "\n".join(f"{m['who']}: {m['text'][:800]}" for m in transcript[-30:])
    return (
        f"너는 대화방 「{room}」에 참여한 AI 「{me}」다. 사람과 여러 AI 가 같이 이야기한다.\n"
        + (f"너의 성격·말투: {persona}\n" if persona else "")
        + f"최근 대화(아래가 최신):\n{convo}\n\n"
        "방금 누군가 너를 불렀다. 그 부름에 대한 너의 답만 써라. "
        "5문장 이내, 한국어로, 앞에 이름을 붙이지 말고, 다른 참가자를 대신해 말하지 마라. "
        "비밀번호·인증코드·개인정보는 쓰지 않는다. 파일을 고치거나 명령을 실행하지 마라."
    )


def clean_answer(text: str, me: str, max_chars: int) -> str:
    t = text.strip()
    for prefix in (f"{me}:", f"{me} :", f"{me}："):
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
    return t[:max_chars]


class Agent:
    def __init__(
        self,
        client: httpx.AsyncClient,
        key: str,
        llm: Llm,
        persona: str = "",
        max_chars: int = 1200,
    ):
        self.client = client
        self.h = {"x-key": key}
        self.llm = llm
        self.persona = persona
        self.max_chars = max_chars
        self.last_id = 0
        self.transcript: list[dict] = []

    async def step(self) -> str | None:
        """한 번 읽고, 내 차례면 한 번 답한다. 보낸 글(없으면 None)."""
        r = await self.client.get(
            "/api/p/msgs", params={"since": self.last_id}, headers=self.h
        )
        if r.status_code == 429:
            return None
        r.raise_for_status()
        out = r.json()
        new = [m for m in out["msgs"] if m["id"] > self.last_id]
        if new:
            self.last_id = new[-1]["id"]
        self.transcript.extend(new)
        if not out["my_turn"]:
            return None
        raw = await self.llm(
            build_prompt(out["room"], out["me"], self.transcript, self.persona)
        )
        text = clean_answer(raw, out["me"], self.max_chars)
        if not text:
            return None
        r = await self.client.post("/api/p/send", json={"text": text}, headers=self.h)
        if r.status_code in (409, 429):
            # 그사이 차례가 지나갔거나 너무 잦음: 다음 주기에 다시 본다
            return None
        r.raise_for_status()
        self.last_id = max(self.last_id, r.json()["id"])
        self.transcript.append({"who": out["me"], "kind": "ai", "text": text})
        return text


def cli_llm(cmd: str = "claude -p", timeout_s: float = 240) -> Llm:
    """프롬프트를 표준입력으로 넘기는 CLI. 명령에 {out} 이 있으면 그 파일에서 답을 읽는다(codex -o)."""

    async def run(prompt: str) -> str:
        out_file = None
        argv = shlex.split(cmd)
        if any("{out}" in a for a in argv):
            fd, out_file = tempfile.mkstemp(prefix="room_agent_", suffix=".txt")
            os.close(fd)
            argv = [a.replace("{out}", out_file) for a in argv]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=timeout_s
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"LLM 실패({proc.returncode}): {stderr.decode(errors='replace')[:200]}"
                )
            if out_file:
                return Path(out_file).read_text(encoding="utf-8", errors="replace")
            return stdout.decode(errors="replace")
        finally:
            if out_file:
                Path(out_file).unlink(missing_ok=True)

    return run


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    url, key = os.environ["ROOM_URL"].rstrip("/"), os.environ["ROOM_KEY"]
    poll = max(15.0, float(os.environ.get("AGENT_POLL_S", "20")))
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        agent = Agent(
            client,
            key,
            cli_llm(os.environ.get("AGENT_LLM_CMD", "claude -p")),
            persona=os.environ.get("AGENT_PERSONA", ""),
            max_chars=int(os.environ.get("AGENT_MAX_CHARS", "1200")),
        )
        log.info("Agent 시작: %s (%.0f초마다 확인)", url, poll)
        while True:
            try:
                sent = await agent.step()
                if sent:
                    log.info("답함: %s", sent.replace("\n", " ")[:80])
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
