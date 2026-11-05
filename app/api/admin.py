"""관리자 전용 (계획서 7.1, 7.2, 7.3, 9.3). 관리자가 아니면 403. 구현: T27."""

from fastapi import APIRouter, status

from app.api._stub import NOT_IMPLEMENTED, not_implemented
from app.schemas.api.admin import CrawlRunsResponse, ReextractResponse

router = APIRouter(prefix="/admin", tags=["admin"], responses=NOT_IMPLEMENTED)


@router.get("/crawl-runs", response_model=CrawlRunsResponse)
def crawl_runs() -> CrawlRunsResponse:
    not_implemented()


@router.post(
    "/notices/{notice_id}/reextract",
    response_model=ReextractResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reextract(notice_id: int) -> ReextractResponse:
    """추출하지 않고 extract_status = PENDING으로만 바꾼 뒤 바로 응답합니다(9.3)."""
    not_implemented()
