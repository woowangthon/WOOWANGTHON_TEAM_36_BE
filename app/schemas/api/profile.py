"""프로필 API DTO (계획서 4.1, 7.2).

소득분위·주소·특수자격은 DB에 암호화해 저장하지만(4.1), API에서는 평문으로 주고받습니다.
이 값들은 로그에 남기지 않습니다(4.2).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extraction.schemas import INCOME_BRACKETS, EnrollmentStatus, Gender

Department = Literal["소프트웨어학부", "기타"]
SpecialFlag = Literal["다자녀", "보훈", "장애"]  # 보훈·장애는 민감정보 동의가 있어야 저장
FullTuitionWaiver = Literal["예", "아니오", "모름"]


class Region(BaseModel):
    """시/도 + 시/군/구. 상세 주소는 받지 않습니다(4.1)."""

    model_config = ConfigDict(extra="forbid")

    sido: str = Field(min_length=1, max_length=20)  # 예: 충청남도
    sigungu: str | None = Field(default=None, max_length=20)  # 예: 논산시


class Profile(BaseModel):
    """GET 응답과 PUT 요청에 같은 형식을 씁니다. 선택 항목의 null은 '입력 안 함·모름'입니다."""

    model_config = ConfigDict(extra="forbid")

    # 필수
    name: str = Field(min_length=1, max_length=100)
    gender: Gender
    birth_year: int = Field(ge=1900, le=2100)  # 나이는 서버가 계산(5.3)
    gpa: float = Field(ge=0, le=4.5)  # 직전학기 평점, 4.5 만점
    credits: int = Field(ge=0, le=30)  # 직전학기 이수학점
    grade: int = Field(ge=1, le=4)
    extra_semester: bool
    enrollment_status: EnrollmentStatus
    department: Department
    aibc_member: bool

    # 선택 (소득분위·주소·특수자격은 암호화 저장)
    income_bracket: float | None = None  # 기초 0, 차상위 0.5, 1~10구간. null = 모름
    self_region: Region | None = None
    parent_region: Region | None = None
    special_flags: list[SpecialFlag] | None = None
    full_tuition_waiver: FullTuitionWaiver | None = None

    @field_validator("income_bracket")
    @classmethod
    def _income(cls, v: float | None) -> float | None:
        if v is not None and v not in INCOME_BRACKETS:
            raise ValueError("income_bracket은 0, 0.5, 1~10 중 하나여야 합니다")
        return v
