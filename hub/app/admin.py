"""운영자 계정 관리 CLI (hub-admin). 비밀번호·TOTP 재설정은 웹에 두지 않는다(docs/05-ui.md 7장).

    python -m app.admin create-operator --username me
    python -m app.admin reset-password --username me
    python -m app.admin reset-totp --username me

비밀번호는 프롬프트로 받는다. 스크립트에서는 --password-stdin 으로 표준입력 한 줄을 쓴다.
TOTP 시크릿과 otpauth URI 는 이 터미널에만 출력하고 로그에 남기지 않는다.
"""

import argparse
import asyncio
import getpass
import sys

import pyotp
from sqlalchemy import select

from app.auth import hash_password, new_totp_secret
from app.db import dispose_engine, get_sessionmaker
from app.models import Operator

ISSUER = "roundtable-hub"


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("새 비밀번호: ")
        if pw != getpass.getpass("비밀번호 확인: "):
            sys.exit("비밀번호가 일치하지 않습니다")
    if len(pw) < 12:
        sys.exit("비밀번호는 12자 이상이어야 합니다")
    return pw


def _print_totp(username: str, secret: str) -> None:
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)
    print("인증 앱에 아래 시크릿(또는 URI)을 등록하세요. 다시 표시되지 않습니다.")
    print(f"  secret: {secret}")
    print(f"  uri:    {uri}")


async def _run(args: argparse.Namespace) -> None:
    async with get_sessionmaker()() as db:
        op = (await db.execute(select(Operator).where(Operator.username == args.username))).scalar_one_or_none()

        if args.cmd == "create-operator":
            if op is not None:
                sys.exit(f"이미 있는 운영자입니다: {args.username}")
            if (await db.execute(select(Operator.id))).first() is not None:
                sys.exit("운영자는 한 명만 둡니다. 기존 계정을 재설정하세요.")
            secret = new_totp_secret()
            db.add(
                Operator(
                    username=args.username,
                    password_hash=hash_password(_read_password(args.password_stdin)),
                    totp_secret=secret,
                )
            )
            await db.commit()
            print(f"운영자 생성: {args.username}")
            _print_totp(args.username, secret)
            return

        if op is None:
            sys.exit(f"운영자가 없습니다: {args.username}")
        if args.cmd == "reset-password":
            op.password_hash = hash_password(_read_password(args.password_stdin))
        elif args.cmd == "reset-totp":
            op.totp_secret = new_totp_secret()
        # 재설정하면 기존 로그인 쿠키를 모두 무효화하고 잠금을 푼다
        op.auth_version += 1
        op.failed_attempts = 0
        op.locked_until = None
        await db.commit()
        print(f"{args.cmd} 완료: {args.username}")
        if args.cmd == "reset-totp":
            _print_totp(args.username, op.totp_secret)


def main() -> None:
    p = argparse.ArgumentParser(prog="hub-admin")
    p.add_argument("cmd", choices=["create-operator", "reset-password", "reset-totp"])
    p.add_argument("--username", required=True)
    p.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준입력 한 줄로 받는다")
    args = p.parse_args()

    async def go() -> None:
        try:
            await _run(args)
        finally:
            await dispose_engine()

    asyncio.run(go())


if __name__ == "__main__":
    main()
