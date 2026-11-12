"""조건 답 테이블과 xlsx 첨부 마이그레이션 합치기

Revision ID: 132c673f3141
Revises: afd589895138, e5a1c9d3b7f2
Create Date: 2026-11-12 14:56:58.736255+09:00

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "132c673f3141"
down_revision: str | Sequence[str] | None = ("afd589895138", "e5a1c9d3b7f2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
