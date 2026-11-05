"""SOURCES, CRAWL_RUNS (계획서 6장, 2장, 9.3)."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_values

CRAWL_RUN_STATUSES = ("RUNNING", "DONE", "FAILED")


class Source(Base):
    __tablename__ = "sources"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # SW_DEPT / AIBC / KOSAF
    name: Mapped[str] = mapped_column(String(100))
    list_url: Mapped[str] = mapped_column(String(500))
    is_internal: Mapped[bool] = mapped_column(Boolean)  # 교내 / 교외 (대시보드 필터, 7.1)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (CheckConstraint(in_values("status", CRAWL_RUN_STATUSES), name="status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(ForeignKey("sources.code"), index=True)
    status: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
