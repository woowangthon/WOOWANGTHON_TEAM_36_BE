"""3단계 판정 (계획서 5.3). DB·HTTP·LLM에 의존하지 않는 순수 함수입니다.

1. 하나라도 불충족이거나, 사용자가 `extra_conditions`(자격을 좁히는 조건) 중 하나에
   "아니에요"로 답함 → 지원 불가(`INELIGIBLE`)
2. 그 밖에 알 수 없음이 있거나, 답하지 않은 `extra_conditions`가 있거나,
   **읽지 못한 첨부가 있음**
   → 확인 필요(`CONDITIONAL`). 못 읽은 첨부에 조건이 있을 수 있어
   "지원 가능"이라 단정하지 않습니다(D8).
3. 전부 충족이고 `extra_conditions`에 모두 "해당돼요"로 답함 → 지원 가능(`ELIGIBLE`)

`extra_conditions` 답은 문장마다 `condition_key()`로 찾습니다. 재추출로 문장이 바뀌면
이전 답은 맞는 문장이 없어 쓰이지 않습니다(5.3, 2026-11-12).

공지 조건이 null이면 비교하지 않습니다.
`ambiguous_fields`에 든 조건은 값과 관계없이 알 수 없음입니다(5.2).
`basic_conditions`(국적·중복 수혜 등 대부분 충족)와 `notes`(자격이 아닌 안내)는 판정을 바꾸지 않고
화면에 보여 주기만 합니다(extract_v5).
"""

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from app.extraction.schemas import ScholarshipCriteria
from app.matching import rules
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
    basic_conditions: list[str]
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
    ("min_remaining_semesters", _no_date(rules.min_remaining_semesters)),
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


def _evidence(c: ScholarshipCriteria, condition: str) -> tuple[str | None, str | None]:
    """(근거 문장, 근거 출처). 출처는 "본문" 또는 첨부 파일 이름이고 v3 결과에는 없습니다."""
    for key in EVIDENCE_KEYS.get(condition, (condition,)):
        if text := c.evidence.get(key):  # type: ignore[call-overload]
            return text, c.evidence_source.get(key)  # type: ignore[call-overload]
    return None, None


def evaluate(
    profile: Profile,
    criteria: ScholarshipCriteria,
    today: date,
    *,
    has_unread_attachments: bool = False,
    answers: Mapping[str, ConditionAnswer] | None = None,
) -> Evaluation:
    """프로필과 장학금 조건을 비교합니다.

    `today`는 한국 날짜이고, `apply_end`가 없을 때 나이 기준일로 씁니다.
    `has_unread_attachments`는 공지 첨부 중 읽지 못한 것(스캔본·지원 안 하는 형식·실패)이
    있는지입니다. 있으면 지원 가능을 확인 필요로 낮추고, 불충족은 그대로 둡니다.
    `answers`는 사용자가 `extra_conditions`에 답한 값(`condition_key` → YES/NO)입니다.
    """
    checks: list[ConditionCheck] = []
    ambiguous = set(criteria.ambiguous_fields)
    for name, rule in RULES:
        outcome = rules.UNKNOWN if name in ambiguous else rule(profile, criteria, today)
        if outcome is not None:
            evidence, source = _evidence(criteria, name)
            checks.append(
                ConditionCheck(
                    condition=name, outcome=outcome, evidence=evidence, evidence_source=source
                )
            )

    answers = answers or {}
    extra_checks = [
        ExtraCheck(key=key, text=text, answer=answers.get(key))
        for text in criteria.extra_conditions
        for key in (condition_key(text),)
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
        extra_conditions=list(criteria.extra_conditions),
        extra_checks=extra_checks,
        other_unknown=other_unknown,
        basic_conditions=list(criteria.basic_conditions),
        notes=list(criteria.notes),
    )
