"""프로필 편입생·외국인 여부 추가 (T39, 계획서 4.1)

옛 서버는 새 칸을 읽지 않으므로 그대로 동작합니다. 기존 사용자는 둘 다 false로 채웁니다
(편입생이 아니고 내국인으로 보던 기존 판정과 같음).

Revision ID: f3b8d2a6c104
Revises: 132c673f3141
Create Date: 2026-11-12 18:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8d2a6c104"
down_revision: str | Sequence[str] | None = "132c673f3141"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("is_transfer", "is_foreign")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            "user_profiles",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column("user_profiles", column)
