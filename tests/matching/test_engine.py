"""규칙 엔진 (T22, 계획서 5.3). 표의 행마다 충족·불충족·알 수 없음과 3단계 판정을 확인합니다."""

from datetime import date

import pytest

from app.extraction.schemas import ScholarshipCriteria
from app.matching.engine import evaluate
from app.parsers.dates import kst
from app.schemas.api.profile import Profile

TODAY = date(2026, 11, 6)


def profile(**overrides) -> Profile:
    base = {
        "name": "홍길동",
        "gender": "여",
        "birth_year": 2004,
        "gpa": 3.6,
        "credits": 15,
        "completed_semesters": 5,  # 3학년
        "enrollment_status": "재학",
        "aibc_member": False,
        "income_bracket": 4,
        "self_region": {"sido": "충청북도", "sigungu": "청주시"},
        "parent_region": {"sido": "충청남도", "sigungu": "논산시"},
        "special_flags": ["다자녀"],
        "full_tuition_waiver": "아니오",
    }
    return Profile.model_validate({**base, **overrides})


def criteria(**fields) -> ScholarshipCriteria:
    return ScholarshipCriteria.model_validate(
        {"scholarship_name": "테스트 장학금", "target_department": "제한없음", **fields}
    )


def outcome(condition: str, p: Profile, c: ScholarshipCriteria, today: date = TODAY) -> str | None:
    checks = {check.condition: check.outcome for check in evaluate(p, c, today).checks}
    return checks.get(condition)


# --- 학년 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p", "grades", "expected"),
    [
        ({"completed_semesters": 5}, [2, 3], "MET"),
        ({"completed_semesters": 0}, [1], "MET"),  # 신입생 첫 학기 = 1학년
        ({"completed_semesters": 1}, [1], "MET"),
        ({"completed_semesters": 1}, [2, 3], "UNMET"),
        ({"completed_semesters": 2}, [2], "MET"),  # 2학기를 마치면 2학년
        ({"completed_semesters": 7}, [4], "MET"),
        ({"completed_semesters": 8}, [4], "MET"),  # 초과학기는 4학년
        ({"completed_semesters": 16}, [2, 3], "UNMET"),
    ],
)
def test_target_grades(p, grades, expected):
    assert outcome("target_grades", profile(**p), criteria(target_grades=grades)) == expected


def test_target_grades_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["target_grades"])
    assert outcome("target_grades", profile(), c) == "UNKNOWN"


@pytest.mark.parametrize(
    ("grades", "extra", "expected"),
    [
        ([4], True, "UNMET"),  # 4학년 대상이어도 초과학기 제외
        ([4], False, "MET"),
        (None, True, "UNMET"),  # target_grades가 null이어도 따로 비교
        (None, False, "MET"),
    ],
)
def test_excludes_extra_semester(grades, extra, expected):
    p = profile(completed_semesters=8 if extra else 7)
    c = criteria(target_grades=grades, excludes_extra_semester=True)
    assert outcome("excludes_extra_semester", p, c) == expected


def test_excludes_extra_semester_false_is_not_compared():
    c = criteria(excludes_extra_semester=False)
    assert outcome("excludes_extra_semester", profile(completed_semesters=8), c) is None


def test_excludes_extra_semester_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["excludes_extra_semester"])
    assert outcome("excludes_extra_semester", profile(), c) == "UNKNOWN"


# --- 재학상태 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "ambiguous", "expected"),
    [("재학", [], "MET"), ("휴학", [], "UNMET"), ("재학", ["enrollment_status"], "UNKNOWN")],
)
def test_enrollment_status(status, ambiguous, expected):
    c = criteria(enrollment_status=None if ambiguous else ["재학"], ambiguous_fields=ambiguous)
    assert outcome("enrollment_status", profile(enrollment_status=status), c) == expected


# --- 평점·이수학점 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gpa", "ambiguous", "expected"),
    [(3.5, [], "MET"), (3.49, [], "UNMET"), (4.0, ["min_gpa"], "UNKNOWN")],
)
def test_min_gpa(gpa, ambiguous, expected):
    c = criteria(min_gpa=None if ambiguous else 3.5, ambiguous_fields=ambiguous)
    assert outcome("min_gpa", profile(gpa=gpa), c) == expected


