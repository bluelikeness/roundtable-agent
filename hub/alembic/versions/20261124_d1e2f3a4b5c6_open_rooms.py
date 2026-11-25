"""열린 방: room, participant, room_message (docs/proposals/open-rooms.md R1)

Revision ID: d1e2f3a4b5c6
Revises: c4d5e6f7a8b9
Create Date: 2026-11-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = postgresql.ENUM("human", "ai", name="participant_kind", create_type=False)
ROLE = postgresql.ENUM("member", "leader", name="participant_role", create_type=False)


def upgrade() -> None:
    KIND.create(op.get_bind(), checkfirst=True)
    ROLE.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "room",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "participant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "room_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("room.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("role", ROLE, nullable=False),
        sa.Column("check_every_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("room_id", "name", name="uq_participant_room_name"),
        sa.UniqueConstraint("key_hash", name="uq_participant_key_hash"),
    )
    op.create_index("ix_participant_room_id", "participant", ["room_id"])
    op.create_table(
        "room_message",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "room_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("room.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("who", sa.String(length=40), nullable=False),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_room_message_room_id", "room_message", ["room_id"])


def downgrade() -> None:
    op.drop_index("ix_room_message_room_id", table_name="room_message")
    op.drop_table("room_message")
    op.drop_index("ix_participant_room_id", table_name="participant")
    op.drop_table("participant")
    op.drop_table("room")
    ROLE.drop(op.get_bind(), checkfirst=True)
    KIND.drop(op.get_bind(), checkfirst=True)
