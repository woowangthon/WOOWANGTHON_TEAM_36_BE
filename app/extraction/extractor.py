"""AI 추출 함수 (계획서 3장 AI 행, 5.2, 8.1, 9.3).

공개 함수는 extract(request) 하나입니다. 공지 텍스트(ExtractRequest) 외의 인자를 받지 않습니다.
추출은 수집 작업(scheduler/jobs.py)에서만 부릅니다. 학생 요청 경로에서는 부르지 않습니다.

현재(T04)는 뼈대입니다.
- AI_MOCK=1이면 고정된 목 응답을 돌려줍니다(T17이 T15 없이 진행할 수 있게).
- 그 밖에는 NotImplementedError를 냅니다. Gemini 호출·재시도·시간 제한은 T15에서 채웁니다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.extraction.schemas import ExtractedCriteria, ExtractionResult, ExtractRequest

PROMPT_VERSION = "extract_v1"
MOCK_MODEL_NAME = "mock"

_SEOUL = ZoneInfo("Asia/Seoul")


class ExtractionError(Exception):
    """추출 실패.

    수집 작업은 이 예외를 받으면 extract_status = FAILED로 기록하고 다음 공지를 처리합니다.

    메시지에 공지 원문이나 비밀값을 넣지 않습니다.
    """


def extract(request: ExtractRequest) -> ExtractionResult:
    if get_settings().ai_mock:
        return _mock_result()
    raise NotImplementedError("Gemini 추출은 T15에서 구현합니다. 로컬에서는 AI_MOCK=1을 쓰세요.")


def _mock_result() -> ExtractionResult:
    """계획서 5.2 표의 예시 값으로 만든 고정 응답입니다.

    요청 내용과 관계없이 같은 값을 돌려줍니다.
    """
    criteria = ExtractedCriteria.model_validate(
        {
            "notice_kind": "모집",
            "scholarships": [
                {
                    "scholarship_name": "두산연강재단 장학금",
                    "benefit_amount_text": "연 600만원",
                    "benefit_type": "등록금성",
                    "apply_start": datetime(2026, 10, 30, 9, 0, tzinfo=_SEOUL),
                    "apply_end": datetime(2026, 11, 12, 18, 0, tzinfo=_SEOUL),
                    "target_grades": [2, 3],
                    "excludes_extra_semester": False,
                    "enrollment_status": ["재학"],
                    "min_gpa": 3.5,
                    "min_credits": 12,
                    "gender": "여",
                    "max_age": 26,
                    "max_income_bracket": 8,
                    "regions": ["충청남도 논산시"],
                    "region_basis": "부모",
                    "required_special": ["다자녀"],
                    "excludes_full_tuition": True,
                    "target_department": "제한없음",
                    "target_program": "제한없음",
                    "required_documents": ["재학증명서", "성적증명서"],
                    "extra_conditions": ["직전학기 봉사활동 20시간 이상"],
                    "ambiguous_fields": [],
                    "evidence": {"min_gpa": "직전학기 평점평균 3.5 이상인 자"},
                }
            ],
        }
    )
    return ExtractionResult(criteria=criteria, model_version=f"{MOCK_MODEL_NAME}+{PROMPT_VERSION}")
