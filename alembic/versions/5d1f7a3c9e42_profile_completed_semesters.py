"""프로필 이수학기 칸 추가와 값 정리 (계획서 4.1·4.3, 배포 ①)

옛 서버도 그대로 읽을 수 있는 변경만 합니다. 배포 중 몇 분 동안은 옛 서버가 아직 돌기 때문입니다.
- `completed_semesters` 추가(nullable) 후 채움: 학년 n → (n−1)×2+1, 초과학기면 8
- `gpa`·`aibc_member` nullable (이수학기 0이면 평점 없음, 양성인재 모름)
- 재학상태 수료·졸업유예 → 재학, 감면 `모름` → null, 성적은 소수 둘째 자리로 반올림
- 실명 삭제는 배포 ③, 옛 칸(grade·extra_semester·department) 삭제는 배포 ④에서 합니다.

Revision ID: 5d1f7a3c9e42
Revises: 8c2d4e6f1a37
Create Date: 2026-11-10 21:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d1f7a3c9e42"
down_revision: str | Sequence[str] | None = "8c2d4e6f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCORE_COLUMNS = ("gpa", "score_100", "gpa_total", "score_100_total")


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("completed_semesters", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE user_profiles SET completed_semesters = "
        "CASE WHEN extra_semester THEN 8 ELSE (grade - 1) * 2 + 1 END"
    )
    op.alter_column("user_profiles", "gpa", existing_type=sa.Float(), nullable=True)
    op.alter_column("user_profiles", "aibc_member", existing_type=sa.Boolean(), nullable=True)
    op.execute(
        "UPDATE user_profiles SET enrollment_status = '재학' "
        "WHERE enrollment_status IN ('수료', '졸업유예')"
    )
    op.execute(
        "UPDATE user_profiles SET full_tuition_waiver = NULL WHERE full_tuition_waiver = '모름'"
    )
    for column in SCORE_COLUMNS:
        op.execute(
            f"UPDATE user_profiles SET {column} = round({column}::numeric, 2)::double precision "
            f"WHERE {column} <> round({column}::numeric, 2)"
        )


def downgrade() -> None:
    # 값 정리(수료·졸업유예 → 재학, 모름 → null, 반올림)는 되돌리지 않습니다.
    # 옛 스키마의 NOT NULL을 지키려고 새 서버가 넣은 null만 채웁니다.
    op.execute("UPDATE user_profiles SET aibc_member = false WHERE aibc_member IS NULL")
    op.execute("UPDATE user_profiles SET gpa = 0 WHERE gpa IS NULL")
    op.alter_column("user_profiles", "aibc_member", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("user_profiles", "gpa", existing_type=sa.Float(), nullable=False)
    op.drop_column("user_profiles", "completed_semesters")