@pytest.mark.parametrize(
    ("p", "basis", "expected"),
    [
        ({"gpa": 3.5}, "직전학기", "MET"),
        ({"gpa": 3.4, "gpa_total": 3.9}, "직전학기", "UNMET"),  # 전체 평점은 보지 않음
        ({"gpa": 3.9, "gpa_total": 3.5}, "전체", "MET"),
        ({"gpa": 3.9, "gpa_total": 3.4}, "전체", "UNMET"),  # 직전학기 평점은 보지 않음
        ({"gpa": 3.9, "gpa_total": None}, "전체", "UNKNOWN"),  # 전체 평점 미입력
    ],
)
def test_min_gpa_by_grade_basis(p, basis, expected):
    c = criteria(min_gpa=3.5, grade_basis=basis)
    assert outcome("min_gpa", profile(**p), c) == expected


def test_min_gpa_unknown_when_grade_basis_ambiguous():
    c = criteria(min_gpa=2.6, ambiguous_fields=["grade_basis"])
    assert outcome("min_gpa", profile(gpa=4.0, gpa_total=4.0), c) == "UNKNOWN"


@pytest.mark.parametrize(
    ("p", "basis", "ambiguous", "expected"),
    [
        # 100점 환산 = 평점 × 10 + 52.5 (2026-11-10). 기준 85점이면 평점 3.25가 경계
        ({"gpa": 3.25}, "직전학기", [], "MET"),
        ({"gpa": 3.24, "gpa_total": 4.5}, "직전학기", [], "UNMET"),
        ({"gpa": None}, "직전학기", [], "UNKNOWN"),
        ({"gpa": 3.6, "score_100": 50.0}, "직전학기", [], "MET"),  # 저장된 옛 입력값은 안 씀
        ({"gpa": 1.0, "gpa_total": 3.25}, "전체", [], "MET"),
        ({"gpa": 4.5, "gpa_total": 3.0}, "전체", [], "UNMET"),
        ({"gpa": 4.5, "gpa_total": None}, "전체", [], "UNKNOWN"),
        ({"gpa": 3.25}, None, [], "MET"),  # 학기 기준이 없으면 직전학기
        ({"gpa": 4.5, "gpa_total": 4.5}, "직전학기", ["grade_basis"], "UNKNOWN"),
        ({"gpa": 4.5}, None, ["min_score_100"], "UNKNOWN"),
    ],
)
def test_min_score_100(p, basis, ambiguous, expected):
    c = criteria(
        min_score_100=None if "min_score_100" in ambiguous else 85,
        grade_basis=basis,
        ambiguous_fields=ambiguous,
    )
    assert outcome("min_score_100", profile(**p), c) == expected


@pytest.mark.parametrize(
    ("gpa", "score"),
    [(3.6, 88.5), (4.5, 97.5), (3.0, 82.5), (0.0, 52.5), (3.45, 87.0), (None, None)],
)
def test_score_100_from_gpa(gpa, score):
    p = profile(gpa=gpa, gpa_total=gpa, score_100=12.3, score_100_total=45.6)
    assert (p.score_100, p.score_100_total) == (score, score)


def test_either_basis_condition_is_conditional():
    """'직전학기 또는 전체 평균 80점 이상'은 성적 필드 없이 extra_conditions로 → 확인 필요 (5.2)."""
    c = criteria(extra_conditions=["직전학기 또는 전체 평균 성적 80점 이상"])
    evaluation = evaluate(profile(score_100=None), c, TODAY)
    assert evaluation.result == "CONDITIONAL"
    assert "min_score_100" not in {check.condition for check in evaluation.checks}


def test_score_evidence_falls_back_to_grade_basis():
    c = criteria(
        min_score_100=70,
        grade_basis="직전학기",
        evidence={"grade_basis": "직전학기 성적 70점 이상"},
    )
    checks = {ch.condition: ch for ch in evaluate(profile(score_100=75), c, TODAY).checks}
    assert checks["min_score_100"].evidence == "직전학기 성적 70점 이상"


@pytest.mark.parametrize(
    ("credits", "ambiguous", "expected"),
    [(12, [], "MET"), (11, [], "UNMET"), (18, ["min_credits"], "UNKNOWN"), (None, [], "UNKNOWN")],
)
def test_min_credits(credits, ambiguous, expected):
    c = criteria(min_credits=None if ambiguous else 12, ambiguous_fields=ambiguous)
    assert outcome("min_credits", profile(credits=credits), c) == expected


