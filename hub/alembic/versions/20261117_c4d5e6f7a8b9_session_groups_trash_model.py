"""세션 관리: 그룹(session_group), 세션별 모델, 휴지통(session.trashed_at)

Revision ID: c4d5e6f7a8b9
Revises: b3c1e2f4a5d6
Create Date: 2026-11-17 16:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c1e2f4a5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_group",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        "session",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("session_group.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_session_group_id", "session", ["group_id"])
    # 세션별 Claude 모델(sonnet|opus). 비면 기본(sonnet)
    op.add_column("session", sa.Column("model", sa.String(length=16), nullable=True))
    # 휴지통으로 옮긴 시각. 비면 휴지통이 아니다
    op.add_column("session", sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "trashed_at")
    op.drop_column("session", "model")
    op.drop_index("ix_session_group_id", table_name="session")
    op.drop_column("session", "group_id")
    op.drop_table("session_group")
