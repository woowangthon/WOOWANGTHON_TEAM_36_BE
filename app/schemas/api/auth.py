"""인증 API DTO (계획서 7.2, 7.3)."""

from pydantic import BaseModel, ConfigDict, Field

# 메일 발송이 없어 이메일 인증은 하지 않습니다(7.3). 형식만 확인합니다.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class ConsentRequest(BaseModel):
    """개인정보 동의는 필수, 민감정보(장애·보훈) 동의는 선택입니다(4.2)."""

    model_config = ConfigDict(extra="forbid")

    privacy: bool
    sensitive: bool


class MeResponse(BaseModel):
    """로그인 사용자. 프론트 라우팅 가드가 이 값으로 이동할 화면을 정합니다."""

    id: int
    email: str
    privacy_agreed: bool  # false면 /consent
    sensitive_agreed: bool
    profile_completed: bool  # false면 /profile
    is_admin: bool  # ADMIN_EMAILS에 있고 google_sub가 있는 계정 (7.3)
    google_linked: bool
    has_password: bool  # false면 Google 전용 계정
