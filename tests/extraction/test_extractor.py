"""AI 추출 모듈 테스트 (T15). Gemini를 실제로 호출하지 않고 가짜 클라이언트를 씁니다."""

import ast
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from google.genai import errors

from app.extraction import extractor
from app.extraction.extractor import (
    MAX_INPUT_CHARS,
    PROMPT_PATH,
    ExtractionError,
    extract,
)
from app.extraction.schemas import ExtractRequest

SEOUL = ZoneInfo("Asia/Seoul")
EXTRACTION_DIR = Path(extractor.__file__).parent

VALID_RESPONSE = {
    "notice_kind": "모집",
    "scholarships": [
        {
            "scholarship_name": "푸른등대 기부장학금",
            "apply_end": "2026-11-12T18:00:00+09:00",
            "min_gpa": 3.0,
            "target_department": "제한없음",
            "ambiguous_fields": ["max_income_bracket"],
            "evidence": {"min_gpa": "직전학기 평점 3.0 이상"},
        }
    ],
}


def request(text: str = "공지 본문") -> ExtractRequest:
    return ExtractRequest(
        source_code="SW_DEPT",
        title="푸른등대 기부장학금 선발 안내 ( ~11.12 18:00)",
        posted_at=datetime(2026, 10, 22, 15, 33, tzinfo=SEOUL),
        text=text,
    )


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    """generate_content 호출마다 outcomes를 순서대로 씁니다(예외면 던지고, 문자열이면 응답)."""

    def __init__(self, outcomes: list, clock: "FakeClock", call_seconds: float) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.clock = clock
        self.call_seconds = call_seconds

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        self.clock.now += self.call_seconds
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(extractor, "_monotonic", fake.monotonic)
    monkeypatch.setattr(extractor, "_sleep", fake.sleep)
    return fake


@pytest.fixture
def gemini(monkeypatch, clock):
    """가짜 Gemini를 설치합니다. gemini(outcomes, call_seconds=1.0) → FakeModels."""
    monkeypatch.setenv("AI_MOCK", "0")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def install(outcomes: list, call_seconds: float = 1.0) -> FakeModels:
        models = FakeModels(outcomes, clock, call_seconds)
        monkeypatch.setattr(extractor, "_new_client", lambda api_key: FakeClient(models))
        return models

    return install


def server_error(code: int) -> errors.APIError:
    error_class = errors.ServerError if code >= 500 else errors.ClientError
    return error_class(code, {"error": {"code": code, "message": "fake", "status": "FAKE"}})


# --- 정상 ---


def test_success(gemini, clock):
    models = gemini([json.dumps(VALID_RESPONSE)])
    result = extract(request())
    assert result.model_version == "gemini-test-flash+extract_v5"
    scholarship = result.criteria.scholarships[0]
    assert scholarship.apply_end == datetime(2026, 11, 12, 18, 0, tzinfo=SEOUL)
    assert scholarship.ambiguous_fields == ["max_income_bracket"]
    assert len(models.calls) == 1
    assert clock.sleeps == []


def test_request_uses_structured_output_and_prompt(gemini):
    models = gemini([json.dumps(VALID_RESPONSE)])
    extract(request())
    call = models.calls[0]
    config = call["config"]
    assert call["model"] == "gemini-test-flash"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["title"] == "ExtractedCriteria"
    assert config.system_instruction == PROMPT_PATH.read_text(encoding="utf-8")
    assert config.temperature == 0
    assert config.http_options.timeout == 60_000  # 호출 1회 60초


def test_request_contains_only_notice_fields(gemini):
    models = gemini([json.dumps(VALID_RESPONSE)])
    extract(request("본문 내용"))
    contents = models.calls[0]["contents"]
    assert contents.startswith("출처: SW_DEPT\n제목: 푸른등대 기부장학금 선발 안내")
    assert "게시 시각: 2026-10-22T15:33:00+09:00" in contents
    assert contents.endswith("---\n본문 내용")


def test_input_is_truncated_to_30000_chars(gemini):
    models = gemini([json.dumps(VALID_RESPONSE)])
    extract(request("가" * (MAX_INPUT_CHARS + 5000)))
    body = models.calls[0]["contents"].split("---\n", 1)[1]
    assert len(body) == MAX_INPUT_CHARS


def test_prompt_contains_required_rules():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for rule in (
        '`null`은 "조건 없음"',
        "ambiguous_fields",
        "나눈다",
        "글자 그대로",
        "정해진 값만",
        "evidence_source",  # 근거 출처(extract_v4)
        "**첨부 공고문**을 따른다",  # D4: 자격 조건은 첨부
        "**본문**을 따른다",  # D4: 신청 기간은 본문(학과 마감)
        "`basic_conditions`",  # 기타 조건 세 갈래(extract_v5)
        "`notes`",
    ):
        assert rule in prompt, rule


def test_input_limit_is_100k_chars():
    """D5: 3만 자면 공고문 첨부 하나(KOSAF 업무처리기준 2.8만 자)로도 넘치는 공지가 있습니다."""
    assert MAX_INPUT_CHARS == 100_000


# --- 재시도 ---


@pytest.mark.parametrize("code", [429, 500, 503])
def test_retries_retryable_errors_with_backoff(gemini, clock, code):
    models = gemini([server_error(code), server_error(code), json.dumps(VALID_RESPONSE)])
    result = extract(request())
    assert result.criteria.notice_kind == "모집"
    assert len(models.calls) == 3
    assert clock.sleeps == [2.0, 4.0]  # 지수 백오프


