"""정기 수집 (계획서 9.3). Cloud Scheduler 전용, X-Cron-Token이 다르면 403."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.schemas.api.admin import CrawlResponse

router = APIRouter(prefix="/internal", tags=["internal"])


FORBIDDEN = {403: {"description": "X-Cron-Token 불일치"}}


@router.post("/crawl", response_model=CrawlResponse, responses=FORBIDDEN)
def crawl(x_cron_token: Annotated[str | None, Header()] = None) -> CrawlResponse:
    """수집 → 추출 → 저장 → 마감 상태 갱신을 요청 안에서 끝까지 실행합니다.

    `BackgroundTasks`를 쓰지 않습니다(9.3). 다른 실행이 진행 중이면 skipped=true로 바로 끝납니다.
    """
    expected = get_settings().cron_token
    if (
        expected is None
        or x_cron_token is None
        or not secrets.compare_digest(x_cron_token.encode(), expected.get_secret_value().encode())
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    from app.scheduler.jobs import run_crawl  # 수집 의존성은 요청 때 불러옴(9.5)

    outcome = run_crawl()
    return CrawlResponse(skipped=outcome.skipped, runs=outcome.runs)
