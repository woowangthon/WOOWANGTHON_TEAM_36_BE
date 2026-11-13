"""프로필 API DTO (계획서 4.1, 4.3, 7.2).

소득분위·주소·특수자격은 DB에 암호화해 저장하지만(4.1), API에서는 평문으로 주고받습니다.
이 값들은 로그에 남기지 않습니다(4.2).

- `Profile`: GET 응답과 판정(5.3)에 씁니다. 저장된 값을 그대로 읽으므로 범위 검사를 하지 않습니다.
  규칙이 바뀌어도 기존 사용자의 조회가 실패하지 않게 하기 위함입니다(4.3).
- `ProfileUpdate`: PUT 요청. 엄격한 입력 규칙은 여기에만 둡니다(4.3).
"""

import re
import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.extraction.schemas import INCOME_BRACKETS, Gender
from app.parsers.dates import now_kst
from app.schemas.api.regions import REGIONS

# 프로필 전용 재학상태. 공지 추출용 EnrollmentStatus(4개)와 따로 둡니다(추출 스키마는 그대로).
ProfileEnrollmentStatus = Literal["재학", "휴학"]
SpecialFlag = Literal["다자녀", "보훈", "장애"]  # 특수자격은 민감정보 동의가 있어야 저장(4.1·4.2)
FullTuitionWaiver = Literal["예", "아니오"]  # null = 모름

# 전환 기간(배포 ①~③)에만 받는 옛 칸 값. 배포 ③에서 지웁니다.
LegacyDepartment = Literal["소프트웨어학부", "기타"]
LegacyFullTuitionWaiver = Literal["예", "아니오", "모름"]

MIN_BIRTH_YEAR = 1950
MIN_AGE = 15
MAX_SEMESTERS = 16
# 편입생은 3학년 편입으로 보고 4학기를 더해 판정합니다(4.1, 2026-11-12).
TRANSFER_CREDIT_SEMESTERS = 4
MAX_TRANSFER_SEMESTERS = MAX_SEMESTERS - TRANSFER_CREDIT_SEMESTERS
NICKNAME = re.compile(r"[가-힣A-Za-z0-9]{2,10}")
# 100점 환산 = 평점 × 10 + 52.5 (2026-11-10 결정, 시연용 추정값).
# 충북대 등급표에서 원점수는 평점×10+50~55 사이라 그 가운데 값을 씁니다. 학생은 입력하지 않습니다.
SCORE_100_OFFSET = 52.5


