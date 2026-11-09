"""NOTICES, SCHOLARSHIPS (계획서 6장, 2장 수집 전략, 5.2 장학금 상태).

공지 1건에 장학금이 여러 개 들어갈 수 있습니다(5.2). 판정·관심 표시는 장학금 단위입니다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, in_values

NOTICE_KINDS = ("모집", "결과", "기준안내", "기타")
EXTRACT_STATUSES = ("PENDING", "DONE", "FAILED")
BENEFIT_TYPES = ("등록금성", "생활비성", "혼합")
DEADLINE_SOURCES = ("REGEX", "AI")
SCHOLARSHIP_STATUSES = ("OPEN", "ALWAYS", "CLOSED")
ATTACHMENT_KINDS = ("PDF", "HWP", "HWPX", "DOCX", "IMAGE", "ZIP", "OTHER")
# SKIPPED = 일부러 읽지 않음(결과 공지·개수 상한).
# 그 밖의 OK가 아닌 상태는 "못 읽은 첨부"입니다(D8).
ATTACHMENT_STATUSES = ("OK", "NO_TEXT", "UNSUPPORTED", "FAILED", "TOO_LARGE", "SKIPPED")
UNREAD_ATTACHMENT_STATUSES = ("NO_TEXT", "UNSUPPORTED", "FAILED", "TOO_LARGE")


class Notice(Base):
    __tablename__ = "notices"
    __table_args__ = (
        # 상단 고정 공지가 매 페이지에 다시 나와도 중복 저장되지 않게 합니다(2장).
        UniqueConstraint("source_code", "external_id"),
        CheckConstraint(in_values("notice_kind", NOTICE_KINDS), name="notice_kind"),
        CheckConstraint(in_values("extract_status", EXTRACT_STATUSES), name="extract_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(ForeignKey("sources.code"))
    external_id: Mapped[str] = mapped_column(String(100))  # document_srl / slug / seqNo
    title: Mapped[str] = mapped_column(String(500))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(String(1000))
    body_text: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )  # [{"name": ..., "url": ...}]
    content_hash: Mapped[str] = mapped_column(String(64))  # 제목 + 본문 + 첨부 목록 (2장)
    notice_kind: Mapped[str | None] = mapped_column(String(10))  # 추출 전에는 NULL
    extract_status: Mapped[str] = mapped_column(
        String(10), default="PENDING", server_default="PENDING"
    )
    # 연속 추출 실패 횟수. 3번이면 FAILED 자동 재시도에서 뺍니다(9.3). 402는 세지 않음
    extract_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_version: Mapped[str | None] = mapped_column(String(100))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    scholarships: Mapped[list["Scholarship"]] = relationship(back_populates="notice")
    # 첨부를 읽은 결과. ZIP 안의 파일은 한 행씩입니다. 새 글·바뀐 글을 수집할 때 통째로 바꿉니다.
    files: Mapped[list["NoticeAttachment"]] = relationship(
        back_populates="notice",
        order_by="NoticeAttachment.position",
        cascade="all, delete-orphan",
    )


class NoticeAttachment(Base):
    """첨부 1개(ZIP이면 안의 파일 1개)에서 읽은 텍스트와 상태. 원본 파일은 저장하지 않습니다."""

    __tablename__ = "notice_attachments"
    __table_args__ = (
        UniqueConstraint("notice_id", "position"),
        CheckConstraint(in_values("file_kind", ATTACHMENT_KINDS), name="file_kind"),
        CheckConstraint(in_values("status", ATTACHMENT_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 추출 입력에 붙이는 순서
    name: Mapped[str] = mapped_column(String(500))  # ZIP 안의 파일은 "ZIP 이름 / 파일 이름"
    url: Mapped[str] = mapped_column(String(1000))  # ZIP 안의 파일은 ZIP의 URL
    file_kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    notice: Mapped[Notice] = relationship(back_populates="files")


class Scholarship(Base):
    __tablename__ = "scholarships"
    __table_args__ = (
        CheckConstraint(in_values("benefit_type", BENEFIT_TYPES), name="benefit_type"),
        CheckConstraint(in_values("deadline_source", DEADLINE_SOURCES), name="deadline_source"),
        CheckConstraint(in_values("status", SCHOLARSHIP_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    benefit_type: Mapped[str | None] = mapped_column(String(10))
    benefit_amount_text: Mapped[str | None] = mapped_column(String(300))
    apply_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 정렬·마감 기준
    deadline_source: Mapped[str | None] = mapped_column(String(10))
    deadline_conflict: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )  # "마감일 확인 필요" 표시
    status: Mapped[str] = mapped_column(String(10))
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB)  # ScholarshipCriteria (5.2)

    notice: Mapped[Notice] = relationship(back_populates="scholarships")
