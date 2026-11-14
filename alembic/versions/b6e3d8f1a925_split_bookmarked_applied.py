"""관심과 신청완료를 따로 저장 (T70, 계획서 6장·7.2)

`state` 한 칸(BOOKMARKED / APPLIED)을 `bookmarked`·`applied` 두 칸으로 나눕니다.
신청완료를 누르면 관심이 덮어써지던 문제를 없앱니다. 예전에 덮어써진 관심은 기록이 없어
복구하지 않습니다.

옛 서버는 `state` 칸을 읽으므로 이 마이그레이션 뒤에는 새 서버가 떠야 합니다.

Revision ID: b6e3d8f1a925
Revises: a7c41e9d2b58
Create Date: 2026-11-14 21:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6e3d8f1a925"
down_revision: str | Sequence[str] | None = "a7c41e9d2b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_scholarship_states"
CONSTRAINT = "ck_user_scholarship_states_state"


def upgrade() -> None:
    for name in ("bookmarked", "applied"):
        op.add_column(TABLE, sa.Column(name, sa.Boolean(), server_default="false", nullable=False))
    op.execute(
        f"UPDATE {TABLE} SET bookmarked = (state = 'BOOKMARKED'), applied = (state = 'APPLIED')"
    )
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.drop_column(TABLE, "state")


def downgrade() -> None:
    op.add_column(TABLE, sa.Column("state", sa.String(length=12), nullable=True))
    # 한 값만 담을 수 있으므로 둘 다 켠 행은 APPLIED로 접고, 둘 다 끈 행은 지웁니다.
    op.execute(f"DELETE FROM {TABLE} WHERE NOT bookmarked AND NOT applied")
    op.execute(f"UPDATE {TABLE} SET state = CASE WHEN applied THEN 'APPLIED' ELSE 'BOOKMARKED' END")
    op.alter_column(TABLE, "state", nullable=False)
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, "state IN ('BOOKMARKED', 'APPLIED')")
    op.drop_column(TABLE, "applied")
    op.drop_column(TABLE, "bookmarked")
