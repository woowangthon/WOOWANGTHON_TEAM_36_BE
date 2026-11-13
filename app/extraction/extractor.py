"""AI 추출 함수 (계획서 3장 AI 행, 5.2, 8.1, 9.3).

공개 함수는 extract(request) 하나입니다. 공지 텍스트(ExtractRequest) 외의 인자를 받지 않습니다.
추출은 수집 작업(scheduler/jobs.py)에서만 부릅니다. 학생 요청 경로에서는 부르지 않습니다.

- AI_MOCK=1이면 고정된 목 응답을 돌려줍니다(Gemini 키·모델 이름 없이 로컬 개발).
- 모델 이름은 GEMINI_MODEL에서만 읽습니다. 코드에 기본값이 없고, 없으면 ExtractionError입니다.
- Structured Output(`response_json_schema`)으로 ExtractedCriteria 형태를 강제하고,
  응답을 Pydantic으로 다시 검증합니다. 검증 실패는 재시도하지 않습니다(9.3).
- 429·5xx·호출 시간 초과는 지수 백오프로 최대 3회 재시도합니다. 호출 1회 90초, 공지 1건의 재시도를
  포함한 전체 처리는 150초를 넘지 않습니다.
- 추론 강도(thinking level)는 LOW로 둡니다. 모델 기본값(HIGH)은 공지 1건에 60~85초가 걸려
  시간 제한에 걸렸고, LOW는 같은 공지를 13~22초에 같은 정답으로 처리했습니다(T47).
- 입력 텍스트는 10만 자에서 자릅니다(D5). 수집 작업이 본문 → 공고문 첨부 순으로 붙이고
  신청서·서식 같은 양식 파일은 넣지 않으므로, 넘쳐도 잘리는 쪽은 뒤쪽 첨부입니다.
- google-genai는 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
"""

import json
import logging
import time
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import log_event
from app.extraction.schemas import ExtractedCriteria, ExtractionResult, ExtractRequest

PROMPT_VERSION = "extract_v12"
MOCK_MODEL_NAME = "mock"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"

MAX_INPUT_CHARS = 100_000  # 약 120쪽. 넘치면 뒤쪽 첨부부터 잘림(D5)
CALL_TIMEOUT_SECONDS = 90.0
TOTAL_TIMEOUT_SECONDS = 150.0
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0  # 2, 4, 8초
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_SEOUL = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)

# 테스트에서 가짜 시계로 바꿀 수 있게 모듈 변수로 둡니다.
_monotonic = time.monotonic
_sleep = time.sleep


class ExtractionError(Exception):
    """추출 실패.

    수집 작업은 이 예외를 받으면 extract_status = FAILED로 기록하고 다음 공지를 처리합니다.

    메시지에 공지 원문이나 비밀값을 넣지 않습니다.
    `counts_as_attempt`가 거짓이면 공지 탓이 아닌 실패(선불 크레딧 소진 402)라
    수집 작업이 재시도 횟수(`extract_attempts`, 9.3)에 세지 않습니다.
    """

    def __init__(self, message: str, *, counts_as_attempt: bool = True) -> None:
        super().__init__(message)
        self.counts_as_attempt = counts_as_attempt


class _RetryableError(Exception):
    """재시도할 수 있는 호출 실패(429·5xx·시간 초과)."""


def extract(request: ExtractRequest) -> ExtractionResult:
    settings = get_settings()
    if settings.ai_mock:
        return _mock_result()
    if not settings.gemini_model:
        raise ExtractionError("GEMINI_MODEL이 설정되지 않았습니다")
    if settings.gemini_api_key is None:
        raise ExtractionError("GEMINI_API_KEY가 설정되지 않았습니다")

    model = settings.gemini_model
    client = _new_client(settings.gemini_api_key.get_secret_value())
    raw = _generate_with_retry(client, model, _user_content(request))
    criteria = _validate(raw)
    return ExtractionResult(criteria=criteria, model_version=f"{model}+{PROMPT_VERSION}")


def _new_client(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)


@cache
def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _user_content(request: ExtractRequest) -> str:
    """Gemini에 보내는 내용: 공지 텍스트만 넣습니다(8.1)."""
    text = request.text[:MAX_INPUT_CHARS]
    posted_at = request.posted_at.astimezone(_SEOUL).isoformat()
    return (
        f"출처: {request.source_code}\n제목: {request.title}\n게시 시각: {posted_at}\n---\n{text}"
    )


