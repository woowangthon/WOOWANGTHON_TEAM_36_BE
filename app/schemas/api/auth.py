"""인증 API DTO (계획서 7.2, 7.3)."""

import string

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 형식만 확인합니다. 소유 확인은 가입 인증번호로 합니다(7.3).
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
TOKEN_MAX_LENGTH = 2048


class SignupCodeRequest(BaseModel):
    """가입 1단계: 인증번호 메일 받기 (7.3)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)


class SignupCodeResponse(BaseModel):
    code_token: str  # 2단계에 그대로 돌려줌. 번호는 들어 있지 않음
    expires_in: int  # 인증번호 유효 시간(초)
    resend_after: int  # 다시 받기까지 기다릴 시간(초)


class SignupVerifyRequest(BaseModel):
    """가입 2단계: 메일로 받은 6자리 번호 확인."""

    model_config = ConfigDict(extra="forbid")

    code_token: str = Field(max_length=TOKEN_MAX_LENGTH)
    code: str

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 6 or not v.isascii() or not v.isdigit():
            raise ValueError("인증번호 6자리 숫자를 입력하세요")
        return v


class SignupVerifyResponse(BaseModel):
    signup_token: str  # 3단계(가입)에 그대로 돌려줌
    expires_in: int  # 가입을 마쳐야 하는 시간(초)


# 가입·변경 비밀번호 규칙 (7.3, T37): 8~16자, 영문·숫자·특수문자 각 1자 이상.
# 로그인은 이 규칙을 보지 않습니다(규칙 전에 가입한 비밀번호도 로그인할 수 있게).
PASSWORD_MIN = 8
PASSWORD_MAX = 16
_LETTERS = frozenset(string.ascii_letters)
_DIGITS = frozenset(string.digits)
_SPECIALS = frozenset(string.punctuation)


def validate_new_password(v: str) -> str:
    """어긴 규칙을 한국어 문장으로 알려 줍니다. 순서: 길이 → 허용 문자 → 영문 → 숫자 → 특수문자."""
    if not PASSWORD_MIN <= len(v) <= PASSWORD_MAX:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 입력하세요")
    chars = set(v)
    if not chars <= _LETTERS | _DIGITS | _SPECIALS:
        raise ValueError("영문·숫자·특수문자만 쓸 수 있어요")
    if not chars & _LETTERS:
        raise ValueError("영문을 1자 이상 넣어 주세요")
    if not chars & _DIGITS:
        raise ValueError("숫자를 1자 이상 넣어 주세요")
    if not chars & _SPECIALS:
        raise ValueError("특수문자를 1자 이상 넣어 주세요")
    return v


class SignupRequest(BaseModel):
    """가입 3단계: 확인된 이메일(`signup_token`)로 계정 만들기. 이메일은 따로 받지 않습니다."""

    model_config = ConfigDict(extra="forbid")

    signup_token: str = Field(max_length=TOKEN_MAX_LENGTH)
    password: str

    _password = field_validator("password")(validate_new_password)


class PasswordChangeRequest(BaseModel):
    """내 정보 화면의 비밀번호 변경 (T37). 새 비밀번호는 가입과 같은 규칙입니다."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str

    _new_password = field_validator("new_password")(validate_new_password)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class ConsentRequest(BaseModel):
    """개인정보 동의는 필수, 민감정보(장애·보훈) 동의는 선택입니다(4.2)."""

    model_config = ConfigDict(extra="forbid")

    privacy: bool
    sensitive: bool


class NotificationSettingRequest(BaseModel):
    """장학금 알림 메일 수신 켜기·끄기 (7.4)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    email_notifications: bool


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
    # '내 정보' 요약 카드용 (FE T52, 2026-11-11 결정). 프로필이 없으면 null.
    # 모든 화면이 부르는 응답이라 암호화하는 민감 필드(소득분위·주소·특수자격)는 넣지 않습니다(4.2).
    nickname: str | None = None
    completed_semesters: int | None = None
    gpa: float | None = None  # 직전학기 평점. 이수학기 0이면 null
    email_notifications: bool = True  # 장학금 알림 메일 수신 (7.4)
