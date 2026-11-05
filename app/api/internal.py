"""정기 수집 (계획서 9.3). Cloud Scheduler 전용, X-Cron-Token이 다르면 403. 구현: T17."""

from typing import Annotated

from fastapi import APIRouter, Header

from app.api._stub import NOT_IMPLEMENTED, not_implemented
from app.schemas.api.admin import CrawlResponse

router = APIRouter(prefix="/internal", tags=["internal"], responses=NOT_IMPLEMENTED)


@router.post("/crawl", response_model=CrawlResponse)
def crawl(x_cron_token: Annotated[str | None, Header()] = None) -> CrawlResponse:
    """수집 → 추출 → 저장 → 마감 상태 갱신을 요청 안에서 끝까지 실행합니다."""
    not_implemented()
