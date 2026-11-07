"""AI 추출 스키마 (계획서 5.2, 8.1, 8.3).

이 파일이 ExtractRequest·ExtractedCriteria·ScholarshipCriteria의 유일한 정의입니다(사본 없음).
필드를 바꾸면 계획서 5.2 표와 프롬프트 버전(extract_vN.md)을 함께 올립니다.

개인정보 경계(8.1): 이 모듈은 공지 텍스트만 다룹니다. 사용자·프로필 관련 모듈을 import하지 않습니다.

null 규칙(5.2): 조건 필드의 None은 "조건 없음"입니다. 공지에 조건은 있지만 값을 확정할 수 없으면
None으로 두고 필드 이름을 ambiguous_fields에 넣습니다.
"""

from typing import Literal, Self, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SourceCode = Literal["SW_DEPT", "AIBC", "KOSAF"]
NoticeKind = Literal["모집", "결과", "기준안내", "기타"]
BenefitType = Literal["등록금성", "생활비성", "혼합"]
EnrollmentStatus = Literal["재학", "휴학", "수료", "졸업유예"]  # 계획서 4.1 재학상태 선택지
Gender = Literal["남", "여"]
RegionBasis = Literal["본인", "부모", "무관"]
TargetDepartment = Literal["제한없음", "소프트웨어학부 포함", "소프트웨어학부 불포함", "불명"]
TargetProgram = Literal["제한없음", "AIBC 양성인재"]
GradeBasis = Literal["직전학기", "전체"]  # 성적 조건이 어느 학기 성적 기준인지 (5.2)

# 기초생활수급자 = 0, 차상위 = 0.5, 학자금지원구간 1~10 = 1~10 (5.2)
INCOME_BRACKETS: frozenset[float] = frozenset({0.0, 0.5, *(float(n) for n in range(1, 11))})

# ambiguous_fields·evidence의 키로 쓸 수 있는 필드 이름 (장학금 단위 필드)
CriteriaField = Literal[
    "scholarship_name",
    "benefit_amount_text",
    "benefit_type",
    "apply_start",
    "apply_end",
    "target_grades",
    "excludes_extra_semester",
    "enrollment_status",
    "min_gpa",
    "min_score_100",
    "grade_basis",
    "min_credits",
    "gender",
    "max_age",
    "max_income_bracket",
    "regions",
    "region_basis",
    "required_special",
    "excludes_full_tuition",
    "target_department",
    "target_program",
    "required_documents",
]

_STRICT = ConfigDict(extra="forbid")


class ExtractRequest(BaseModel):
    """추출 입력: 공지 텍스트만 받습니다(8.1). 사용자 정보를 넣을 필드를 만들지 않습니다."""

    model_config = _STRICT

    source_code: SourceCode
    title: str = Field(min_length=1)
    posted_at: AwareDatetime  # 한국 시간(+09:00)으로 해석한 게시 시각
    text: str  # 본문 + PDF 첨부 텍스트. 3만 자 자르기는 extractor가 합니다(3장 AI 행).


class ScholarshipCriteria(BaseModel):
    """장학금 1개의 조건 (5.2 표). 판정·카드·관심 표시는 이 단위로 합니다."""

    model_config = _STRICT

    scholarship_name: str = Field(min_length=1)
    benefit_amount_text: str | None = None
    benefit_type: BenefitType | None = None

    # 한국 시간(+09:00). 시각이 없으면 23:59로 채웁니다(2장 날짜 해석).
    apply_start: AwareDatetime | None = None
    apply_end: AwareDatetime | None = None

    target_grades: list[int] | None = None
    excludes_extra_semester: bool | None = None
    enrollment_status: list[EnrollmentStatus] | None = None

    # 성적 조건 (5.2): 4.5 만점 평점과 100점 만점 점수는 서로 환산하지 않고 따로 둡니다.
    # min_score_100·grade_basis는 extract_v3에서 추가했습니다.
    # 기본값이 None이라 DB에 저장된 v2 결과도 그대로 읽힙니다.
    min_gpa: float | None = Field(default=None, ge=0, le=4.5)
    min_score_100: float | None = Field(default=None, ge=0, le=100)
    grade_basis: GradeBasis | None = None
    min_credits: int | None = Field(default=None, ge=0)

    gender: Gender | None = None
    max_age: int | None = Field(default=None, ge=0, le=100)  # 만 나이

    max_income_bracket: float | None = None

    regions: list[str] | None = None  # 예: ["충청남도 논산시"]
    region_basis: RegionBasis | None = None

    required_special: list[str] | None = None  # 공지 표현 그대로 (예: "다자녀", "한부모")
    excludes_full_tuition: bool | None = None

    target_department: TargetDepartment = "불명"
    target_program: TargetProgram = "제한없음"

    required_documents: list[str] = Field(default_factory=list)
    extra_conditions: list[str] = Field(default_factory=list)  # 정리하지 못한 조건 문장 그대로
    ambiguous_fields: list[CriteriaField] = Field(default_factory=list)
    evidence: dict[CriteriaField, str] = Field(default_factory=dict)  # 필드별 근거 원문 문장

    @model_validator(mode="after")
    def _check_values(self) -> Self:
        if self.target_grades is not None:
            if not self.target_grades or any(g not in range(1, 5) for g in self.target_grades):
                raise ValueError("target_grades는 1~4 중 하나 이상이어야 합니다")
        if self.enrollment_status is not None and not self.enrollment_status:
            raise ValueError("enrollment_status 조건이 없으면 빈 목록 대신 null이어야 합니다")
        if self.max_income_bracket is not None and self.max_income_bracket not in INCOME_BRACKETS:
            raise ValueError("max_income_bracket은 0, 0.5, 1~10 중 하나여야 합니다")
        if self.regions is not None and not self.regions:
            raise ValueError("regions 조건이 없으면 빈 목록 대신 null이어야 합니다")
        if self.required_special is not None and not self.required_special:
            raise ValueError("required_special 조건이 없으면 빈 목록 대신 null이어야 합니다")
        return self


class ExtractedCriteria(BaseModel):
    """공지 1건의 추출 결과: 공지 종류 1개 + 장학금 목록 (5.2)."""

    model_config = _STRICT

    notice_kind: NoticeKind
    scholarships: list[ScholarshipCriteria] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.notice_kind in ("결과", "기타") and self.scholarships:
            raise ValueError("notice_kind가 결과·기타면 scholarships는 빈 목록이어야 합니다")
        return self


class ExtractionResult(BaseModel):
    """extract()의 반환값. model_version = "{GEMINI_MODEL}+{프롬프트 버전}" (backend CLAUDE.md)."""

    model_config = _STRICT

    criteria: ExtractedCriteria
    model_version: str = Field(min_length=1)


CRITERIA_FIELDS: tuple[str, ...] = get_args(CriteriaField)
