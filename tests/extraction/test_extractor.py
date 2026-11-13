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
    assert result.model_version == "gemini-test-flash+extract_v11"
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
    assert config.thinking_config.thinking_level == "LOW"  # 기본값(높음)이면 큰 공지가 시간 초과
    assert config.http_options.timeout == 90_000  # 호출 1회 90초


def test_request_contains_only_notice_fields(gemini):
    models = gemini([json.dumps(VALID_RESPONSE)])
    extract(request("본문 내용"))
    contents = models.calls[0]["contents"]
    assert contents.startswith("출처: SW_DEPT\n제목: 푸른등대 기부장학금 선발 안내")
    assert "게시 시각: 2026-10-22T15:33:00+09:00" in contents
    assert contents.endswith("---\n본문 내용")


def test_input_is_truncated_to_max_chars(gemini):
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
        "`excludes_new_students`",  # 새 필드(extract_v6)
        "`min_remaining_semesters`",
        "`excludes_transfer_students`",  # 새 필드(extract_v7)
        "`target_nationality`",
        "`school_excluded`",  # 새 필드(extract_v8)
        "`min_admission_year`",  # 새 필드(extract_v11)
        "`display_name`",  # 쉬운 이름(extract_v9)
        "다듬지 않는다",  # scholarship_name은 원래 이름 그대로(재추출 매칭 키)
        "지어내지 않는다",
        "충북대학교",  # 학교 정보로 알 수 있는 조건 → basic_conditions
        "세 목록 어디에도 다시 넣지 않는다",  # 필드로 옮긴 문장
        "문장 전체",  # 섞인 문장
        "수혜·선발 이력",  # 이력 조건 → extra_conditions
        "`summary`",  # 조건 요약(extract_v10)
        "맞아요",  # 긍정형: "맞아요" = 충족
        "어느 목록에도 넣지 않는다",  # 같은 공지에 세부 자격이 있는 규정 출처
        "직접 답할 부분만",  # 섞인 문장 요약
    ):
        assert rule in prompt, rule


def _prompt_line(prefix: str) -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return next(line for line in prompt.splitlines() if line.lstrip().startswith(prefix))


def test_transfer_only_exclusion_goes_to_transfer_field():
    """편입생만 제외하면 신입생을 떨어뜨리지 않게 따로 둡니다(extract_v7)."""
    line = _prompt_line("- `excludes_new_students`")
    assert "**신입생을 제외하면** `true`" in line
    assert "**편입생만** 제외하면 이 필드에 넣지 않고 `excludes_transfer_students`" in line


def test_nationality_is_not_basic_condition():
    """국적은 판정하는 필드로 옮겼으므로 basic_conditions 예시에 없어야 합니다(extract_v7)."""
    line = _prompt_line("- 충북대학교가 충족하는 학교 조건")
    assert "국적" not in line


def test_basic_conditions_only_when_school_satisfies():
    """충북대가 충족하는 학교 조건만 basic이고, 못 하면 school_excluded입니다(extract_v8)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "충북대학교가 충족하는" in prompt
    assert "충족하지 못하는" in prompt


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


# --- 전체 시간 제한 (150초) ---


def test_total_timeout_stops_retrying(gemini, clock):
    # 호출마다 70초 걸리고 실패 → 70 + 2 + 70 = 142초, 다음 백오프 4초 후 146초에 시작하면
    # 남은 시간 4초만 쓸 수 있음 → 그 호출도 실패하면 150초를 넘어 중단
    models = gemini([server_error(503)] * 5, call_seconds=70.0)
    with pytest.raises(ExtractionError, match="150초"):
        extract(request())
    assert len(models.calls) == 3
    assert clock.now <= 150 + 70  # 마지막 호출은 남은 시간만큼만 기다림(가짜 시계라 70초 더해짐)


def test_call_timeout_is_capped_by_remaining_time(gemini, clock):
    models = gemini([server_error(503), json.dumps(VALID_RESPONSE)], call_seconds=80.0)
    extract(request())
    # 두 번째 호출은 80 + 2 = 82초 뒤 시작 → 남은 68초가 제한 시간
    assert models.calls[0]["config"].http_options.timeout == 90_000
    assert models.calls[1]["config"].http_options.timeout == 68_000


def test_no_retry_when_backoff_exceeds_deadline(gemini, clock):
    models = gemini([server_error(503), json.dumps(VALID_RESPONSE)], call_seconds=149.0)
    with pytest.raises(ExtractionError, match="150초"):
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
    assert extract(request()).model_version == "mock+extract_v11"


# --- import 경계 (8.1) ---

FORBIDDEN_MODULES = (
    "app.api",
    "app.core.crypto",
    "app.core.security",
    "app.models.user",
    "app.models.notification",
    "app.notifications",
)
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


def test_unmet_school_condition_goes_to_school_excluded():
    """충북대가 충족하지 못하는 학교 조건은 지원 불가로 판정합니다(extract_v8)."""
    line = _prompt_line("- `school_excluded`")
    assert "사립대학" in line and "전문대학" in line
    assert "`false`는 쓰지 않는다" in line


def test_regulation_title_with_details_is_not_external_reference():
    """세부 자격이 같은 공지에 있으면 "관리지침을 따름"은 외부 기준 참조가 아닙니다(extract_v8)."""
    line = _prompt_line("- **외부 기준 참조**")
    assert "같은 공지에 이어서 적혀 있으면" in line
    assert "어느 목록에도 넣지 않는다" in line  # extract_v10: basic_conditions에도 넣지 않음
    assert "`basic_conditions`에 넣는다" not in line


def test_headings_go_to_no_list():
    """제목·소제목은 어느 목록에도 넣지 않으므로 다른 규칙과 어긋나지 않아야 합니다(extract_v10)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "제목처럼" not in prompt  # v9: "설명·안내·제목처럼 … notes나 basic_conditions다"
    lead = _prompt_line("7. **필드로 옮길 수 없는 문장은")
    assert "어느 목록에도 넣지 않는다" in lead  # 규칙 7 머리의 예외
    answerable = _prompt_line("- **`extra_conditions`에는 학생이")
    assert "(제목·소제목은 어느 목록에도 넣지 않는다)" in answerable
    assert '"맞아요/안 맞아요"' in answerable  # 요약 극성("맞아요" = 충족)과 같은 말
    assert "해당돼요" not in prompt


