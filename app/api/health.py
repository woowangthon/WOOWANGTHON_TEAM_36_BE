"""상태 확인 (계획서 9.5): DB 연결 확인 없이 200, 인증 불필요. 깨우기 핑(9.8)도 이 경로를 씁니다."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
