"""공지 추출 재시도 횟수 칸 추가 (계획서 9.3): 연속 3번 실패한 FAILED는 자동 재시도에서 뺌

Revision ID: 8c2d4e6f1a37
Revises: 3b7e1c5a9f20
Create Date: 2026-11-07 23:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c2d4e6f1a37"
down_revision: str | Sequence[str] | None = "3b7e1c5a9f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notices",
        sa.Column("extract_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    # 이미 FAILED인 공지는 매시간 여러 번 실패해 온 것이므로 바로 자동 재시도에서 뺍니다.
    # 다시 시도하려면 관리자 재추출을 누릅니다.
    op.execute("UPDATE notices SET extract_attempts = 3 WHERE extract_status = 'FAILED'")


def downgrade() -> None:
    op.drop_column("notices", "extract_attempts")
