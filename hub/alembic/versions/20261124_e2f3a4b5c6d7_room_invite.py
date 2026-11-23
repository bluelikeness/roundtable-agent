"""열린 방 R2: 사람 초대 링크 키(room.invite_hash)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-24 15:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("room", sa.Column("invite_hash", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_room_invite_hash", "room", ["invite_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_room_invite_hash", "room", type_="unique")
    op.drop_column("room", "invite_hash")