@pytest.mark.parametrize("basis", ["직전학기", "전체", None])
def test_freshman_without_grades_is_unknown_not_unmet(basis):
    """이수학기 0(신입생 첫 학기)은 성적이 없어 성적 조건이 모두 알 수 없음 → 확인 필요(5.3).

    신입생 성적 기준 면제 여부가 공지마다 달라 불충족으로 보지 않습니다.
    """
    p = profile(completed_semesters=0, gpa=None, credits=None)
    c = criteria(min_gpa=3.0, min_score_100=80, min_credits=12, grade_basis=basis)
    result = evaluate(p, c, TODAY)
    assert {check.condition: check.outcome for check in result.checks} == {
        "min_gpa": "UNKNOWN",
        "min_score_100": "UNKNOWN",
        "min_credits": "UNKNOWN",
    }
    assert result.result == "CONDITIONAL"


# --- 성별 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gender", "ambiguous", "expected"),
    [("여", [], "MET"), ("남", [], "UNMET"), ("여", ["gender"], "UNKNOWN")],
)
def test_gender(gender, ambiguous, expected):
    c = criteria(gender=None if ambiguous else "여", ambiguous_fields=ambiguous)
    assert outcome("gender", profile(gender=gender), c) == expected


# --- 나이 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("birth_year", "expected"),
    [
        (2000, "MET"),  # c = 26 = max_age
        (1999, "UNKNOWN"),  # c = 27 = max_age + 1 (생일 전이면 아직 26세)
        (1998, "UNMET"),  # c = 28 = max_age + 2
        (2005, "MET"),
    ],
)
def test_max_age_boundaries(birth_year, expected):
    c = criteria(max_age=26)
    assert outcome("max_age", profile(birth_year=birth_year), c) == expected


def test_max_age_uses_apply_end_year():
    """기준일은 apply_end. 올해(2026) 기준이면 c=26이지만 마감이 2027년이면 c=27."""
    p = profile(birth_year=2000)
    assert outcome("max_age", p, criteria(max_age=26)) == "MET"
    c = criteria(max_age=26, apply_end=kst(2027, 1, 10, 18))
    assert outcome("max_age", p, c) == "UNKNOWN"


def test_max_age_apply_end_is_read_in_seoul_time():
    """UTC로 2026-12-31 16:00 = 한국 2027-01-01 01:00 → 2027년 기준."""
    from datetime import UTC, datetime

    c = criteria(max_age=26, apply_end=datetime(2026, 12, 31, 16, tzinfo=UTC))
    assert outcome("max_age", profile(birth_year=2000), c) == "UNKNOWN"


def test_max_age_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["max_age"])
    assert outcome("max_age", profile(), c) == "UNKNOWN"


# --- 소득분위 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("income", "limit", "expected"),
    [
        (4, 8, "MET"),
        (8, 8, "MET"),
        (9, 8, "UNMET"),
        (None, 8, "UNKNOWN"),  # 모름
        (0, 0, "MET"),  # 기초생활수급자 전용
        (0.5, 0, "UNMET"),  # 차상위는 기초 전용 장학금 불가
        (0.5, 0.5, "MET"),  # 기초·차상위 대상
        (0, 0.5, "MET"),
        (1, 0.5, "UNMET"),
    ],
)
def test_max_income_bracket(income, limit, expected):
    c = criteria(max_income_bracket=limit)
    assert outcome("max_income_bracket", profile(income_bracket=income), c) == expected


def test_income_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["max_income_bracket"])
    assert outcome("max_income_bracket", profile(income_bracket=1), c) == "UNKNOWN"


# --- 지역 ---------------------------------------------------------------------------

NONSAN = {"sido": "충청남도", "sigungu": "논산시"}
CHEONGJU = {"sido": "충청북도", "sigungu": "청주시"}
GONGJU = {"sido": "충청남도", "sigungu": "공주시"}