def _generate_with_retry(client: Any, model: str, content: str) -> str:
    deadline = _monotonic() + TOTAL_TIMEOUT_SECONDS
    for attempt in range(MAX_RETRIES + 1):
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise ExtractionError(f"추출 전체 시간({TOTAL_TIMEOUT_SECONDS:.0f}초)을 넘었습니다")
        try:
            return _generate(client, model, content, timeout=min(CALL_TIMEOUT_SECONDS, remaining))
        except _RetryableError as error:
            if attempt == MAX_RETRIES:
                raise ExtractionError(f"재시도를 다 썼습니다({error})") from error
            backoff = BACKOFF_BASE_SECONDS * 2**attempt
            if _monotonic() + backoff >= deadline:
                raise ExtractionError(
                    f"추출 전체 시간({TOTAL_TIMEOUT_SECONDS:.0f}초) 안에 재시도할 수 없습니다"
                ) from error
            log_event(
                logger,
                "Gemini 호출 재시도",
                logging.WARNING,
                attempt=attempt + 1,
                reason=str(error),
                backoff_seconds=backoff,
            )
            _sleep(backoff)
    raise AssertionError("unreachable")  # pragma: no cover


def _generate(client: Any, model: str, content: str, *, timeout: float) -> str:
    """Gemini 호출 1회. 재시도할 수 있는 실패는 _RetryableError로 바꿉니다."""
    import httpx
    from google.genai import errors, types

    config = types.GenerateContentConfig(
        system_instruction=_system_prompt(),
        response_mime_type="application/json",
        response_json_schema=ExtractedCriteria.model_json_schema(),
        temperature=0,
        # 생각 수준을 정하지 않으면 모델 기본값(높음)으로 오래 생각해 공지 1건에 60~85초가
        # 걸립니다(T47 측정). 추출은 원문의 값을 옮겨 적는 일이라 낮음으로 충분합니다.
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        http_options=types.HttpOptions(timeout=int(timeout * 1000)),  # 밀리초
    )
    try:
        response = client.models.generate_content(model=model, contents=content, config=config)
    except errors.APIError as error:
        if error.code in RETRYABLE_STATUS:
            raise _RetryableError(f"HTTP {error.code}") from error
        # 402(선불 크레딧 소진, 9.1)·400·403 등은 재시도해도 같은 결과.
        # 402는 공지 탓이 아니므로 공지의 재시도 횟수에 세지 않습니다(9.3).
        raise ExtractionError(
            f"Gemini 호출 실패(HTTP {error.code})", counts_as_attempt=error.code != 402
        ) from error
    except (httpx.TimeoutException, TimeoutError) as error:
        raise _RetryableError("호출 시간 초과") from error

    text = getattr(response, "text", None)
    if not text:
        raise ExtractionError("Gemini 응답이 비었습니다")
    return text


def _validate(raw: str) -> ExtractedCriteria:
    try:
        return ExtractedCriteria.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        # 오류 메시지에 원문 값이 들어갈 수 있어 오류 종류와 개수만 남깁니다.
        count = error.error_count() if isinstance(error, ValidationError) else 1
        raise ExtractionError(f"응답 검증 실패({type(error).__name__}, {count}건)") from error


def _mock_result() -> ExtractionResult:
    """계획서 5.2 표의 예시 값으로 만든 고정 응답입니다.

    요청 내용과 관계없이 같은 값을 돌려줍니다.
    """
    criteria = ExtractedCriteria.model_validate(
        {
            "notice_kind": "모집",
            "scholarships": [
                {
                    "scholarship_name": "2026학년도 2학기 (재)두산연강재단 장학생 선발",
                    "display_name": "두산연강재단 장학금",
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
                    "extra_conditions": [
                        {
                            "text": "직전학기 봉사활동 20시간 이상",
                            "summary": "직전학기 봉사활동 20시간 이상",
                        }
                    ],
                    "ambiguous_fields": [],
                    "evidence": {"min_gpa": "직전학기 평점평균 3.5 이상인 자"},
                }
            ],
        }
    )
    return ExtractionResult(criteria=criteria, model_version=f"{MOCK_MODEL_NAME}+{PROMPT_VERSION}")
