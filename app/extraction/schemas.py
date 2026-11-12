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
Nationality = Literal["domestic", "foreign"]  # 내국인 대상 / 외국인 대상 (extract_v7)
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
    "excludes_new_students",
    "excludes_transfer_students",
    "min_remaining_semesters",
    "enrollment_status",
    "min_gpa",
    "min_score_100",
    "grade_basis",
    "min_credits",
    "target_nationality",
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
    # 본문 + 첨부마다 `[첨부 {형식}: 파일 이름]` + 텍스트. 10만 자 자르기는 extractor가 합니다(D5).
    text: str


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
    # extract_v6에서 추가했습니다. 기본값이 None이라 DB에 저장된 v5 결과도 그대로 읽힙니다.
    # 신입생·편입생 제외면 true. 공지에 조건이 없으면 null(false도 쓰지 않음)
    excludes_new_students: bool | None = None
    # extract_v7에서 추가했습니다. 기본값이 None이라 DB에 저장된 v6 결과도 그대로 읽힙니다.
    # 편입생만 제외하면 true("신·편입생 제외"는 excludes_new_students). 조건이 없으면 null
    excludes_transfer_students: bool | None = None
    # 남은 정규학기가 N학기 이상이어야 함(예: "잔여정규학기 1개 학기 이상" → 1)
    min_remaining_semesters: int | None = Field(default=None, ge=1, le=8)
    enrollment_status: list[EnrollmentStatus] | None = None

    # 성적 조건 (5.2): 4.5 만점 평점과 100점 만점 점수는 서로 환산하지 않고 따로 둡니다.
    # min_score_100·grade_basis는 extract_v3에서 추가했습니다.
    # 기본값이 None이라 DB에 저장된 v2 결과도 그대로 읽힙니다.
    min_gpa: float | None = Field(default=None, ge=0, le=4.5)
    min_score_100: float | None = Field(default=None, ge=0, le=100)
    grade_basis: GradeBasis | None = None
    min_credits: int | None = Field(default=None, ge=0)

    gender: Gender | None = None
    # 국적 조건 (extract_v7). domestic = 대한민국 국적자 대상, foreign = 외국인 대상, null = 없음
    target_nationality: Nationality | None = None
    max_age: int | None = Field(default=None, ge=0, le=100)  # 만 나이

    max_income_bracket: float | None = None

    regions: list[str] | None = None  # 예: ["충청남도 논산시"]
    region_basis: RegionBasis | None = None

    required_special: list[str] | None = None  # 공지 표현 그대로 (예: "다자녀", "한부모")
    excludes_full_tuition: bool | None = None

    target_department: TargetDepartment = "불명"
    target_program: TargetProgram = "제한없음"

    required_documents: list[str] = Field(default_factory=list)
    # 필드로 옮기지 못한 문장은 판정 영향에 따라 세 갈래로 나눕니다(extract_v5, v6에서 기준 수정).
    # - extra_conditions: 프로필로 판정할 수 없는 자격 → 확인 필요.
    #   프로그램 수료, 외부 기준 참조, 수혜·선발 이력, 필드와 섞인 문장 등.
    #   v6부터 중복 수혜·기존 장학생 제외·관리지침 참조도 여기입니다.
    # - basic_conditions: 학교 정보로 알 수 있거나 재학생 거의 모두가 충족하는 조건
    #   (4년제·지방 대학 재학, 징계 없음 등) → 판정은 그대로, 상세에 "지원 전 확인".
    #   국적은 v7부터 target_nationality로 판정합니다.
    # - notes: 자격이 아닌 안내(선발 방식, 선발 뒤 의무, 그 장학금의 신청 절차, 예외 규정 등)
    # 기본값이 빈 목록이라 DB에 저장된 v4 결과도 그대로 읽힙니다(v4는 셋 모두 extra_conditions).
    extra_conditions: list[str] = Field(default_factory=list)
    basic_conditions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    ambiguous_fields: list[CriteriaField] = Field(default_factory=list)
    evidence: dict[CriteriaField, str] = Field(default_factory=dict)  # 필드별 근거 원문 문장
    # 근거 문장이 나온 곳: "본문" 또는 입력의 `[첨부 …: 파일 이름]`에 적힌 파일 이름(extract_v4).
    # 기본값이 빈 dict라 DB에 저장된 v3 결과도 그대로 읽힙니다.
    evidence_source: dict[CriteriaField, str] = Field(default_factory=dict)

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
