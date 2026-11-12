"""장학금 쉬운 이름 추가 (T41, 계획서 5.2 display_name)

옛 서버는 새 칸을 읽지 않으므로 그대로 동작합니다. 기존 행은 NULL이고, 화면은 name을 보여 줍니다.
관리자 재추출이나 다음 수정 공지 수집 때 extract_v8 결과로 채워집니다.

Revision ID: a7c41e9d2b58
Revises: f3b8d2a6c104
Create Date: 2026-11-12 21:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c41e9d2b58"
down_revision: str | Sequence[str] | None = "f3b8d2a6c104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scholarships", sa.Column("display_name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("scholarships", "display_name")
