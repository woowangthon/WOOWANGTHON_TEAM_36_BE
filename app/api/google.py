"""Google 로그인 (계획서 7.3). 구현: T20.

두 경로 모두 응답 본문 없이 다른 주소로 이동(302)합니다.
"""

from fastapi import APIRouter

from app.api._stub import NOT_IMPLEMENTED, not_implemented

router = APIRouter(prefix="/auth/google", tags=["auth"], responses=NOT_IMPLEMENTED)

REDIRECT = {302: {"description": "다른 주소로 이동"}}


@router.get("/login", status_code=302, responses=REDIRECT)
def google_login(next: str | None = None) -> None:
    """state·PKCE를 만들고 Google 로그인 화면으로 이동합니다. next는 내부 경로만 허용."""
    not_implemented()


@router.get("/callback", status_code=302, responses=REDIRECT)
def google_callback(code: str | None = None, state: str | None = None) -> None:
    """코드 교환 → ID 토큰 검증 → JWT 쿠키 발급 후 화면으로 이동합니다."""
    not_implemented()
