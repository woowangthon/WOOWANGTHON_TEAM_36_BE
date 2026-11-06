"""관리자 전용 (계획서 7.1, 7.2, 7.3, 9.3). 관리자가 아니면 403.

- 관리자는 `ADMIN_EMAILS`에 있고 Google로 확인된 계정입니다(`AdminUser`, 7.3).
- 재추출은 요청 안에서 추출하지 않습니다. `extract_status = PENDING`으로만 바꾸고 바로 응답하며,
  실제 추출은 다음 수집 작업이 합니다(9.3, 8.1). 그래서 이 모듈은 추출 모듈을 import하지 않습니다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_event
from app.core.security import AdminUser
from app.models import CrawlRun, Notice, Scholarship
from app.parsers.dates import to_kst
from app.schemas.api.admin import (
    AdminNotice,
    CrawlRunOut,
    CrawlRunsResponse,
    ReextractResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

DB = Annotated[Session, Depends(get_db)]
AUTH_ERRORS = {401: {"description": "로그인 필요"}, 403: {"description": "관리자 아님"}}

RUN_LIMIT = 30  # 최근 수집 기록 (3개 사이트 × 약 10회)
RECENT_NOTICE_LIMIT = 50  # 실패·대기 공지는 모두, 나머지는 최근 50건까지


@router.get("/crawl-runs", response_model=CrawlRunsResponse, responses=AUTH_ERRORS)
def crawl_runs(admin: AdminUser, db: DB) -> CrawlRunsResponse:
    runs = db.scalars(
        select(CrawlRun).order_by(CrawlRun.started_at.desc(), CrawlRun.id.desc()).limit(RUN_LIMIT)
    )
    items = [
        CrawlRunOut(
            id=run.id,
            source_code=run.source_code,
            status=run.status,
            started_at=to_kst(run.started_at),
            finished_at=to_kst(run.finished_at) if run.finished_at else None,
            new_count=run.new_count,
            error=run.error,
        )
        for run in runs
    ]
    return CrawlRunsResponse(items=items, notices=_notices(db))


def _notices(db: Session) -> list[AdminNotice]:
    """추출 실패·대기 공지를 먼저(오래된 것부터), 그다음 최근 공지를 게시일 최신순으로."""
    count = (
        select(func.count(Scholarship.id))
        .where(Scholarship.notice_id == Notice.id)
        .correlate(Notice)
        .scalar_subquery()
    )
    needs_attention = Notice.extract_status.in_(("PENDING", "FAILED"))
    attention_rows = db.execute(
        select(Notice, count).where(needs_attention).order_by(Notice.posted_at, Notice.id)
    ).all()
    recent_rows = db.execute(
        select(Notice, count)
        .where(~needs_attention)
        .order_by(Notice.posted_at.desc(), Notice.id.desc())
        .limit(RECENT_NOTICE_LIMIT)
    ).all()
    return [_to_admin_notice(notice, n) for notice, n in [*attention_rows, *recent_rows]]


def _to_admin_notice(notice: Notice, scholarship_count: int) -> AdminNotice:
    return AdminNotice(
        id=notice.id,
        source_code=notice.source_code,
        title=notice.title,
        url=notice.url,
        posted_at=to_kst(notice.posted_at),
        notice_kind=notice.notice_kind,
        extract_status=notice.extract_status,
        extracted_at=to_kst(notice.extracted_at) if notice.extracted_at else None,
        scholarship_count=scholarship_count,
    )


@router.post(
    "/notices/{notice_id}/reextract",
    response_model=ReextractResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**AUTH_ERRORS, 404: {"description": "공지 없음"}},
)
def reextract(notice_id: int, admin: AdminUser, db: DB) -> ReextractResponse:
    """추출하지 않고 extract_status = PENDING으로만 바꾼 뒤 바로 응답합니다(9.3)."""
    notice = db.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="공지를 찾을 수 없습니다")
    notice.extract_status = "PENDING"
    db.commit()
    log_event(logger, "재추출 요청", notice_id=notice.id, user_id=admin.id)
    return ReextractResponse(notice_id=notice.id, extract_status="PENDING")
