"""러너 연동 컬럼: session.last_seq, run.claude_session_id

Revision ID: b3c1e2f4a5d6
Revises: dd79db7fdda1
Create Date: 2026-11-08 19:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c1e2f4a5d6"
down_revision: str | None = "dd79db7fdda1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("session", sa.Column("last_seq", sa.BigInteger(), server_default="0", nullable=False))
    # 이미 있는 메시지 뒤로 이어지게 맞춘다
    op.execute("UPDATE session SET last_seq = COALESCE((SELECT MAX(seq) FROM message WHERE message.session_id = session.id), 0)")
    op.add_column("run", sa.Column("claude_session_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "claude_session_id")
    op.drop_column("session", "last_seq")