@pytest.mark.parametrize(
    ("regions", "self_region", "expected"),
    [
        # 단위: 시/도만이면 시/도까지
        (["충청남도"], GONGJU, "MET"),
        (["충청남도"], CHEONGJU, "UNMET"),
        (["충남"], GONGJU, "MET"),  # 줄임말
        # 단위: 시/군/구까지
        (["충청남도 논산시"], NONSAN, "MET"),
        (["충청남도 논산시"], GONGJU, "UNMET"),
        (["충남 논산"], NONSAN, "MET"),
        (["충청남도 논산시"], {"sido": "충청남도"}, "UNKNOWN"),  # 프로필은 시/도만
        (["충청북도 청주시", "충청남도 논산시"], NONSAN, "MET"),  # 여러 지역 중 하나
        (["충청남도 논산시"], None, "UNKNOWN"),  # 주소 미입력
    ],
)
def test_regions_basis_self(regions, self_region, expected):
    p = profile(self_region=self_region)
    c = criteria(regions=regions, region_basis="본인")
    assert outcome("regions", p, c) == expected


@pytest.mark.parametrize(
    ("parent_region", "expected"),
    [(NONSAN, "MET"), (GONGJU, "UNMET"), (None, "UNKNOWN")],
)
def test_regions_basis_parent_ignores_self(parent_region, expected):
    p = profile(self_region=NONSAN, parent_region=parent_region)
    c = criteria(regions=["충청남도 논산시"], region_basis="부모")
    assert outcome("regions", p, c) == expected


@pytest.mark.parametrize(
    ("self_region", "parent_region", "expected"),
    [
        (CHEONGJU, NONSAN, "MET"),  # 하나라도 맞으면 충족
        (NONSAN, None, "MET"),
        (CHEONGJU, GONGJU, "UNMET"),  # 둘 다 입력했고 둘 다 안 맞음
        (CHEONGJU, None, "UNKNOWN"),  # 입력한 주소는 안 맞고 하나가 비어 있음
        (None, GONGJU, "UNKNOWN"),
        (None, None, "UNKNOWN"),
    ],
)
def test_regions_basis_any(self_region, parent_region, expected):
    p = profile(self_region=self_region, parent_region=parent_region)
    for basis in ("무관", None):
        c = criteria(regions=["충청남도 논산시"], region_basis=basis)
        assert outcome("regions", p, c) == expected


@pytest.mark.parametrize(
    ("self_region", "parent_region", "expected"),
    [(NONSAN, NONSAN, "MET"), (CHEONGJU, GONGJU, "UNMET"), (NONSAN, CHEONGJU, "UNKNOWN")],
)
def test_regions_basis_ambiguous(self_region, parent_region, expected):
    p = profile(self_region=self_region, parent_region=parent_region)
    c = criteria(regions=["충청남도 논산시"], ambiguous_fields=["region_basis"])
    assert outcome("regions", p, c) == expected


def test_regions_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["regions"])
    assert outcome("regions", profile(), c) == "UNKNOWN"


# --- 특수자격 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "required", "expected"),
    [
        (["다자녀"], ["다자녀"], "MET"),
        (["보훈"], ["국가보훈대상자"], "MET"),  # 공지 표현에 자격 낱말이 들어 있음
        (["장애"], ["다자녀", "장애인 가구"], "MET"),  # 하나라도 가지면 충족
        ([], ["다자녀"], "UNMET"),
        (["보훈"], ["다자녀"], "UNMET"),
        (["다자녀"], ["한부모"], "UNKNOWN"),  # 프로필에 없는 종류만 요구
        ([], ["다자녀", "한부모"], "UNKNOWN"),  # 프로필에 없는 종류가 섞여 있음
        (["다자녀"], ["한부모", "다자녀"], "MET"),
        (None, ["다자녀"], "UNKNOWN"),  # 입력 안 함·민감정보 미동의
    ],
)
def test_required_special(flags, required, expected):
    c = criteria(required_special=required)
    assert outcome("required_special", profile(special_flags=flags), c) == expected


def test_required_special_ambiguous_is_unknown():
    c = criteria(ambiguous_fields=["required_special"])
    assert outcome("required_special", profile(), c) == "UNKNOWN"


# --- 등록금 전액 감면 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("waiver", "expected"),
    [("아니오", "MET"), ("예", "UNMET"), (None, "UNKNOWN")],  # null = 모름
)
def test_excludes_full_tuition(waiver, expected):
    c = criteria(excludes_full_tuition=True)
    assert outcome("excludes_full_tuition", profile(full_tuition_waiver=waiver), c) == expected


