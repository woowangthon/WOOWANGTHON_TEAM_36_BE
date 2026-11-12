import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.extraction import (
    ExtractedCriteria,
    ExtractionError,
    ExtractionResult,
    ExtractRequest,
    ScholarshipCriteria,
    extract,
)
from app.extraction.schemas import CRITERIA_FIELDS, DISPLAY_NAME_MAX

SEOUL = ZoneInfo("Asia/Seoul")

# 계획서 5.2 표의 예시 값을 JSON으로 옮긴 것 (Gemini 응답·골든셋 정답과 같은 형태)
EXAMPLE_JSON = {
    "notice_kind": "모집",
    "scholarships": [
        {
            "scholarship_name": "두산연강재단 장학금",
            "benefit_amount_text": "연 600만원",
            "benefit_type": "등록금성",
            "apply_start": "2026-10-30T09:00:00+09:00",
            "apply_end": "2026-11-12T18:00:00+09:00",
            "target_grades": [2, 3],
            "excludes_extra_semester": False,
            "enrollment_status": ["재학"],
            "min_gpa": 3.5,
            "min_credits": 12,
            "gender": "여",
            "max_age": 26,
            "max_income_bracket": 8,
            "regions": ["충청남도 논산시"],
            "region_basis": "본인",
            "required_special": ["다자녀"],
            "excludes_full_tuition": True,
            "target_department": "소프트웨어학부 포함",
            "target_program": "AIBC 양성인재",
            "required_documents": ["재학증명서", "성적증명서"],
            "extra_conditions": ["직전학기 봉사활동 20시간 이상"],
            "ambiguous_fields": ["max_income_bracket"],
            "evidence": {"min_gpa": "직전학기 평점평균 3.5 이상인 자"},
        }
    ],
}


def _example(**overrides):
    data = copy.deepcopy(EXAMPLE_JSON)
    data["scholarships"][0].update(overrides)
    return data


# --- 5.2 예시 검증 ---------------------------------------------------------


def test_example_json_validates():
    criteria = ExtractedCriteria.model_validate(EXAMPLE_JSON)
    s = criteria.scholarships[0]
    assert s.apply_end == datetime(2026, 11, 12, 18, 0, tzinfo=SEOUL)
    assert s.max_income_bracket == 8
    assert s.evidence["min_gpa"].startswith("직전학기")


def test_evidence_source_names_body_or_attachment():
    data = _example(
        evidence={
            "min_gpa": "직전학기 평점평균 3.5 이상인 자",
            "apply_end": "11.12.(수) 18:00까지",
        },
        evidence_source={"min_gpa": "붙임1.zip / 업무처리기준.pdf", "apply_end": "본문"},
    )
    s = ExtractedCriteria.model_validate(data).scholarships[0]
    assert s.evidence_source == {"min_gpa": "붙임1.zip / 업무처리기준.pdf", "apply_end": "본문"}


def test_v3_result_without_evidence_source_still_loads():
    """DB에 저장된 v3 결과(evidence_source 없음)도 읽힙니다."""
    s = ScholarshipCriteria.model_validate({"scholarship_name": "교내 장학금"})
    assert s.evidence_source == {}


def test_example_round_trips_through_json():
    criteria = ExtractedCriteria.model_validate(EXAMPLE_JSON)
    again = ExtractedCriteria.model_validate_json(criteria.model_dump_json())
    assert again == criteria


def test_null_means_no_condition():
    """5.2: 조건 필드를 비워 두면 None(조건 없음)이고 목록 필드는 빈 목록입니다."""
    s = ScholarshipCriteria(scholarship_name="교내 장학금")
    for name in ("min_gpa", "target_grades", "max_income_bracket", "regions", "gender"):
        assert getattr(s, name) is None
    assert s.required_documents == [] and s.ambiguous_fields == [] and s.evidence == {}
    assert s.target_department == "불명"
    assert s.target_program == "제한없음"


def test_multiple_scholarships_in_one_notice():
    data = copy.deepcopy(EXAMPLE_JSON)
    data["scholarships"].append({"scholarship_name": "교외장학금 B", "min_gpa": 3.0})
    assert len(ExtractedCriteria.model_validate(data).scholarships) == 2


@pytest.mark.parametrize("kind", ["결과", "기타"])
def test_result_and_other_notices_have_no_scholarships(kind):
    assert ExtractedCriteria.model_validate({"notice_kind": kind}).scholarships == []
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate({**EXAMPLE_JSON, "notice_kind": kind})


@pytest.mark.parametrize("bracket", [0, 0.5, 1, 10])
def test_income_bracket_allowed_values(bracket):
    s = ExtractedCriteria.model_validate(_example(max_income_bracket=bracket)).scholarships[0]
    assert s.max_income_bracket == bracket


def test_new_student_and_remaining_semester_fields():
    s = ExtractedCriteria.model_validate(
        _example(excludes_new_students=True, min_remaining_semesters=1)
    ).scholarships[0]
    assert s.excludes_new_students is True
    assert s.min_remaining_semesters == 1


@pytest.mark.parametrize("value", [0, 9])
def test_min_remaining_semesters_range(value):
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(min_remaining_semesters=value))


def test_v5_criteria_without_new_fields_still_load():
    """extract_v6 이전에 저장된 조건(새 키 없음)도 읽혀야 합니다."""
    s = ScholarshipCriteria.model_validate({"scholarship_name": "v5 장학금"})
    assert s.excludes_new_students is None and s.min_remaining_semesters is None


def test_v6_criteria_without_new_fields_load():
    """extract_v7 이전에 저장된 조건(새 키 없음)도 읽혀야 합니다."""
    s = ScholarshipCriteria.model_validate({"scholarship_name": "v6 장학금"})
    assert s.excludes_transfer_students is None and s.target_nationality is None


