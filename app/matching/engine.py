"""3단계 판정 (계획서 5.3). DB·HTTP·LLM에 의존하지 않는 순수 함수입니다.

1. 하나라도 불충족이거나, 사용자가 `extra_conditions`(자격을 좁히는 조건) 중 하나에
   "안 맞아요(불충족)"로 답함 → 지원 불가(`INELIGIBLE`)
2. 그 밖에 알 수 없음이 있거나, 답하지 않은 `extra_conditions`가 있거나,
   **읽지 못한 첨부가 있음**
   → 확인 필요(`CONDITIONAL`). 못 읽은 첨부에 조건이 있을 수 있어
   "지원 가능"이라 단정하지 않습니다(D8).
3. 전부 충족이고 `extra_conditions`에 모두 "맞아요(충족)"로 답함 → 지원 가능(`ELIGIBLE`)

`extra_conditions` 답은 문장마다 `condition_key()`로 찾습니다. 재추출로 문장이 바뀌면
이전 답은 맞는 문장이 없어 쓰이지 않습니다(5.3, 2026-11-12).

공지 조건이 null이면 비교하지 않습니다.
`ambiguous_fields`에 든 조건은 값과 관계없이 알 수 없음입니다(5.2).
`grade_either = true`이고 평점·100점 조건이 모두 있으면 둘을 OR로 묶어 `min_gpa` 한 줄로
판정합니다(`rules.either`, extract_v12). 그 밖에는 두 성적 조건을 따로(AND) 판정합니다.
`extra_conditions` 항목에 `or_fields`가 있으면 "그 필드 조건 또는 이 문장"입니다(extract_v13).
그 필드가 충족이면 내 정보로 판정한 조건에 충족으로 보이고 질문은 묻지 않습니다. 충족이 아니면
(불충족·알 수 없음) 필드 줄은 보이지 않고 질문으로 판정합니다.
필드 불충족만으로는 지원 불가가 되지 않습니다.
`basic_conditions`(학교 조건·품행 등 대부분 충족)와 `notes`(자격이 아닌 안내)는 판정을 바꾸지 않고
화면에 보여 주기만 합니다(extract_v5).
"""

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from app.extraction.schemas import ConditionText, ScholarshipCriteria
from app.matching import rules
from app.matching.describe import describe, grade_either_applies, is_ambiguous
from app.schemas.api.profile import Profile
from app.schemas.api.scholarships import (
    ConditionAnswer,
    ConditionCheck,
    ConditionName,
    ExtraCheck,
    MatchResult,
)

Rule = Callable[[Profile, ScholarshipCriteria, date], rules.Outcome]


@dataclass(frozen=True)
class Evaluation:
    result: MatchResult
    checks: list[ConditionCheck]  # 화면 체크리스트 순서(5.3 표 순서)
    extra_conditions: list[str]
    extra_checks: list[ExtraCheck]  # extra_conditions 순서 그대로, 사용자 답 포함
    basic_conditions: list[str]  # text만
    basic_items: list[ConditionText]  # text와 summary
    notes: list[str]
    # 답하지 않은 extra_conditions 말고도 판정을 막는 것(알 수 없음·못 읽은 첨부)이 있는지
    other_unknown: bool


_SPACES = re.compile(r"\s+")


def condition_key(text: str) -> str:
    """자격을 좁히는 조건 문장의 키: NFC 정규화·공백 하나로 → SHA-256 앞 16자 (5.3)."""
    normalized = _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


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
    ("excludes_new_students", _no_date(rules.excludes_new_students)),
    ("excludes_transfer_students", _no_date(rules.excludes_transfer_students)),
    ("min_admission_year", rules.min_admission_year),
    ("min_remaining_semesters", _no_date(rules.min_remaining_semesters)),
    ("enrollment_status", _no_date(rules.enrollment_status)),
    ("school_excluded", _no_date(rules.school_excluded)),
    ("min_gpa", _no_date(_min_gpa)),
    ("min_score_100", _no_date(_min_score_100)),
    ("min_credits", _no_date(rules.min_credits)),
    ("target_nationality", _no_date(rules.target_nationality)),
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