def test_retries_timeout(gemini, clock):
    timeout = httpx.ReadTimeout("timed out")
    models = gemini([timeout, json.dumps(VALID_RESPONSE)])
    extract(request())
    assert len(models.calls) == 2


def test_gives_up_after_three_retries(gemini, clock):
    models = gemini([server_error(503)] * 5)
    with pytest.raises(ExtractionError, match="재시도"):
        extract(request())
    assert len(models.calls) == 4  # 처음 1회 + 재시도 3회
    assert clock.sleeps == [2.0, 4.0, 8.0]


@pytest.mark.parametrize("code", [400, 402, 403])
def test_non_retryable_errors_fail_immediately(gemini, clock, code):
    # 402: 선불 크레딧 소진(9.1)
    models = gemini([server_error(code), json.dumps(VALID_RESPONSE)])
    with pytest.raises(ExtractionError, match=str(code)) as info:
        extract(request())
    assert len(models.calls) == 1
    assert clock.sleeps == []
    assert info.value.counts_as_attempt is (code != 402)  # 402는 재시도 횟수에 세지 않음(9.3)


# --- 검증 실패 ---


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"notice_kind": "모집공지", "scholarships": []}),  # 정해진 값 밖
        json.dumps({**VALID_RESPONSE, "notice_kind": "결과"}),  # 결과인데 장학금 있음
        json.dumps({"notice_kind": "모집", "scholarships": [{"min_gpa": 3.0}]}),  # 이름 없음
    ],
)
def test_validation_failure_is_not_retried(gemini, clock, raw):
    models = gemini([raw, json.dumps(VALID_RESPONSE)])
    with pytest.raises(ExtractionError, match="검증 실패"):
        extract(request())
    assert len(models.calls) == 1


def test_validation_error_message_has_no_notice_text(gemini):
    secret_value = "학번20261234홍길동"
    bad = {
        "notice_kind": "모집",
        "scholarships": [{"scholarship_name": secret_value, "gender": "X"}],
    }
    gemini([json.dumps(bad)])
    with pytest.raises(ExtractionError) as error:
        extract(request())
    assert secret_value not in str(error.value)


def test_empty_response_fails(gemini):
    gemini([""])
    with pytest.raises(ExtractionError, match="비었"):
        extract(request())


# --- 전체 시간 제한 (100초) ---


def test_total_timeout_stops_retrying(gemini, clock):
    # 호출마다 45초 걸리고 실패 → 45 + 2 + 45 = 92초, 다음 백오프 4초 후 96초에 시작하면
    # 남은 시간 4초만 쓸 수 있음 → 그 호출도 실패하면 100초를 넘어 중단
    models = gemini([server_error(503)] * 5, call_seconds=45.0)
    with pytest.raises(ExtractionError, match="100초"):
        extract(request())
    assert len(models.calls) == 3
    assert clock.now <= 100 + 45  # 마지막 호출은 남은 시간만큼만 기다림(가짜 시계라 45초 더해짐)


def test_call_timeout_is_capped_by_remaining_time(gemini, clock):
    models = gemini([server_error(503), json.dumps(VALID_RESPONSE)], call_seconds=50.0)
    extract(request())
    # 두 번째 호출은 50 + 2 = 52초 뒤 시작 → 남은 48초가 제한 시간
    assert models.calls[0]["config"].http_options.timeout == 60_000
    assert models.calls[1]["config"].http_options.timeout == 48_000


def test_no_retry_when_backoff_exceeds_deadline(gemini, clock):
    models = gemini([server_error(503), json.dumps(VALID_RESPONSE)], call_seconds=99.0)
    with pytest.raises(ExtractionError, match="100초"):
        extract(request())
    assert len(models.calls) == 1
    assert clock.sleeps == []


# --- 설정 ---


def test_missing_api_key_fails(monkeypatch):
    monkeypatch.setenv("AI_MOCK", "0")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-flash")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="GEMINI_API_KEY"):
        extract(request())


def test_mock_does_not_need_model_or_key(monkeypatch):
    monkeypatch.setenv("AI_MOCK", "1")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert extract(request()).model_version == "mock+extract_v5"


# --- import 경계 (8.1) ---

FORBIDDEN_MODULES = ("app.api", "app.core.crypto", "app.core.security", "app.models.user")
FORBIDDEN_NAMES = {"User", "UserProfile", "UserScholarshipState"}


def test_extraction_source_does_not_import_user_modules():
    for path in EXTRACTION_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                imported = {alias.name for alias in node.names}
                assert not imported & FORBIDDEN_NAMES, f"{path.name}: {imported & FORBIDDEN_NAMES}"
                if node.module == "app.models":
                    raise AssertionError(f"{path.name}: app.models 전체를 import하지 않습니다")
            else:
                continue
            for name in names:
                assert not name.startswith(FORBIDDEN_MODULES), f"{path.name}: {name}"


def test_importing_extraction_does_not_load_user_modules():
    # 다른 테스트가 이미 불러온 모듈과 섞이지 않도록 새 프로세스에서 확인합니다.
    code = (
        "import sys, app.extraction, app.extraction.extractor; "
        f"print([m for m in sys.modules if m.startswith({FORBIDDEN_MODULES!r}) "
        "or m == 'app.models'])"
    )
    output = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.strip()
    assert output == "[]"


def test_importing_extraction_does_not_load_genai():
    # google-genai는 함수 안에서만 import(9.5)
    output = subprocess.run(
        [sys.executable, "-c", "import sys, app.extraction; print('google.genai' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.strip()
    assert output == "False"
