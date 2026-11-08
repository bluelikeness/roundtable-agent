"""hub-admin CLI: 운영자 생성·재설정."""

import argparse
import io

import pytest
from sqlalchemy import select

from app import admin
from app.auth import verify_password
from app.models import Operator


async def _run(monkeypatch, cmd, password=None, username="me"):
    if password is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    await admin._run(argparse.Namespace(cmd=cmd, username=username, password_stdin=password is not None))


async def test_create_and_reset(monkeypatch, db, capsys):
    await _run(monkeypatch, "create-operator", "first-password-123")
    out = capsys.readouterr().out
    assert "secret:" in out and "otpauth://" in out
    op = (await db.execute(select(Operator))).scalar_one()
    assert verify_password("first-password-123", op.password_hash)
    old_secret, old_version = op.totp_secret, op.auth_version

    await _run(monkeypatch, "reset-password", "second-password-456")
    await db.refresh(op)
    assert verify_password("second-password-456", op.password_hash)
    assert op.auth_version == old_version + 1  # 기존 로그인 무효화

    await _run(monkeypatch, "reset-totp")
    await db.refresh(op)
    assert op.totp_secret != old_secret


async def test_single_operator_and_short_password(monkeypatch):
    await _run(monkeypatch, "create-operator", "first-password-123")
    with pytest.raises(SystemExit):
        await _run(monkeypatch, "create-operator", "another-password-1", username="other")
    with pytest.raises(SystemExit):
        await _run(monkeypatch, "reset-password", "short")