def grade_of(semesters: int) -> int:
    """이수학기 → 학년 (4.1): min(이수학기 ÷ 2(버림) + 1, 4)."""
    return min(semesters // 2 + 1, 4)


def is_extra_semester(semesters: int) -> bool:
    """이수학기 8 이상이면 초과학기 (4.1)."""
    return semesters >= 8


def semesters_from_grade(grade: int, extra_semester: bool) -> int:
    """옛 학년 값 → 이수학기 (4.1 기존 사용자 이전): 학년 n → (n−1)×2+1, 초과학기면 8."""
    return 8 if extra_semester else (grade - 1) * 2 + 1


def score_100_from_gpa(gpa: float | None) -> float | None:
    """평점(4.5 만점) → 100점 환산 추정값. 평점이 없으면 null, 최대 100."""
    if gpa is None:
        return None
    return min(100.0, round(gpa * 10 + SCORE_100_OFFSET, 2))


def max_birth_year() -> int:
    """'올해'는 Asia/Seoul 기준입니다(4.3)."""
    return now_kst().year - MIN_AGE


class Region(BaseModel):
    """시/도 + 시/군/구. 상세 주소는 받지 않습니다(4.1)."""

    model_config = ConfigDict(extra="forbid")

    sido: str = Field(min_length=1, max_length=20)  # 예: 충청남도
    sigungu: str | None = Field(default=None, max_length=20)  # 예: 논산시


class RegionUpdate(Region):
    """PUT용 주소. 시/도·시/군/구가 목록(`regions.py`)에 있고 서로 맞는지 봅니다(정의서 15-4·15-5).

    조회(`Region`)는 검사하지 않아, 목록이 바뀌어도 기존 사용자의 조회가 실패하지 않습니다(15-8).
    """

    @model_validator(mode="after")
    def _in_list(self) -> "RegionUpdate":
        if self.sido not in REGIONS:
            raise ValueError("목록에 있는 시/도를 고르세요")
        if self.sigungu == "":
            self.sigungu = None
        if self.sigungu is not None and self.sigungu not in REGIONS[self.sido]:
            raise ValueError("고른 시/도에 있는 시/군/구를 고르세요")
        return self


class Profile(BaseModel):
    """GET 응답·판정용. 선택 항목의 null은 '입력 안 함·모름'입니다."""

    name: str | None  # 닉네임. 기존 실명을 지운 사용자는 null(다시 입력 요청)
    gender: Gender
    birth_year: int  # 나이는 서버가 계산(5.3)
    # 마친 정규학기 수. 편입생이면 편입 후 이 학교에서 마친 학기 수(4.1).
    # 학년·초과학기·남은 학기는 effective_semesters()로 계산합니다.
    completed_semesters: int
    enrollment_status: ProfileEnrollmentStatus
    aibc_member: bool | None = None  # null = 모름
    is_transfer: bool = False  # 편입생 (T39)
    is_foreign: bool = False  # 외국인(대한민국 국적이 아님) (T39)

    # 성적 (4.1, C안). 이수학기 0이면 모두 null
    gpa: float | None = None  # 직전학기 평점평균, 4.5 만점
    score_100: float | None = None  # 직전학기 100점 환산. 저장값과 상관없이 평점으로 계산
    credits: int | None = None  # 직전학기 취득학점(F 제외)
    gpa_total: float | None = None  # 전체 평점평균
    score_100_total: float | None = None  # 전체 100점 환산. 전체 평점평균으로 계산

    # 선택 (소득분위·주소·특수자격은 암호화 저장)
    income_bracket: float | None = None  # 기초 0, 차상위 0.5, 1~10구간. null = 모름
    self_region: Region | None = None
    parent_region: Region | None = None
    special_flags: list[SpecialFlag] | None = None
    full_tuition_waiver: FullTuitionWaiver | None = None

    @model_validator(mode="after")
    def _score_from_gpa(self) -> "Profile":
        # 예전에 학생이 직접 입력해 저장된 값도 평점으로 다시 계산합니다(화면·판정이 같은 값).
        self.score_100 = score_100_from_gpa(self.gpa)
        self.score_100_total = score_100_from_gpa(self.gpa_total)
        return self


def effective_semesters(p: Profile) -> int:
    """판정용 이수학기: 편입생이면 편입 후 마친 학기 + 4 (4.1)."""
    return p.completed_semesters + (TRANSFER_CREDIT_SEMESTERS if p.is_transfer else 0)


def _two_decimals(value: float | None, label: str) -> float | None:
    if value is not None and Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError(f"{label}은 소수 둘째 자리까지 입력하세요")
    return value


def _between(value: float | None, low: float, high: float, label: str) -> float | None:
    if value is not None and not low <= value <= high:
        raise ValueError(f"{label}은 {low:g}~{high:g} 사이로 입력하세요")
    return value


def _grade_fields(p: "ProfileUpdate") -> list[tuple[float | int | None, str]]:
    """학생이 입력하는 성적 3칸과 빠졌을 때 쓰는 문장 앞부분(조사 포함). 화면의 칸 순서와 같습니다.

    100점 환산 2칸은 평점으로 계산하므로 여기에 없습니다.
    """
    return [
        (p.gpa, "직전학기 평점을"),
        (p.credits, "직전학기 이수학점을"),
        (p.gpa_total, "전체 평점평균을"),
    ]


class ProfileUpdate(BaseModel):
    """PUT 요청. 입력 검증 규칙(4.3)을 여기서 지킵니다.

    선택 항목의 null은 '입력 안 함·모름'입니다.
    """

    model_config = ConfigDict(extra="forbid")

    # 필수
    name: str  # 닉네임
    gender: Gender
    birth_year: int
    completed_semesters: int | None = None  # 필수. 전환 기간에만 옛 grade로 대신할 수 있음
    aibc_member: StrictBool | None  # "yes"·1 같은 값은 거부
    # 편입생·외국인 (T39). 배포된 옛 화면이 보내지 않아도 저장되게 기본값 false
    is_transfer: StrictBool = False
    is_foreign: StrictBool = False

    # 서비스 대상이 재학생이라 화면에서 묻지 않습니다(2026-11-10 결정). 보내지 않으면 재학으로 저장.
    # 열려 있던 옛 화면이 보내는 값은 전환 기간에만 받습니다(배포 ③에서 칸을 지움).
    enrollment_status: ProfileEnrollmentStatus | None = None

    # 성적: 이수학기가 1 이상이면 평점·이수학점·전체 평점평균 필수(2026-11-10 결정),
    # 0이면 모두 버리고 null로 저장
    gpa: float | None = None
    credits: int | None = None
    gpa_total: float | None = None
    # 100점 환산은 평점으로 계산합니다(2026-11-10 결정). 화면이 보내도 받고 무시합니다.
    score_100: float | None = None
    score_100_total: float | None = None

    # 선택
    income_bracket: float | None = None
    self_region: RegionUpdate | None = None
    parent_region: RegionUpdate | None = None
    special_flags: list[SpecialFlag] | None = None
    full_tuition_waiver: LegacyFullTuitionWaiver | None = None  # "모름"은 전환 기간에만 받음

    # 전환 기간(배포 ①~③)에만 받는 옛 칸. 열려 있던 옛 화면이 보냅니다.
    grade: int | None = Field(default=None, ge=1, le=4)
    extra_semester: StrictBool | None = None
    department: LegacyDepartment | None = None  # 받고 무시

    @field_validator("name")
    @classmethod
    def _nickname(cls, v: str) -> str:
        v = unicodedata.normalize("NFC", v)
        if any(unicodedata.category(ch) == "Cc" for ch in v):  # 줄바꿈·탭 같은 제어문자
            raise ValueError("닉네임은 2~10자의 한글·영문·숫자로 입력하세요")
        v = v.strip()
        if not v:
            raise ValueError("닉네임을 입력하세요")
        if not NICKNAME.fullmatch(v):
            raise ValueError("닉네임은 2~10자의 한글·영문·숫자로 입력하세요")
        if v.isdigit():
            raise ValueError("닉네임은 숫자만으로 만들 수 없어요")
        return v

    @field_validator("birth_year")
    @classmethod
    def _birth_year(cls, v: int) -> int:
        high = max_birth_year()
        if not MIN_BIRTH_YEAR <= v <= high:
            raise ValueError(f"출생연도는 {MIN_BIRTH_YEAR}~{high} 사이로 입력하세요")
        return v

    @field_validator("completed_semesters")
    @classmethod
    def _semesters(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= MAX_SEMESTERS:
            raise ValueError(f"이수학기는 0~{MAX_SEMESTERS} 사이로 입력하세요")
        return v

    @field_validator("gpa", "gpa_total")
    @classmethod
    def _gpa(cls, v: float | None) -> float | None:
        return _two_decimals(_between(v, 0, 4.5, "평점"), "평점")

    @field_validator("credits")
    @classmethod
    def _credits(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= 30:
            raise ValueError("이수학점은 0~30 사이로 입력하세요")
        return v

    @field_validator("income_bracket")
    @classmethod
    def _income(cls, v: float | None) -> float | None:
        if v is not None and v not in INCOME_BRACKETS:
            raise ValueError("소득분위는 기초·차상위·1~10구간 중에서 고르세요")
        return v

    @field_validator("special_flags")
    @classmethod
    def _dedupe(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _derive(self) -> "ProfileUpdate":
        if self.completed_semesters is None:
            if self.grade is None or self.extra_semester is None:
                raise ValueError("이수학기를 입력하세요")
            self.completed_semesters = semesters_from_grade(self.grade, self.extra_semester)
        if self.is_transfer and self.completed_semesters > MAX_TRANSFER_SEMESTERS:
            raise ValueError(f"편입 후 마친 학기는 0~{MAX_TRANSFER_SEMESTERS} 사이로 입력하세요")
        # 신입생 첫 학기는 성적이 없습니다. 편입 첫 학기는 전 학교 성적을 받습니다(4.1).
        if self.completed_semesters == 0 and not self.is_transfer:
            self.gpa = self.credits = self.gpa_total = None
        else:
            for value, label in _grade_fields(self):
                if value is None:
                    raise ValueError(f"{label} 입력하세요")
        self.score_100 = score_100_from_gpa(self.gpa)
        self.score_100_total = score_100_from_gpa(self.gpa_total)
        if self.enrollment_status is None:
            self.enrollment_status = "재학"
        if self.full_tuition_waiver == "모름":
            self.full_tuition_waiver = None
        return self
