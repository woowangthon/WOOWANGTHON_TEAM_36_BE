"""이메일 가입·로그인, 동의, 내 정보, 탈퇴 (계획서 7.2, 7.3). 구현: T19."""

from fastapi import APIRouter, status

from app.api._stub import NOT_IMPLEMENTED, not_implemented
from app.schemas.api.auth import ConsentRequest, LoginRequest, MeResponse, SignupRequest

router = APIRouter(prefix="/auth", tags=["auth"], responses=NOT_IMPLEMENTED)


@router.post("/signup", response_model=MeResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest) -> MeResponse:
    """가입하고 JWT 쿠키를 발급합니다."""
    not_implemented()


@router.post("/login", response_model=MeResponse)
def login(body: LoginRequest) -> MeResponse:
    """로그인하고 JWT 쿠키를 발급합니다. 시도 제한을 넘으면 429."""
    not_implemented()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> None:
    """JWT 쿠키를 지웁니다."""
    not_implemented()


@router.post("/consent", response_model=MeResponse)
def consent(body: ConsentRequest) -> MeResponse:
    """개인정보·민감정보 동의를 저장합니다."""
    not_implemented()


@router.delete("/consent/sensitive", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_sensitive_consent() -> None:
    """민감정보 동의를 철회하고 특수자격 값을 지웁니다."""
    not_implemented()


@router.get("/me", response_model=MeResponse)
def me() -> MeResponse:
    """로그인 사용자 (동의 여부, 프로필 입력 여부, 관리자 여부 포함)."""
    not_implemented()


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me() -> None:
    """회원 탈퇴: 계정·프로필·상태를 즉시 삭제합니다."""
    not_implemented()
