"""맞춤 목록·상세·내 상태 (계획서 5.3, 7.1, 7.2). 구현: T23."""

from fastapi import APIRouter, status

from app.api._stub import NOT_IMPLEMENTED, not_implemented
from app.extraction.schemas import BenefitType
from app.schemas.api.scholarships import (
    ResultFilter,
    ScholarshipDetail,
    ScholarshipListResponse,
    SortOrder,
    SourceFilter,
    StateFilter,
    StateRequest,
    StateResponse,
)

router = APIRouter(prefix="/scholarships", tags=["scholarships"], responses=NOT_IMPLEMENTED)


@router.get("", response_model=ScholarshipListResponse)
def list_scholarships(
    result: ResultFilter = "all",
    sort: SortOrder = "deadline",
    source: SourceFilter | None = None,
    benefit_type: BenefitType | None = None,
    state: StateFilter | None = None,
) -> ScholarshipListResponse:
    """장학금 단위 맞춤 목록(판정 결과·내 상태 포함).

    기본은 CLOSED와 숨긴 장학금을 빼고, state를 주면 내 장학금 화면용으로 마감된 것도 포함합니다.
    """
    not_implemented()


@router.get("/{scholarship_id}", response_model=ScholarshipDetail)
def get_scholarship(scholarship_id: int) -> ScholarshipDetail:
    """상세 + 조건별 판정 + 같은 공지의 다른 장학금."""
    not_implemented()


@router.put("/{scholarship_id}/state", response_model=StateResponse)
def put_state(scholarship_id: int, body: StateRequest) -> StateResponse:
    """관심 / 신청완료 / 숨기기."""
    not_implemented()


@router.delete("/{scholarship_id}/state", status_code=status.HTTP_204_NO_CONTENT)
def delete_state(scholarship_id: int) -> None:
    not_implemented()
