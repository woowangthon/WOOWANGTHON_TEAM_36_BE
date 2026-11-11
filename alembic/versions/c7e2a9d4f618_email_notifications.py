"""이메일 알림(계획서 7.4): 수신 설정, 장학금 처음 저장 시각, 발송 기록·하루 1회 실행 표

Revision ID: c7e2a9d4f618
Revises: a4c8e1f5b2d9
Create Date: 2026-11-11 21:00:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a9d4f618"
down_revision: str | Sequence[str] | None = "a4c8e1f5b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_notifications", sa.Boolean(), server_default="true", nullable=False),
    )

    # 기존 장학금은 공지 게시일을 넣습니다. now()로 채우면 첫 발송 때 전부 "새 장학금"으로 나갑니다.
    op.add_column(
        "scholarships", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE scholarships s SET created_at = n.posted_at FROM notices n WHERE n.id = s.notice_id"
    )
    op.alter_column("scholarships", "created_at", nullable=False, server_default=sa.text("now()"))

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scholarship_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("apply_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('NEW_MATCH', 'DEADLINE_D3', 'DEADLINE_D1')",
            name=op.f("ck_notification_logs_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["scholarship_id"],
            ["scholarships.id"],
            name=op.f("fk_notification_logs_scholarship_id_scholarships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_logs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_logs")),
    )
    op.create_index(
        "ix_notification_logs_user_id_kind", "notification_logs", ["user_id", "kind"], unique=False
    )

    op.create_table(
        "notification_runs",
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'DONE', 'FAILED')", name=op.f("ck_notification_runs_status")
        ),
        sa.PrimaryKeyConstraint("run_date", name=op.f("pk_notification_runs")),
    )


def downgrade() -> None:
    op.drop_table("notification_runs")
    op.drop_index("ix_notification_logs_user_id_kind", table_name="notification_logs")
    op.drop_table("notification_logs")
    op.drop_column("scholarships", "created_at")
    op.drop_column("users", "email_notifications")
