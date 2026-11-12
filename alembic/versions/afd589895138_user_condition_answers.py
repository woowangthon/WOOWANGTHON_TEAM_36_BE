"""자격을 좁히는 조건에 사용자가 답한 값(계획서 5.3): 사용자·장학금·조건 문장 키마다 암호화한 YES/NO

Revision ID: afd589895138
Revises: c7e2a9d4f618
Create Date: 2026-11-12 11:50:01.382873+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "afd589895138"
down_revision: str | Sequence[str] | None = "c7e2a9d4f618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_condition_answers",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scholarship_id", sa.Integer(), nullable=False),
        sa.Column("condition_key", sa.String(length=16), nullable=False),
        sa.Column("enc_answer", sa.LargeBinary(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scholarship_id"],
            ["scholarships.id"],
            name=op.f("fk_user_condition_answers_scholarship_id_scholarships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_condition_answers_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "scholarship_id", "condition_key", name=op.f("pk_user_condition_answers")
        ),
    )


def downgrade() -> None:
    op.drop_table("user_condition_answers")
