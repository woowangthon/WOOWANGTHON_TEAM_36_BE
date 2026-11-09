"""장학금 숨기기 상태(HIDDEN) 제거: 숨긴 장학금을 되돌릴 화면이 없고 탭 필터로 충분함

저장된 HIDDEN 행은 지웁니다(상태 없음 = 기본 목록에 다시 보임).

Revision ID: 5e9a1b7c3d42
Revises: 8c2d4e6f1a37
Create Date: 2026-11-09 12:00:00.000000+09:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e9a1b7c3d42"
down_revision: str | Sequence[str] | None = "8c2d4e6f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_scholarship_states"
CONSTRAINT = "ck_user_scholarship_states_state"


def upgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE state = 'HIDDEN'")
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, "state IN ('BOOKMARKED', 'APPLIED')")


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), TABLE, "state IN ('BOOKMARKED', 'APPLIED', 'HIDDEN')"
    )
