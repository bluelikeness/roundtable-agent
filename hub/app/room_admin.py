"""열린 방 관리 CLI. 운영자 로그인 없이 허브 컨테이너 안에서 방·참가자를 만든다(자동화·시험용).

    python -m app.room_admin list
    python -m app.room_admin create-room --name 비서봇들 [--call-only]
    python -m app.room_admin add --room 비서봇들 --name 클로 [--kind ai|human] [--leader] [--check-every 60]
    python -m app.room_admin rotate --room 비서봇들 --name 클로
    python -m app.room_admin call-only --room 비서봇들 --on|--off

참가자 키는 발급할 때 표준출력에 한 줄(`key=rk_...`)로만 나온다. 다시 보여 주지 않는다.
--room 은 방 이름(정확히 일치) 또는 id.
"""

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.models import Participant, ParticipantKind, ParticipantRole, Room
from app.rooms import hash_key, new_key


async def _room(db, ref: str) -> Room:
    try:
        room = await db.get(Room, uuid.UUID(ref))
    except ValueError:
        rows = (
            (
                await db.execute(
                    select(Room).where(Room.name == ref, Room.closed_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if len(rows) > 1:
            sys.exit(f"같은 이름의 열린 방이 {len(rows)}개입니다. id 로 지정하세요")
        room = rows[0] if rows else None
    if room is None:
        sys.exit(f"방이 없습니다: {ref}")
    return room


async def _run(a: argparse.Namespace) -> None:
    async with get_sessionmaker()() as db:
        if a.cmd == "list":
            for r in (
                await db.execute(select(Room).order_by(Room.created_at))
            ).scalars():
                people = (
                    (
                        await db.execute(
                            select(Participant).where(Participant.room_id == r.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                state = (
                    "닫힘" if r.closed_at else ("부를 때만" if r.call_only else "자유")
                )
                names = ", ".join(
                    f"{p.name}{'(팀장)' if p.role == ParticipantRole.leader else ''}"
                    for p in people
                )
                print(f"{r.id}  {r.name}  [{state}]  {names}")
            return

        if a.cmd == "create-room":
            r = Room(name=a.name.strip(), call_only=a.call_only)
            db.add(r)
            await db.commit()
            print(f"room_id={r.id}")
            return

        room = await _room(db, a.room)
        if a.cmd == "call-only":
            room.call_only = a.on
            await db.commit()
            print(f"call_only={room.call_only}")
            return

        if a.cmd == "add":
            key = new_key()
            p = Participant(
                room_id=room.id,
                name=a.name.strip(),
                kind=ParticipantKind(a.kind),
                role=ParticipantRole.leader if a.leader else ParticipantRole.member,
                check_every_s=a.check_every,
                key_hash=hash_key(key),
            )
            db.add(p)
            try:
                await db.commit()
            except Exception:  # noqa: BLE001
                sys.exit(f"이미 같은 이름이 있습니다: {a.name}")
            print(f"key={key}")
            return

        if a.cmd == "rotate":
            p = (
                await db.execute(
                    select(Participant).where(
                        Participant.room_id == room.id, Participant.name == a.name
                    )
                )
            ).scalar_one_or_none()
            if p is None:
                sys.exit(f"참가자가 없습니다: {a.name}")
            key = new_key()
            p.key_hash = hash_key(key)
            await db.commit()
            print(f"key={key}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="room-admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create-room")
    c.add_argument("--name", required=True)
    c.add_argument("--call-only", action="store_true")
    for name in ("add", "rotate", "call-only"):
        s = sub.add_parser(name)
        s.add_argument("--room", required=True)
        if name in ("add", "rotate"):
            s.add_argument("--name", required=True)
        if name == "add":
            s.add_argument("--kind", choices=["ai", "human"], default="ai")
            s.add_argument("--leader", action="store_true")
            s.add_argument("--check-every", type=int, default=60)
        if name == "call-only":
            g = s.add_mutually_exclusive_group(required=True)
            g.add_argument("--on", dest="on", action="store_true")
            g.add_argument("--off", dest="on", action="store_false")
    args = ap.parse_args()

    async def go() -> None:
        try:
            await _run(args)
        finally:
            await dispose_engine()

    asyncio.run(go())


if __name__ == "__main__":
    main()
