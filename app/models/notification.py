"""NOTIFICATION_LOGS, NOTIFICATION_RUNS (계획서 6장, 7.4 이메일 알림).

- NOTIFICATION_LOGS: 사용자에게 보낸 알림 항목. 같은 알림을 두 번 보내지 않기 위한 기록입니다.
  마감 알림은 그때의 마감(`apply_end`)까지 같아야 "이미 보냄"이라, 마감이 연장되면 다시 알립니다.
  탈퇴하면 함께 지워집니다(4.2).
- NOTIFICATION_RUNS: 서울 날짜마다 한 행. 알림 발송이 하루 한 번만 실행되게 합니다(9.3).
- 추출 모듈(app/extraction)은 이 파일을 import하지 않습니다(8.1 개인정보 경계).
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_values

NOTIFICATION_KINDS = ("NEW_MATCH", "DEADLINE_D3", "DEADLINE_D1")
NOTIFICATION_RUN_STATUSES = ("RUNNING", "DONE", "FAILED")


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        CheckConstraint(in_values("kind", NOTIFICATION_KINDS), name="kind"),
        Index("ix_notification_logs_user_id_kind", "user_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scholarship_id: Mapped[int] = mapped_column(ForeignKey("scholarships.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))
    # 알림을 보낼 때의 마감 시각. NEW_MATCH는 NULL
    apply_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRun(Base):
    __tablename__ = "notification_runs"
    __table_args__ = (
        CheckConstraint(in_values("status", NOTIFICATION_RUN_STATUSES), name="status"),
    )

    run_date: Mapped[date] = mapped_column(Date, primary_key=True)  # 서울 날짜
    status: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
