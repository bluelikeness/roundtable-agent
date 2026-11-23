"""열린 방 R3: 'AI는 부를 때만' 설정(room.call_only)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-11-24 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("room", sa.Column("call_only", sa.Boolean(), nullable=False, server_default="false"))
    # 참가자별 '내 마지막 글' 을 자주 찾는다(부름 판정)
    op.create_index("ix_room_message_participant_id", "room_message", ["participant_id"])


def downgrade() -> None:
    op.drop_index("ix_room_message_participant_id", table_name="room_message")
    op.drop_column("room", "call_only")
