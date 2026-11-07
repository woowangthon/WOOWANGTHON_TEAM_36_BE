"""3단계 판정 (계획서 5.3). DB·HTTP·LLM에 의존하지 않는 순수 함수입니다.

1. 하나라도 불충족 → 지원 불가(`INELIGIBLE`)
2. 불충족은 없지만 알 수 없음이 있거나 `extra_conditions`가 있음 → 확인 필요(`CONDITIONAL`)
3. 전부 충족 → 지원 가능(`ELIGIBLE`)

공지 조건이 null이면 비교하지 않습니다.
`ambiguous_fields`에 든 조건은 값과 관계없이 알 수 없음입니다(5.2).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.extraction.schemas import ScholarshipCriteria
from app.matching import rules
from app.schemas.api.profile import Profile
from app.schemas.api.scholarships import ConditionCheck, ConditionName, MatchResult

Rule = Callable[[Profile, ScholarshipCriteria, date], rules.Outcome]


@dataclass(frozen=True)
class Evaluation:
    result: MatchResult
    checks: list[ConditionCheck]  # 화면 체크리스트 순서(5.3 표 순서)
    extra_conditions: list[str]


def _no_date(rule: Callable[[Profile, ScholarshipCriteria], rules.Outcome]) -> Rule:
    return lambda p, c, _today: rule(p, c)


def _regions(p: Profile, c: ScholarshipCriteria) -> rules.Outcome:
    return rules.regions(p, c, basis_ambiguous="region_basis" in c.ambiguous_fields)


def _min_gpa(p: Profile, c: ScholarshipCriteria) -> rules.Outcome:
    return rules.min_gpa(p, c, basis_ambiguous="grade_basis" in c.ambiguous_fields)


def _min_score_100(p: Profile, c: ScholarshipCriteria) -> rules.Outcome:
    return rules.min_score_100(p, c, basis_ambiguous="grade_basis" in c.ambiguous_fields)


# 5.3 표 순서. 조건 이름 = 추출 필드 이름이고, 그 필드가 ambiguous_fields에 있으면 알 수 없음.
# (region_basis·grade_basis만 모호한 경우는 rules.regions·min_gpa·min_score_100이 처리)
RULES: tuple[tuple[ConditionName, Rule], ...] = (
    ("target_grades", _no_date(rules.target_grades)),
    ("excludes_extra_semester", _no_date(rules.excludes_extra_semester)),
    ("enrollment_status", _no_date(rules.enrollment_status)),
    ("min_gpa", _no_date(_min_gpa)),
    ("min_score_100", _no_date(_min_score_100)),
    ("min_credits", _no_date(rules.min_credits)),
    ("gender", _no_date(rules.gender)),
    ("max_age", rules.max_age),
    ("max_income_bracket", _no_date(rules.max_income_bracket)),
    ("regions", _no_date(_regions)),
    ("required_special", _no_date(rules.required_special)),
    ("excludes_full_tuition", _no_date(rules.excludes_full_tuition)),
    ("target_department", _no_date(rules.target_department)),
    ("target_program", _no_date(rules.target_program)),
)

# 근거 문장을 찾을 evidence 키 (조건 이름과 다를 때만)
EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "regions": ("regions", "region_basis"),
    "min_gpa": ("min_gpa", "grade_basis"),
    "min_score_100": ("min_score_100", "grade_basis"),
}


def _evidence(c: ScholarshipCriteria, condition: str) -> str | None:
    for key in EVIDENCE_KEYS.get(condition, (condition,)):
        if text := c.evidence.get(key):  # type: ignore[call-overload]
            return text
    return None


def evaluate(profile: Profile, criteria: ScholarshipCriteria, today: date) -> Evaluation:
    """프로필과 장학금 조건을 비교합니다.

    `today`는 한국 날짜이고, `apply_end`가 없을 때 나이 기준일로 씁니다.
    """
    checks: list[ConditionCheck] = []
    ambiguous = set(criteria.ambiguous_fields)
    for name, rule in RULES:
        outcome = rules.UNKNOWN if name in ambiguous else rule(profile, criteria, today)
        if outcome is not None:
            checks.append(
                ConditionCheck(condition=name, outcome=outcome, evidence=_evidence(criteria, name))
            )

    outcomes = {check.outcome for check in checks}
    if rules.UNMET in outcomes:
        result: MatchResult = "INELIGIBLE"
    elif rules.UNKNOWN in outcomes or criteria.extra_conditions:
        result = "CONDITIONAL"
    else:
        result = "ELIGIBLE"
    return Evaluation(
        result=result, checks=checks, extra_conditions=list(criteria.extra_conditions)
    )
