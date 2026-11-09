"""추출 입력이 10만 자를 넘어 잘렸는지 기록: 상세 화면에 "공고문 일부만 읽음" 표시(D5)

Revision ID: a4c8e1f5b2d9
Revises: 7d3f9b2e6c51
Create Date: 2026-11-09 22:30:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c8e1f5b2d9"
down_revision: str | Sequence[str] | None = "7d3f9b2e6c51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notices",
        sa.Column("input_truncated", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("notices", "input_truncated")