def test_full_tuition_not_excluded_is_not_compared():
    c = criteria(excludes_full_tuition=False)
    assert outcome("excludes_full_tuition", profile(full_tuition_waiver="예"), c) is None


# --- 학과 (4값) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("제한없음", None),  # 조건 없음: 비교하지 않음(= 충족)
        ("소프트웨어학부 포함", "MET"),  # 모든 사용자를 소프트웨어학부로 봄(4.1)
        ("소프트웨어학부 불포함", "UNMET"),
        ("불명", "UNKNOWN"),
    ],
)
def test_target_department(target, expected):
    c = criteria(target_department=target)
    assert outcome("target_department", profile(), c) == expected


# --- 사업단 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("program", "member", "expected"),
    [
        ("AIBC 양성인재", True, "MET"),
        ("AIBC 양성인재", False, "UNMET"),
        ("AIBC 양성인재", None, "UNKNOWN"),  # 모름
        ("제한없음", False, None),
    ],
)
def test_target_program(program, member, expected):
    c = criteria(target_program=program)
    assert outcome("target_program", profile(aibc_member=member), c) == expected


def test_target_program_ambiguous_is_unknown():
    c = criteria(target_program="AIBC 양성인재", ambiguous_fields=["target_program"])
    assert outcome("target_program", profile(aibc_member=True), c) == "UNKNOWN"


# --- 3단계 종합 판정 ------------------------------------------------------------------


def test_no_conditions_is_eligible():
    result = evaluate(profile(), criteria(), TODAY)
    assert result.result == "ELIGIBLE"
    assert result.checks == []


def test_all_met_is_eligible():
    c = criteria(min_gpa=3.0, target_grades=[3], gender="여", max_income_bracket=8)
    result = evaluate(profile(), c, TODAY)
    assert result.result == "ELIGIBLE"
    assert {check.outcome for check in result.checks} == {"MET"}


def test_unknown_without_unmet_is_conditional():
    c = criteria(min_gpa=3.0, max_income_bracket=8)
    assert evaluate(profile(income_bracket=None), c, TODAY).result == "CONDITIONAL"


def test_extra_conditions_make_conditional():
    c = criteria(min_gpa=3.0, extra_conditions=["직전학기 봉사활동 20시간 이상"])
    result = evaluate(profile(), c, TODAY)
    assert result.result == "CONDITIONAL"
    assert result.extra_conditions == ["직전학기 봉사활동 20시간 이상"]


def test_any_unmet_is_ineligible_even_with_unknown():
    c = criteria(min_gpa=4.0, max_income_bracket=8, extra_conditions=["면접"])
    assert evaluate(profile(income_bracket=None), c, TODAY).result == "INELIGIBLE"


def test_unknown_department_default_is_conditional():
    """AI가 대상 학과를 정하지 못하면(기본값 불명) 확인 필요."""
    c = ScholarshipCriteria(scholarship_name="학과 미상 장학금")
    assert evaluate(profile(), c, TODAY).result == "CONDITIONAL"


def test_checks_follow_table_order_and_carry_evidence():
    c = criteria(
        target_department="소프트웨어학부 포함",
        min_gpa=3.5,
        target_grades=[3],
        regions=["충청남도 논산시"],
        region_basis="부모",
        evidence={
            "min_gpa": "직전학기 평점평균 3.5 이상인 자",
            "region_basis": "부모가 논산시에 거주하는 자",
        },
    )
    checks = evaluate(profile(), c, TODAY).checks
    assert [check.condition for check in checks] == [
        "target_grades",
        "min_gpa",
        "regions",
        "target_department",
    ]
    evidence = {check.condition: check.evidence for check in checks}
    assert evidence["min_gpa"] == "직전학기 평점평균 3.5 이상인 자"
    assert (
        evidence["regions"] == "부모가 논산시에 거주하는 자"
    )  # regions 근거가 없으면 region_basis
    assert evidence["target_grades"] is None


def test_engine_is_pure():
    """matching은 DB·HTTP·LLM 모듈을 import하지 않습니다(5.3, backend CLAUDE.md)."""
    import ast
    from pathlib import Path

    import app.matching

    banned = ("sqlalchemy", "httpx", "app.core.db", "app.models", "app.extraction.extractor")
    for path in Path(app.matching.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                for name in names:
                    assert not name.startswith(banned), f"{path}: {name}"
