"""✓/✗ 조건 요약 문구 (T43, spec 3.2).

프로필로 이미 판정하는 조건의 기준값을 보여 주는 문구이며, 판정에는 쓰지 않습니다.
이미 추출된 값(`ScholarshipCriteria`)에서 코드가 만들므로 LLM을 부르지 않습니다.
DB나 HTTP에 의존하지 않는 순수 함수입니다.
"""

from collections.abc import Callable

from app.extraction.schemas import ScholarshipCriteria
from app.schemas.api.scholarships import ConditionName


def _num(value: float) -> str:
    """정수면 소수점 없이 씁니다(80, 12)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _basis(c: ScholarshipCriteria) -> str:
    """성적 기준 학기 접두어. 기준이 모호하다고 표시됐으면 생략합니다."""
    if c.grade_basis is None or "grade_basis" in c.ambiguous_fields:
        return ""
    return ("직전학기" if c.grade_basis == "직전학기" else "전체 학기") + " "


def _target_grades(c: ScholarshipCriteria) -> str | None:
    if not c.target_grades:
        return None
    return "·".join(str(g) for g in c.target_grades) + "학년"


def _min_admission_year(c: ScholarshipCriteria) -> str | None:
    if c.min_admission_year is None:
        return None
    return f"{c.min_admission_year}학년도 이후 입학생"


def _min_remaining_semesters(c: ScholarshipCriteria) -> str | None:
    if c.min_remaining_semesters is None:
        return None
    return f"{_num(c.min_remaining_semesters)}학기 이상"


def _enrollment_status(c: ScholarshipCriteria) -> str | None:
    if not c.enrollment_status:
        return None
    return " 또는 ".join(f"{s}생" for s in c.enrollment_status)


def grade_either_applies(c: ScholarshipCriteria) -> bool:
    """평점·100점 조건 중 하나만 맞으면 되는지 (`grade_either`, extract_v12).

    두 조건이 모두 있어야(값이 있거나 모호하다고 표시됨) OR로 묶습니다. 한쪽만 있으면 보통처럼
    따로 판정합니다. 묶으면 엔진은 `min_gpa` 한 줄로 판정하고 `min_score_100` 줄은 만들지 않습니다.
    """

    def present(field: str) -> bool:
        return getattr(c, field) is not None or field in c.ambiguous_fields

    return bool(c.grade_either) and present("min_gpa") and present("min_score_100")


def _min_gpa(c: ScholarshipCriteria) -> str | None:
    if c.min_gpa is None:
        return None
    if grade_either_applies(c):
        if c.min_score_100 is None:  # 100점 쪽이 모호함 (is_ambiguous가 표시)
            return None
        return f"{_basis(c)}{c.min_gpa} 이상 또는 {_num(c.min_score_100)}점 이상"
    return f"{_basis(c)}{c.min_gpa} 이상"


def _min_score_100(c: ScholarshipCriteria) -> str | None:
    if c.min_score_100 is None:
        return None
    return f"{_basis(c)}{_num(c.min_score_100)}점 이상"


def _min_credits(c: ScholarshipCriteria) -> str | None:
    if c.min_credits is None:
        return None
    return f"{_num(c.min_credits)}학점 이상"


def _target_nationality(c: ScholarshipCriteria) -> str | None:
    return {"domestic": "대한민국 국적", "foreign": "외국 국적"}.get(c.target_nationality or "")


def _gender(c: ScholarshipCriteria) -> str | None:
    return f"{c.gender}학생" if c.gender else None


def _max_age(c: ScholarshipCriteria) -> str | None:
    if c.max_age is None:
        return None
    return f"만 {_num(c.max_age)}세 이하"


def _max_income_bracket(c: ScholarshipCriteria) -> str | None:
    value = c.max_income_bracket
    if value is None:
        return None
    if value == 0:
        return "기초생활수급자"
    if value == 0.5:
        return "차상위계층 이하"
    return f"{_num(value)}구간 이하"


def _regions(c: ScholarshipCriteria) -> str | None:
    if not c.regions:
        return None
    text = " 또는 ".join(c.regions)
    if c.region_basis in ("본인", "부모") and "region_basis" not in c.ambiguous_fields:
        return f"{c.region_basis} 주소 {text}"
    return text


def _required_special(c: ScholarshipCriteria) -> str | None:
    if not c.required_special:
        return None
    return " 또는 ".join(c.required_special)


def _target_department(c: ScholarshipCriteria) -> str | None:
    return {
        "소프트웨어학부 포함": "소프트웨어학부 포함 학과",
        "소프트웨어학부 불포함": "소프트웨어학부 외 학과",
    }.get(c.target_department)


_DESCRIBERS: dict[ConditionName, Callable[[ScholarshipCriteria], str | None]] = {
    "target_grades": _target_grades,
    "min_admission_year": _min_admission_year,
    "min_remaining_semesters": _min_remaining_semesters,
    "enrollment_status": _enrollment_status,
    "min_gpa": _min_gpa,
    "min_score_100": _min_score_100,
    "min_credits": _min_credits,
    "target_nationality": _target_nationality,
    "gender": _gender,
    "max_age": _max_age,
    "max_income_bracket": _max_income_bracket,
    "regions": _regions,
    "required_special": _required_special,
    "target_department": _target_department,
}

# 요약을 붙이는 14개
DESCRIBED: frozenset[ConditionName] = frozenset(_DESCRIBERS)
# 이름만으로 충분해 요약을 붙이지 않는 6개
NAME_ONLY: frozenset[ConditionName] = frozenset(
    {
        "excludes_extra_semester",
        "excludes_new_students",
        "excludes_transfer_students",
        "school_excluded",
        "excludes_full_tuition",
        "target_program",
    }
)


_BASIS_FIELD: dict[ConditionName, str] = {
    "min_gpa": "grade_basis",
    "min_score_100": "grade_basis",
    "regions": "region_basis",
}


def _own_field_ambiguous(condition: ConditionName, c: ScholarshipCriteria) -> bool:
    """조건 자기 필드가 모호하다고 표시됐는지. 소속 학과 "불명"도 모호로 봅니다."""
    if condition in c.ambiguous_fields:
        return True
    return condition == "target_department" and c.target_department == "불명"


def is_ambiguous(condition: ConditionName, c: ScholarshipCriteria) -> bool:
    """조건이 모호하다고 표시됐는지. 평점·100점 성적·지역은 기준 필드만 모호해도 모호입니다.

    기준 필드만 모호하면 규칙은 알 수 없음이지만 값 문구(`describe`)는 남습니다.
    """
    if _own_field_ambiguous(condition, c):
        return True
    if condition == "min_gpa" and grade_either_applies(c) and "min_score_100" in c.ambiguous_fields:
        return True  # 평점·100점 OR 줄(extract_v12)은 100점 쪽이 모호해도 모호입니다.
    basis = _BASIS_FIELD.get(condition)
    return basis is not None and basis in c.ambiguous_fields


def describe(condition: ConditionName, c: ScholarshipCriteria) -> str | None:
    """조건의 기준값 문구. 이름만 쓰는 조건·자기 필드가 모호한 조건·값이 없는 조건은 None."""
    if condition in NAME_ONLY or _own_field_ambiguous(condition, c):
        return None
    return _DESCRIBERS[condition](c)
