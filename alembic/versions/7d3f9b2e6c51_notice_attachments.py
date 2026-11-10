"""첨부별 텍스트·상태 표 추가: 첨부(ZIP 안의 파일 포함)마다 읽은 결과를 남겨 근거와 판정에 씀

Revision ID: 7d3f9b2e6c51
Revises: 5e9a1b7c3d42
Create Date: 2026-11-09 21:30:00.000000+09:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d3f9b2e6c51"
down_revision: str | Sequence[str] | None = "5e9a1b7c3d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notice_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notice_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("file_kind", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "file_kind IN ('PDF', 'HWP', 'HWPX', 'DOCX', 'IMAGE', 'ZIP', 'OTHER')",
            name=op.f("ck_notice_attachments_file_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('OK', 'NO_TEXT', 'UNSUPPORTED', 'FAILED', 'TOO_LARGE', 'SKIPPED')",
            name=op.f("ck_notice_attachments_status"),
        ),
        sa.ForeignKeyConstraint(
            ["notice_id"],
            ["notices.id"],
            name=op.f("fk_notice_attachments_notice_id_notices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notice_attachments")),
        sa.UniqueConstraint(
            "notice_id", "position", name=op.f("uq_notice_attachments_notice_id_position")
        ),
    )
    op.create_index(
        op.f("ix_notice_attachments_notice_id"), "notice_attachments", ["notice_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notice_attachments_notice_id"), table_name="notice_attachments")
    op.drop_table("notice_attachments")
