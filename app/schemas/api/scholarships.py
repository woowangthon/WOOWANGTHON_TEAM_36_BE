"""장학금 API DTO (계획서 5.3 판정 결과, 7.1 화면, 7.2, 9.8 화면 하나에 API 한 번).

목록 응답에 판정 결과와 내 상태를 함께 담아 대시보드가 API를 한 번만 부르게 합니다.
"""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from app.extraction.schemas import BenefitType, SourceCode

MatchResult = Literal["ELIGIBLE", "CONDITIONAL", "INELIGIBLE"]  # 지원 가능 / 확인 필요 / 지원 불가
ConditionOutcome = Literal["MET", "UNMET", "UNKNOWN"]  # 충족 / 불충족 / 알 수 없음
ScholarshipStatus = Literal["OPEN", "ALWAYS", "CLOSED"]

# 5.3 표의 조건 행. 이름은 ScholarshipCriteria 필드 이름을 씁니다.
ConditionName = Literal[
    "target_grades",
    "excludes_extra_semester",
    "excludes_new_students",  # 신·편입생 제외 (extract_v6)
    "excludes_transfer_students",  # 편입생 제외 (extract_v7)
    "min_admission_year",  # 입학 학년도 (extract_v11)
    "min_remaining_semesters",  # 남은 정규학기 N학기 이상 (extract_v6)
    "min_completed_semesters",  # 이수 학기 범위 (extract_v15). max_completed_semesters와 한 행
    "enrollment_status",
    "school_excluded",  # 학교 조건 (extract_v8)
    "min_gpa",
    "min_score_100",
    "min_credits",
    "target_nationality",  # 국적 (extract_v7)
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


ConditionAnswer = Literal[
    "YES", "NO"
]  # 맞아요(충족) / 안 맞아요(불충족) (5.3 자격을 좁히는 조건 직접 답하기)


class OpenCondition(BaseModel):
    """아직 답하지 않은 자격을 좁히는 조건 1줄. "이것만 확인하면 N개" 안내에 씁니다(7.1)."""

    key: str  # condition_key: 문장 정규화 후 SHA-256 앞 16자 (5.3)
    text: str
    summary: str | None = None  # 쉬운 요약. 없으면 화면이 text를 그대로 보입니다


class ExtraCheck(BaseModel):
    """자격을 좁히는 조건 1줄과 사용자 답. 화면에서 ?(null) ✓(YES) ✗(NO)로 표시합니다(5.3)."""

    key: str
    text: str
    answer: ConditionAnswer | None
    summary: str | None = None  # 쉬운 요약. 없으면 화면이 text를 그대로 보입니다


class ScholarshipSummary(BaseModel):
    """대시보드 카드 1장 (장학금 단위, 7.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str  # 공지에 적힌 원래 이름
    display_name: str  # 화면에 보일 쉬운 이름(5.2, extract_v9). 추출값이 없으면 name과 같음
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
    # 관심과 신청완료는 따로 켜고 끕니다(7.1, 2026-11-14)
    bookmarked: bool
    applied: bool
    # 답하지 않은 자격을 좁히는 조건과, 그 밖에 판정을 막는 것(알 수 없는 조건·못 읽은 첨부)이
    # 있는지. "이것만 확인하면 N개가 풀려요" 안내를 화면이 계산합니다(7.1).
    open_conditions: list[OpenCondition] = []
    other_unknown: bool = False
    # 같은 장학금(이름·마감 시각이 같음)이 올라온 다른 게시판 이름. 기본 목록에서만 채웁니다(T45).
    also_posted_in: list[str] = []


class ScholarshipListResponse(BaseModel):
    items: list[ScholarshipSummary]


class ConditionCheck(BaseModel):
    """조건 1개의 판정. 화면에서 ✓(MET) ✗(UNMET) ?(UNKNOWN)로 표시합니다(5.3)."""

    condition: ConditionName
    outcome: ConditionOutcome
    evidence: str | None  # 공지 원문 근거 문장 (5.2 evidence)
    # 근거가 나온 곳: "본문" 또는 첨부 파일 이름. extract_v4 이전 결과는 null
    evidence_source: str | None = None
    # 코드가 값으로 만든 조건 요약("직전학기 3.5 이상"). 모호하거나 이름만으로 충분한 조건은 null
    requirement: str | None = None
    ambiguous: bool = (
        False  # ambiguous_fields에 든 조건. 화면이 "공지 기준이 명확하지 않아요"로 보입니다
    )


class NoticeAttachmentText(BaseModel):
    """읽은 첨부(OK)의 텍스트. 상세 화면의 "원문 보기"에서 본문 아래에 펼쳐 보여 줍니다."""

    name: str  # ZIP 안의 파일은 "ZIP 이름 / 파일 이름". evidence_source와 같은 이름
    url: str  # 첨부(ZIP이면 ZIP) 다운로드 주소
    text: str  # 첨부에서 읽은 평문. 표는 한 행을 한 줄로, 칸은 " | "로 구분


class NoticeRef(BaseModel):
    id: int
    title: str
    url: str  # 원문 링크
    posted_at: AwareDatetime
    # 수집 때 저장한 공지 본문 평문(줄바꿈 유지, 표는 " | " 구분).
    # HTML이 아니므로 화면에서 그대로 텍스트로 표시합니다.
    body_text: str
    # 읽은 첨부만 position 순서로. 못 읽은 첨부는 ScholarshipDetail.unread_attachments에 있습니다.
    attachments: list[NoticeAttachmentText]


class UnreadAttachment(BaseModel):
    """읽지 못한 첨부. 화면에서 원문 링크와 함께 "직접 확인"으로 보여 줍니다(D8)."""

    name: str  # ZIP 안의 파일은 "ZIP 이름 / 파일 이름"
    url: str  # 첨부(ZIP이면 ZIP) 다운로드 주소
    reason: Literal["NO_TEXT", "UNSUPPORTED", "FAILED", "TOO_LARGE"]  # 스캔본/형식/실패/크기


class SiblingScholarship(BaseModel):
    """같은 공지의 다른 장학금 링크 (7.1 상세)."""

    id: int
    name: str
    display_name: str  # ScholarshipSummary.display_name과 같음
    status: ScholarshipStatus


class ConditionTextOut(BaseModel):
    """기본 조건 1줄: 공지 원문 문장과 쉬운 요약."""

    text: str
    summary: str | None = None


class ScholarshipDetail(ScholarshipSummary):
    apply_start: AwareDatetime | None
    conditions: list[ConditionCheck]
    extra_conditions: list[str]  # 자격을 좁히는 조건 (답하지 않은 것이 있으면 확인 필요)
    extra_checks: list[ExtraCheck]  # 위 조건마다 사용자 답 (5.3)
    basic_conditions: list[str]  # 대부분 충족하는 기본 조건 (판정에 영향 없음, 지원 전 확인)
    # 위와 같은 조건의 {text, summary}. 화면은 summary가 있으면 요약을, 없으면 text를 보입니다.
    basic_items: list[ConditionTextOut] = []
    notes: list[str]  # 자격이 아닌 안내 (선발 방식·의무·예외 등, 판정에 영향 없음)
    required_documents: list[str]
    notice: NoticeRef
    siblings: list[SiblingScholarship]
    # 첨부를 못 읽었으면 판정이 "지원 가능"이 아니라 "확인 필요"입니다(D8).
    unread_attachments: list[UnreadAttachment]
    # 추출 입력이 10만 자를 넘어 뒤쪽 첨부 일부를 읽지 못함. 판정은 바꾸지 않습니다(D5).
    input_truncated: bool


class StateRequest(BaseModel):
    """보낸 값만 바꿉니다. 빼거나 null로 보낸 칸은 그대로 둡니다."""

    model_config = ConfigDict(extra="forbid")

    bookmarked: bool | None = None
    applied: bool | None = None

    @model_validator(mode="after")
    def _needs_a_change(self) -> Self:
        if self.bookmarked is None and self.applied is None:
            raise ValueError("bookmarked와 applied 중 하나는 보내야 합니다")
        return self


class StateResponse(BaseModel):
    scholarship_id: int
    bookmarked: bool
    applied: bool


class ConditionAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: ConditionAnswer


class ConditionAnswerResponse(BaseModel):
    scholarship_id: int
    key: str
    answer: ConditionAnswer | None  # DELETE 뒤에는 null
    result: MatchResult  # 답을 반영한 판정
