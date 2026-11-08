"""P0 ① 확인용: 전용 텔레그램 계정으로 Vooy 봇과 1:1 대화가 되는지 본다.

사람이 터미널에서 직접 실행한다(로그인 코드 입력이 필요하다).

    uv run --with telethon python scripts/p0/vooy_probe.py

필요한 환경변수 (.env 에 두지 말고 셸에서만 설정):
    TG_API_ID, TG_API_HASH   my.telegram.org 에서 전용 계정으로 발급
    VOOY_BOT                 Vooy 봇 username (예: @xxxx_bot)

세션 파일은 레포 밖(~/.multibot/p0_vooy.session)에 저장한다. 커밋 금지.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

from telethon import TelegramClient, events

QUIET_S = 2.5  # 어댑터 설계의 기본 quiet_s
TIMEOUT_S = 120
PROMPTS = [
    "안녕하세요, 뭘 도와줄 수 있어요?",
    "내일 점심 강남 한식 2명 예약 가능한 곳 찾아줘. 예약은 아직 하지 마.",
]


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"환경변수 {name} 가 필요합니다")
    return v


async def ask(client: TelegramClient, bot, text: str) -> None:
    got: list[tuple[float, str, object]] = []
    last = [0.0]

    async def on_msg(ev):
        last[0] = time.monotonic()
        got.append((last[0], "new" if isinstance(ev, events.NewMessage.Event) else "edit", ev.message))

    client.add_event_handler(on_msg, events.NewMessage(from_users=bot))
    client.add_event_handler(on_msg, events.MessageEdited(from_users=bot))
    t0 = time.monotonic()
    await client.send_message(bot, text)
    print(f"\n>>> {text}")
    try:
        while time.monotonic() - t0 < TIMEOUT_S:
            await asyncio.sleep(0.5)
            if got and time.monotonic() - last[0] >= QUIET_S:
                break
    finally:
        client.remove_event_handler(on_msg)

    if not got:
        print(f"<<< {TIMEOUT_S}초 안에 응답 없음")
        return
    first = got[0][0] - t0
    print(f"<<< 첫 응답 {first:.1f}초, 확정 {last[0] - t0:.1f}초, 메시지 {len(got)}건")
    for ts, kind, m in got:
        print(f"  [{kind} +{ts - t0:.1f}s] {m.message!r}")
        if m.reply_markup and getattr(m.reply_markup, "rows", None):
            labels = [b.text for row in m.reply_markup.rows for b in row.buttons]
            print(f"    버튼: {labels}")
        if m.media:
            print(f"    미디어: {type(m.media).__name__}")


async def main() -> None:
    session_dir = Path.home() / ".multibot"
    session_dir.mkdir(exist_ok=True)
    client = TelegramClient(str(session_dir / "p0_vooy"), int(env("TG_API_ID")), env("TG_API_HASH"))
    await client.start()  # 처음엔 전화번호·코드·2단계 비밀번호를 터미널에서 묻는다
    me = await client.get_me()
    print(f"로그인: id={me.id}")
    bot = await client.get_entity(env("VOOY_BOT"))
    print(f"봇: {getattr(bot, 'username', bot.id)}")
    for p in PROMPTS:
        await ask(client, bot, p)
        await asyncio.sleep(3)  # min_interval_s
    await client.disconnect()
    print("\n결과를 docs/01-requirements.md 7장 P0 표 ① 에 적는다. 새 대화 명령(/start, /new 등) 유무도 같이 본다.")


if __name__ == "__main__":
    asyncio.run(main())