def test_display_name_has_no_evidence():
    """display_name은 조건이 아니라 근거 키로 쓰면 검증 실패였습니다(eval sw_dept_1154905)."""
    line = _prompt_line("- `display_name`은 조건이 아니므로")
    assert "`evidence`" in line and "`evidence_source`" in line


def test_summary_keeps_widening_exceptions():
    """단서·예외를 빼면 자격이 뒤집히므로 요약에 담거나 비웁니다(extract_v10, eval 검토)."""
    line = _prompt_line("- **단서·예외**")
    assert "반드시 담는다" in line
    assert "`summary`를 비워 둔다(`null`)" in line
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert '| "국가·교내장학금 외 장학금을 받지 않는 학생" |' in prompt
    assert "| (비움, `null`) |" in prompt


def test_prompt_v10_drops_source_line_rule():
    """규정 출처 문장은 조건이 아니므로 basic_conditions 예시에서 뺐습니다(extract_v10)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "세부 자격이 같은 공지에 적힌 규정의 출처 문장: " not in prompt


def test_extra_conditions_must_be_answerable():
    """extra_conditions 문장은 학생이 직접 답하므로 답할 수 있는 문장만 넣습니다(extract_v8)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "답할 수 있는 문장만 넣는다" in prompt
    assert "추후 안내" in prompt


def test_basic_conditions_line_sends_unmet_school_to_school_excluded():
    """기본 조건 설명도 충북대가 충족하지 못하는 학교 조건을 school_excluded로 보냅니다.

    v8에서 이 줄만 extra_conditions로 남아 3장 규칙과 충돌했습니다(오류 현황 E-07).
    """
    line = _prompt_line("- `basic_conditions`")
    assert "충족하지 못하면 여기가 아니라 `school_excluded`" in line
    assert "충족하지 못하면 `extra_conditions`다" not in line


def test_enrollment_year_limit_is_not_a_plan_note():
    """입학 연도로 대상을 좁히는 문장은 안내(notes)가 아니라 자격입니다(서비스 QA 문제-6)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert '연차별 대상 학년을 확대할 예정입니다"' not in prompt
    assert "입학 연도나 입학 예정자로 한정하는 문장" in prompt
    # extract_v11부터는 extra_conditions가 아니라 min_admission_year로 판정합니다(T48).
    assert "입학 연도·입학 예정자 한정" not in _prompt_line("- 그 밖의 자격")


def test_grade_shortfall_without_score_is_basic_not_ambiguous():
    """점수 없이 "성적 미달"만 적힌 제외 대상은 평점을 애매함으로 두지 않고 기본 조건으로 보냅니다.

    v10에서 학과수석장학생(1154016)의 평점이 ambiguous가 되어
    다른 조건이 모두 맞아도 확인 필요였습니다.
    """
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert '기준 점수 없이 "성적 미달"만 적힌 제외 대상' in prompt
    assert "**ambiguous에도 넣지 않는다**" in prompt
    line = _prompt_line("- 기준 점수 없는 성적 제외")
    assert "성적 미달" in line


def test_admission_year_goes_to_field_not_extra_conditions():
    """입학 학년도 한정은 판정하는 필드로 옮겼습니다(extract_v11)."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "2027학년도 신입생" in _prompt_line("- `min_admission_year`")
    assert "게시 시점과 비교해 학년으로 바꾸지 않는다" in prompt
    assert "자격이므로 `min_admission_year`다" in prompt
