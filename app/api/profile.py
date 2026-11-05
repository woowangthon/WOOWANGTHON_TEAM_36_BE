"""프로필 조회·수정 (계획서 4.1, 7.2). 구현: T21."""

from fastapi import APIRouter

from app.api._stub import NOT_IMPLEMENTED, not_implemented
from app.schemas.api.profile import Profile

router = APIRouter(prefix="/profile", tags=["profile"], responses=NOT_IMPLEMENTED)


@router.get("", response_model=Profile)
def get_profile() -> Profile:
    not_implemented()


@router.put("", response_model=Profile)
def put_profile(body: Profile) -> Profile:
    not_implemented()
