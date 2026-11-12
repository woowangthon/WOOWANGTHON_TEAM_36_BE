"""첨부 형식에 XLSX 추가: 엑셀 첨부(참여 대학 목록 등)를 시트·행 텍스트로 읽어 추출에 넣음

Revision ID: e5a1c9d3b7f2
Revises: c7e2a9d4f618
Create Date: 2026-11-12 10:00:00.000000+09:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a1c9d3b7f2"
down_revision: str | Sequence[str] | None = "c7e2a9d4f618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notice_attachments"
CONSTRAINT = "ck_notice_attachments_file_kind"
OLD_KINDS = "'PDF', 'HWP', 'HWPX', 'DOCX', 'IMAGE', 'ZIP', 'OTHER'"
NEW_KINDS = "'PDF', 'HWP', 'HWPX', 'DOCX', 'XLSX', 'IMAGE', 'ZIP', 'OTHER'"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, f"file_kind IN ({NEW_KINDS})")


def downgrade() -> None:
    # 예전 코드는 엑셀을 읽지 못하므로 읽지 못한 첨부로 되돌립니다.
    op.execute(
        f"UPDATE {TABLE} SET file_kind = 'OTHER', status = 'UNSUPPORTED', text = '' "
        "WHERE file_kind = 'XLSX'"
    )
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, f"file_kind IN ({OLD_KINDS})")
