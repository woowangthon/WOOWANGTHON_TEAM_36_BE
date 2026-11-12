"""✓/✗ 조건 요약 (T43, spec 3.2). 값으로 코드가 만든 문구와 생략 규칙을 확인합니다."""

from typing import get_args

import pytest

from app.extraction.schemas import ScholarshipCriteria
from app.matching.describe import DESCRIBED, NAME_ONLY, describe, is_ambiguous
from app.schemas.api.scholarships import ConditionName


def criteria(**fields) -> ScholarshipCriteria:
    return ScholarshipCriteria(scholarship_name="테스트 장학금", **fields)


def test_every_condition_is_classified():
    assert DESCRIBED | NAME_ONLY == set(get_args(ConditionName))
    assert not DESCRIBED & NAME_ONLY
    assert len(DESCRIBED) == 13
    assert len(NAME_ONLY) == 6


@pytest.mark.parametrize(
    ("condition", "fields", "expected"),
    [
        ("target_grades", {"target_grades": [2, 3]}, "2·3학년"),
        ("min_remaining_semesters", {"min_remaining_semesters": 1}, "1학기 이상"),
        ("enrollment_status", {"enrollment_status": ["재학"]}, "재학생"),
        ("enrollment_status", {"enrollment_status": ["재학", "휴학"]}, "재학생 또는 휴학생"),
        ("min_gpa", {"min_gpa": 3.5, "grade_basis": "직전학기"}, "직전학기 3.5 이상"),
        ("min_gpa", {"min_gpa": 3.5}, "3.5 이상"),
        ("min_gpa", {"min_gpa": 3.0}, "3.0 이상"),
        ("min_score_100", {"min_score_100": 80, "grade_basis": "전체"}, "전체 학기 80점 이상"),
        ("min_credits", {"min_credits": 12}, "12학점 이상"),
        ("target_nationality", {"target_nationality": "domestic"}, "대한민국 국적"),
        ("target_nationality", {"target_nationality": "foreign"}, "외국 국적"),
        ("gender", {"gender": "여"}, "여학생"),
        ("max_age", {"max_age": 26}, "만 26세 이하"),
        ("max_income_bracket", {"max_income_bracket": 8}, "8구간 이하"),
        ("max_income_bracket", {"max_income_bracket": 0}, "기초생활수급자"),
        ("max_income_bracket", {"max_income_bracket": 0.5}, "차상위계층 이하"),
        (
            "regions",
            {"regions": ["충청남도 논산시"], "region_basis": "부모"},
            "부모 주소 충청남도 논산시",
        ),
        (
            "regions",
            {"regions": ["충청남도 논산시"], "region_basis": "본인"},
            "본인 주소 충청남도 논산시",
        ),
        ("regions", {"regions": ["충청남도 논산시"], "region_basis": "무관"}, "충청남도 논산시"),
        ("regions", {"regions": ["충청남도 논산시"]}, "충청남도 논산시"),
        (
            "regions",
            {"regions": ["충청남도", "대전광역시"], "region_basis": "본인"},
            "본인 주소 충청남도 또는 대전광역시",
        ),
        ("required_special", {"required_special": ["다자녀", "한부모"]}, "다자녀 또는 한부모"),
        (
            "target_department",
            {"target_department": "소프트웨어학부 포함"},
            "소프트웨어학부 포함 학과",
        ),
        (
            "target_department",
            {"target_department": "소프트웨어학부 불포함"},
            "소프트웨어학부 외 학과",
        ),
    ],
)
def test_describe(condition, fields, expected):
    assert describe(condition, criteria(**fields)) == expected


def test_missing_value_has_no_requirement():
    for condition in DESCRIBED:
        assert describe(condition, criteria()) is None


def test_name_only_conditions_have_no_requirement():
    c = criteria(excludes_new_students=True, excludes_transfer_students=True)
    for condition in NAME_ONLY:
        assert describe(condition, c) is None


def test_ambiguous_field_has_no_requirement():
    c = criteria(max_income_bracket=8, ambiguous_fields=["max_income_bracket"])
    assert describe("max_income_bracket", c) is None
    assert is_ambiguous("max_income_bracket", c)


def test_unknown_department_is_ambiguous():
    c = criteria(target_department="불명")
    assert describe("target_department", c) is None
    assert is_ambiguous("target_department", c)


def test_grade_basis_only_ambiguous_keeps_value():
    c = criteria(min_gpa=3.5, ambiguous_fields=["grade_basis"])
    assert describe("min_gpa", c) == "3.5 이상"
    assert not is_ambiguous("min_gpa", c)
