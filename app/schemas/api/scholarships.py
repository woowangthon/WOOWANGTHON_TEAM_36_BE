"""장학금 API DTO (계획서 5.3 판정 결과, 7.1 화면, 7.2, 9.8 화면 하나에 API 한 번).

목록 응답에 판정 결과와 내 상태를 함께 담아 대시보드가 API를 한 번만 부르게 합니다.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.extraction.schemas import BenefitType, SourceCode

MatchResult = Literal["ELIGIBLE", "CONDITIONAL", "INELIGIBLE"]  # 지원 가능 / 확인 필요 / 지원 불가
ConditionOutcome = Literal["MET", "UNMET", "UNKNOWN"]  # 충족 / 불충족 / 알 수 없음
ScholarshipStatus = Literal["OPEN", "ALWAYS", "CLOSED"]
UserState = Literal["BOOKMARKED", "APPLIED", "HIDDEN"]

# 5.3 표의 조건 행. 이름은 ScholarshipCriteria 필드 이름을 씁니다.
ConditionName = Literal[
    "target_grades",
    "excludes_extra_semester",
    "enrollment_status",
    "min_gpa",
    "min_credits",
    "gender",
    "max_age",
    "max_income_bracket",
    "regions",
    "required_special",
    "excludes_full_tuition",
    "target_department",
    "target_program",
]

# 목록 쿼리 값 (7.1 탭·정렬·필터)
ResultFilter = Literal["eligible", "conditional", "all"]  # 탭: 지원 가능 / 확인 필요 / 전체
SortOrder = Literal["deadline", "latest"]  # 마감 임박순 / 최신순
SourceFilter = Literal["internal", "external"]  # 교내 / 교외
StateFilter = Literal["bookmarked", "applied"]  # 내 장학금 화면 (마감된 장학금 포함)


class ScholarshipSummary(BaseModel):
    """대시보드 카드 1장 (장학금 단위, 7.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_code: SourceCode
    source_name: str
    is_internal: bool
    benefit_amount_text: str | None
    benefit_type: BenefitType | None
    apply_end: AwareDatetime | None  # D-Day 기준. ALWAYS면 "상시·확인 필요"
    status: ScholarshipStatus
    deadline_conflict: bool  # true면 "마감일 확인 필요"
    posted_at: AwareDatetime
    result: MatchResult
    my_state: UserState | None


class ScholarshipListResponse(BaseModel):
    items: list[ScholarshipSummary]


class ConditionCheck(BaseModel):
    """조건 1개의 판정. 화면에서 ✓(MET) ✗(UNMET) ?(UNKNOWN)로 표시합니다(5.3)."""

    condition: ConditionName
    outcome: ConditionOutcome
    evidence: str | None  # 공지 원문 근거 문장 (5.2 evidence)


class NoticeRef(BaseModel):
    id: int
    title: str
    url: str  # 원문 링크
    posted_at: AwareDatetime


class SiblingScholarship(BaseModel):
    """같은 공지의 다른 장학금 링크 (7.1 상세)."""

    id: int
    name: str
    status: ScholarshipStatus


class ScholarshipDetail(ScholarshipSummary):
    apply_start: AwareDatetime | None
    conditions: list[ConditionCheck]
    extra_conditions: list[str]  # 정리하지 못한 조건 (있으면 확인 필요)
    required_documents: list[str]
    notice: NoticeRef
    siblings: list[SiblingScholarship]


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: UserState


class StateResponse(BaseModel):
    scholarship_id: int
    state: UserState | None  # DELETE 뒤에는 null