def _evidence(c: ScholarshipCriteria, condition: str) -> tuple[str | None, str | None]:
    """(근거 문장, 근거 출처). 출처는 "본문" 또는 첨부 파일 이름이고 v3 결과에는 없습니다."""
    for key in EVIDENCE_KEYS.get(condition, (condition,)):
        if text := c.evidence.get(key):  # type: ignore[call-overload]
            return text, c.evidence_source.get(key)  # type: ignore[call-overload]
    return None, None


def _or_field(name: str, either: bool) -> str:
    """`or_fields`의 이름을 판정 줄 이름으로: 평점·100점을 한 줄로 판정하면 min_gpa 줄입니다."""
    return "min_gpa" if either and name == "min_score_100" else name


def evaluate(
    profile: Profile,
    criteria: ScholarshipCriteria,
    today: date,
    *,
    has_unread_attachments: bool = False,
    answers: Mapping[str, ConditionAnswer] | None = None,
) -> Evaluation:
    """프로필과 장학금 조건을 비교합니다.

    `today`는 한국 날짜이고, `apply_end`가 없을 때 나이 기준일로 쓰며
    입학 학년도 조건의 기준일로도 씁니다.
    `has_unread_attachments`는 공지 첨부 중 읽지 못한 것(스캔본·지원 안 하는 형식·실패)이
    있는지입니다. 있으면 지원 가능을 확인 필요로 낮추고, 불충족은 그대로 둡니다.
    `answers`는 사용자가 `extra_conditions`에 답한 값(`condition_key` → YES/NO)입니다.
    """
    checks: list[ConditionCheck] = []
    ambiguous = set(criteria.ambiguous_fields)
    either = grade_either_applies(criteria)
    # 질문과 "또는"으로 묶인 필드. 평점·100점을 한 줄로 판정할 때는 min_gpa 줄이 둘을 대신합니다.
    linked = {
        _or_field(f, either) for item in criteria.extra_conditions for f in item.or_fields or ()
    }
    linked_outcomes: dict[str, rules.Outcome] = {}

    def judge(name: ConditionName, rule: Rule) -> rules.Outcome:
        return rules.UNKNOWN if name in ambiguous else rule(profile, criteria, today)

    for name, rule in RULES:
        if either and name == "min_score_100":
            continue  # min_gpa 줄에서 함께 판정했습니다.
        outcome = judge(name, rule)
        if either and name == "min_gpa":
            outcome = rules.either(outcome, judge("min_score_100", _no_date(_min_score_100)))
        if name in linked:
            linked_outcomes[name] = outcome
            if outcome != rules.MET:
                continue  # 충족이 아니면 줄을 보이지 않고 묶인 질문으로 판정합니다.
        if outcome is not None:
            evidence, source = _evidence(criteria, name)
            if evidence is None and either and name == "min_gpa":
                evidence, source = _evidence(criteria, "min_score_100")
            amb = is_ambiguous(name, criteria)
            checks.append(
                ConditionCheck(
                    condition=name,
                    outcome=outcome,
                    evidence=evidence,
                    evidence_source=source,
                    requirement=describe(name, criteria),
                    ambiguous=amb,
                )
            )

    def satisfied(item: ConditionText) -> bool:
        """묶인 필드 중 하나가 충족이라 묻지 않아도 되는 질문인지."""
        return any(
            linked_outcomes.get(_or_field(f, either)) == rules.MET for f in item.or_fields or ()
        )

    asked = [item for item in criteria.extra_conditions if not satisfied(item)]
    answers = answers or {}
    extra_checks = [
        ExtraCheck(key=key, text=item.text, answer=answers.get(key), summary=item.summary)
        for item in asked
        for key in (condition_key(item.text),)
    ]
    extra_answers = {check.answer for check in extra_checks}

    outcomes = {check.outcome for check in checks}
    other_unknown = rules.UNKNOWN in outcomes or has_unread_attachments
    if rules.UNMET in outcomes or "NO" in extra_answers:
        result: MatchResult = "INELIGIBLE"
    elif other_unknown or None in extra_answers:
        result = "CONDITIONAL"
    else:
        result = "ELIGIBLE"
    return Evaluation(
        result=result,
        checks=checks,
        extra_conditions=[item.text for item in asked],
        extra_checks=extra_checks,
        other_unknown=other_unknown,
        basic_conditions=[item.text for item in criteria.basic_conditions],
        basic_items=list(criteria.basic_conditions),
        notes=list(criteria.notes),
    )
