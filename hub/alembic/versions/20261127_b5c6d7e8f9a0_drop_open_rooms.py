"""열린 방 기능 폐기: room·participant·room_message 테이블과 enum 타입을 지운다

열린 방(R1~R4) 마이그레이션은 뒤에 hub_file 이 이어져 있어 아래로 되돌릴 수 없으므로
지우는 마이그레이션을 위에 쌓는다. 되살릴 일이 있으면 배포 전 DB 백업에서 복원한다.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-11-27 01:30:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_message")
    op.execute("DROP TABLE IF EXISTS participant")
    op.execute("DROP TABLE IF EXISTS room")
    op.execute("DROP TYPE IF EXISTS participant_kind")
    op.execute("DROP TYPE IF EXISTS participant_role")


def downgrade() -> None:
    raise NotImplementedError("열린 방은 폐기했다. 되살리려면 DB 백업에서 복원한다")