@pytest.mark.parametrize("value", ["domestic", "foreign"])
def test_target_nationality_values(value):
    s = ExtractedCriteria.model_validate(_example(target_nationality=value)).scholarships[0]
    assert s.target_nationality == value


def test_target_nationality_rejects_other():
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(target_nationality="korean"))


def test_new_v7_fields_in_criteria_field():
    assert "excludes_transfer_students" in CRITERIA_FIELDS
    assert "target_nationality" in CRITERIA_FIELDS


def test_criteria_fields_cover_scholarship_fields():
    """ambiguous_fields·evidence 키 목록이 조건 필드와 어긋나지 않게 합니다."""
    model_fields = set(ScholarshipCriteria.model_fields)
    meta = {
        "extra_conditions",
        "basic_conditions",
        "notes",
        "ambiguous_fields",
        "evidence",
        "evidence_source",
        "display_name",  # 표시용 이름(extract_v8). 조건이 아니라 근거·모호 표시가 없음
    }
    assert set(CRITERIA_FIELDS) == model_fields - meta


# --- 정해진 값 밖의 값 거부 -------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("benefit_type", "현금"),
        ("gender", "남녀"),
        ("region_basis", "학교"),
        ("target_department", "컴퓨터공학과"),
        ("target_program", "SW중심대학"),
        ("enrollment_status", ["졸업"]),
        ("enrollment_status", []),
        ("target_grades", [0]),
        ("target_grades", [5]),
        ("target_grades", []),
        ("max_income_bracket", 11),
        ("max_income_bracket", 1.5),
        ("max_income_bracket", -1),
        ("min_gpa", 4.6),
        ("min_gpa", -0.1),
        ("min_credits", -1),
        ("max_age", -1),
        ("regions", []),
        ("required_special", []),
        ("ambiguous_fields", ["income"]),
        ("evidence", {"income": "소득 8구간 이하"}),
        ("apply_end", "2026-11-12T18:00:00"),  # 시간대 없는 값
        ("scholarship_name", ""),
    ],
)
def test_rejects_values_outside_allowed(field, value):
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(**{field: value}))


def test_rejects_unknown_notice_kind():
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate({**EXAMPLE_JSON, "notice_kind": "공지"})


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(user_gpa=4.0))
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate({**EXAMPLE_JSON, "extra": 1})


# --- ExtractRequest: 공지 텍스트만 받음 (8.1) --------------------------------


def _request(**overrides):
    data = {
        "source_code": "SW_DEPT",
        "title": "2026학년도 교외장학금 모집 ( ~11.12 18:00)",
        "posted_at": "2026-10-30T09:00:00+09:00",
        "text": "직전학기 평점평균 3.5 이상인 자",
    }
    return ExtractRequest.model_validate({**data, **overrides})


def test_extract_request_fields_are_notice_only():
    assert set(ExtractRequest.model_fields) == {"source_code", "title", "posted_at", "text"}
    assert _request().source_code == "SW_DEPT"


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_code": "CBNU"},
        {"posted_at": "2026-10-30T09:00:00"},  # 시간대 없음
        {"title": ""},
        {"user_id": 1},  # 사용자 정보를 넣을 자리가 없음
    ],
)
def test_extract_request_rejects_invalid(overrides):
    with pytest.raises(ValidationError):
        _request(**overrides)


# --- extract() 목 응답·설정 --------------------------------------------------------


def test_extract_returns_mock_when_ai_mock(monkeypatch):
    monkeypatch.setenv("AI_MOCK", "1")
    result = extract(_request())
    assert isinstance(result, ExtractionResult)
    assert result.model_version == "mock+extract_v8"
    # 목 응답도 스키마 검증을 다시 통과해야 합니다.
    again = ExtractionResult.model_validate_json(result.model_dump_json())
    assert again.criteria.notice_kind == "모집"
    assert again.criteria.scholarships[0].display_name == "두산연강재단 장학금"


def test_extract_without_model_name_fails(monkeypatch):
    # GEMINI_MODEL은 코드에 기본값이 없음(T15). 앱 시작은 막지 않고 추출할 때 실패합니다.
    monkeypatch.setenv("AI_MOCK", "0")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with pytest.raises(ExtractionError):
        extract(_request())


# --- 쉬운 이름 display_name (T41, extract_v8) -----------------------------------------


def test_v7_criteria_without_display_name_load():
    """extract_v8 이전에 저장된 조건(display_name 없음)도 읽혀야 합니다."""
    s = ScholarshipCriteria.model_validate({"scholarship_name": "v7 장학금"})
    assert s.display_name is None


def test_display_name_whitespace_is_collapsed():
    raw = "  두산연강재단 \n 장학금 "
    s = ScholarshipCriteria(scholarship_name="원래 이름", display_name=raw)
    assert s.display_name == "두산연강재단 장학금"


@pytest.mark.parametrize("value", ["", "   ", "가" * (DISPLAY_NAME_MAX + 1)])
def test_bad_display_name_becomes_none_instead_of_failing(value):
    """표시용 값 때문에 공지 전체가 추출 실패가 되지 않습니다."""
    s = ScholarshipCriteria(scholarship_name="원래 이름", display_name=value)
    assert s.display_name is None
    assert s.scholarship_name == "원래 이름"


def test_display_name_at_max_length_is_kept():
    s = ScholarshipCriteria(scholarship_name="원래 이름", display_name="가" * DISPLAY_NAME_MAX)
    assert s.display_name == "가" * DISPLAY_NAME_MAX


def test_display_name_in_json_schema():
    """Gemini Structured Output 스키마에 display_name이 들어가야 합니다."""
    props = ScholarshipCriteria.model_json_schema()["properties"]
    assert "display_name" in props
