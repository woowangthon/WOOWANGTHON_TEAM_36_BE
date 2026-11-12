"""골든셋 채점 (계획서 10장 "골든셋 정답률 계산").

- 공지마다 먼저 장학금 개수가 정답과 같은지 봅니다(개수 일치율은 따로 출력).
- 주요 필드를 **필드별 완전 일치**로 채점합니다.
  - 공지 단위: `notice_kind` (공지 1건에 1개)
  - 장학금 단위: `apply_end`, `min_gpa`, `target_grades`, `max_income_bracket`, `target_department`
    (정답 장학금 1개마다 1개씩)
- 정답률 = 맞은 필드 수 ÷ 전체 필드 수. 분모는 **정답** 기준이므로, 모델이 정답에 없는 장학금을
  더 뽑아도 분모가 늘지 않습니다(개수 불일치로만 드러남).
- 정답 장학금과 예측 장학금은 이름이 가장 비슷한 것끼리 짝짓습니다(계획서에 짝짓기 방법이 없어
  T16에서 정함). 짝이 없는 정답 장학금의 필드는 모두 틀린 것으로 셉니다.
- 추출이 실패한 공지(`ExtractionError`)는 그 공지의 모든 필드를 틀린 것으로 셉니다.
- 보조 지표 `needs_check`(extract_v5): 장학금마다 `extra_conditions`(자격을 좁히는 조건)가
  있는지 없는지가 정답과 같은지 봅니다. 판정의 "확인 필요"를 정하는 값이라 따로 세고,
  버전끼리 비교할 수 있게 전체 정답률(`correct`·`total`)에는 넣지 않습니다.
- 보조 지표 `excludes_new_students`·`min_remaining_semesters`(extract_v6): 값이 정답과 같은지
  봅니다. 같은 이유로 전체 정답률에는 넣지 않습니다.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.extraction.schemas import ExtractedCriteria, ScholarshipCriteria

NOTICE_FIELDS = ("notice_kind",)
SCHOLARSHIP_FIELDS = (
    "apply_end",
    "min_gpa",
    "target_grades",
    "max_income_bracket",
    "target_department",
)
FIELDS = NOTICE_FIELDS + SCHOLARSHIP_FIELDS
# 전체 정답률에 넣지 않는 보조 지표. needs_check는 아래에서 따로 계산합니다.
AUX_VALUE_FIELDS = ("excludes_new_students", "min_remaining_semesters")
AUX_FIELDS = ("needs_check", *AUX_VALUE_FIELDS)
TARGET_ACCURACY = 0.90


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class NoticeScore:
    notice_id: str
    count_expected: int
    count_predicted: int | None  # 추출 실패면 None
    fields: dict[str, FieldScore] = field(default_factory=dict)
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count_match(self) -> bool:
        return self.count_predicted == self.count_expected


def normalize(field_name: str, value: Any) -> Any:
    """완전 일치 비교용 정규화. 학년 목록은 순서를 보지 않습니다."""
    if field_name == "target_grades" and value is not None:
        return sorted(set(value))
    return value


def pair_scholarships(
    expected: list[ScholarshipCriteria], predicted: list[ScholarshipCriteria]
) -> list[tuple[ScholarshipCriteria, ScholarshipCriteria | None]]:
    """정답 장학금마다 이름이 가장 비슷한 예측 장학금을 짝짓습니다(한 번씩만 씀)."""
    candidates = [
        (_similarity(e.scholarship_name, p.scholarship_name), ei, pi)
        for ei, e in enumerate(expected)
        for pi, p in enumerate(predicted)
    ]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    matched: dict[int, int] = {}
    used: set[int] = set()
    for _, ei, pi in candidates:
        if ei not in matched and pi not in used:
            matched[ei] = pi
            used.add(pi)
    return [(e, predicted[matched[ei]] if ei in matched else None) for ei, e in enumerate(expected)]


def score_notice(
    notice_id: str, expected: ExtractedCriteria, predicted: ExtractedCriteria | None
) -> NoticeScore:
    score = NoticeScore(
        notice_id=notice_id,
        count_expected=len(expected.scholarships),
        count_predicted=None if predicted is None else len(predicted.scholarships),
        fields={name: FieldScore() for name in FIELDS + AUX_FIELDS},
    )

    got_kind = None if predicted is None else predicted.notice_kind
    _check(score, "notice_kind", None, expected.notice_kind, got_kind)

    pairs = pair_scholarships(expected.scholarships, predicted.scholarships if predicted else [])
    for expected_one, predicted_one in pairs:
        for name in SCHOLARSHIP_FIELDS + AUX_VALUE_FIELDS:
            want = getattr(expected_one, name)
            got = None if predicted_one is None else getattr(predicted_one, name)
            if predicted_one is None:
                score.fields[name].total += 1
                score.mismatches.append(
                    {
                        "scholarship": expected_one.scholarship_name,
                        "field": name,
                        "expected": _json(want),
                        "got": "(장학금 없음)",
                    }
                )
                continue
            _check(score, name, expected_one.scholarship_name, want, got)
        got_check = None if predicted_one is None else bool(predicted_one.extra_conditions)
        _check(
            score,
            "needs_check",
            expected_one.scholarship_name,
            bool(expected_one.extra_conditions),
            got_check,
        )
    return score


def summarize(scores: list[NoticeScore]) -> dict[str, Any]:
    totals = {name: FieldScore() for name in FIELDS + AUX_FIELDS}
    for notice in scores:
        for name, value in notice.fields.items():
            totals[name].correct += value.correct
            totals[name].total += value.total
    correct = sum(totals[name].correct for name in FIELDS)
    total = sum(totals[name].total for name in FIELDS)
    accuracy = correct / total if total else 0.0
    return {
        "notices": len(scores),
        "failed": sum(1 for s in scores if s.count_predicted is None),
        "count_match": sum(1 for s in scores if s.count_match),
        "fields": {
            name: {"correct": v.correct, "total": v.total, "accuracy": round(v.accuracy, 4)}
            for name, v in totals.items()
        },
        "correct": correct,
        "total": total,
        "accuracy": round(accuracy, 4),
        "target": TARGET_ACCURACY,
        "gap": round(accuracy - TARGET_ACCURACY, 4),
    }


def _check(score: NoticeScore, name: str, scholarship: str | None, want: Any, got: Any) -> None:
    result = score.fields[name]
    result.total += 1
    if normalize(name, want) == normalize(name, got):
        result.correct += 1
        return
    score.mismatches.append(
        {"scholarship": scholarship, "field": name, "expected": _json(want), "got": _json(got)}
    )


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, "".join(a.split()), "".join(b.split())).ratio()


def _json(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value
