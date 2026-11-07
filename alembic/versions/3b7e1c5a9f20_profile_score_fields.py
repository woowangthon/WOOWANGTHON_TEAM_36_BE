"""프로필 성적 칸 추가 (계획서 4.1 성적 입력 C안): 100점 환산·전체 성적, 이수학점 선택

Revision ID: 3b7e1c5a9f20
Revises: d69fcfa87f87
Create Date: 2026-11-07 13:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b7e1c5a9f20"
down_revision: str | Sequence[str] | None = "d69fcfa87f87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("score_100", sa.Float(), nullable=True))
    op.add_column("user_profiles", sa.Column("gpa_total", sa.Float(), nullable=True))
    op.add_column("user_profiles", sa.Column("score_100_total", sa.Float(), nullable=True))
    op.alter_column("user_profiles", "credits", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 이수학점을 비운 프로필이 있으면 되돌릴 수 없으므로 0으로 채운 뒤 NOT NULL로 돌립니다.
    op.execute("UPDATE user_profiles SET credits = 0 WHERE credits IS NULL")
    op.alter_column("user_profiles", "credits", existing_type=sa.Integer(), nullable=False)
    op.drop_column("user_profiles", "score_100_total")
    op.drop_column("user_profiles", "gpa_total")
    op.drop_column("user_profiles", "score_100")
