"""알림 대상 선정 (계획서 7.4). DB 없이 순수 함수만 확인합니다."""

from datetime import UTC, datetime, timedelta

import pytest

from app.extraction.schemas import ScholarshipCriteria
from app.matching.engine import condition_key
from app.notifications.select import Candidate, Recipient, select_items
from app.parsers.dates import kst
from app.schemas.api.profile import Profile

NOW = kst(2026, 11, 11, 9, 0)
PROFILE = {
    "name": "길동",
    "gender": "여",
    "birth_year": 2004,
    "gpa": 3.6,
    "score_100": 88.0,
    "credits": 15,
    "gpa_total": 3.5,
    "score_100_total": 87.0,
    "completed_semesters": 5,
    "enrollment_status": "재학",
    "aibc_member": False,
    "income_bracket": None,
    "self_region": None,
    "parent_region": None,
    "special_flags": None,
    "full_tuition_waiver": None,
}
NO_LIMIT = {"target_department": "제한없음"}
ELIGIBLE = {**NO_LIMIT, "min_gpa": 3.0}
CONDITIONAL = {**NO_LIMIT, "max_income_bracket": 8}  # 프로필 소득분위 모름
INELIGIBLE = {**NO_LIMIT, "min_gpa": 4.0}
OLD = NOW - timedelta(days=30)


def cand(scholarship_id: int, criteria: dict = ELIGIBLE, **changes) -> Candidate:
    values = {
        "scholarship_id": scholarship_id,
        "name": f"장학금{scholarship_id}",
        "status": "OPEN",
        "notice_kind": "모집",
        "apply_end": None,
        "created_at": NOW - timedelta(hours=1),
        "criteria": ScholarshipCriteria.model_validate(
            {"scholarship_name": f"장학금{scholarship_id}", **criteria}
        ),
        "has_unread": False,
    }
    return Candidate(**{**values, **changes})


def recipient(bookmarked=(), applied=(), sent=()) -> Recipient:
    return Recipient(
        user_id=1,
        profile=Profile.model_validate(PROFILE),
        bookmarked=frozenset(bookmarked),
        applied=frozenset(applied),
        sent=frozenset(sent),
    )


def picked(items) -> list[tuple[int, str]]:
    return [(i.scholarship_id, i.kind) for i in items]


# --- 새 장학금 ------------------------------------------------------------------


def test_new_match_includes_eligible_and_conditional_only():
    cands = [cand(1, ELIGIBLE), cand(2, CONDITIONAL), cand(3, INELIGIBLE)]

    items = select_items(recipient(), cands, NOW)

    assert [(i.scholarship_id, i.kind, i.result) for i in items] == [
        (1, "NEW_MATCH", "ELIGIBLE"),
        (2, "NEW_MATCH", "CONDITIONAL"),
    ]


def test_unread_attachment_lowers_eligible_to_conditional():
    items = select_items(recipient(), [cand(1, ELIGIBLE, has_unread=True)], NOW)

    assert [i.result for i in items] == ["CONDITIONAL"]


def test_new_match_uses_condition_answers():
    """대시보드와 같은 판정: 조건에 ✗로 답했으면 알리지 않고, 모두 ✓면 지원 가능으로 알립니다."""
    extra = {**ELIGIBLE, "extra_conditions": ["봉사활동 20시간 이상"]}
    key = condition_key("봉사활동 20시간 이상")
    base = recipient()
    answered = Recipient(
        user_id=base.user_id,
        profile=base.profile,
        bookmarked=frozenset(),
        applied=frozenset(),
        sent=frozenset(),
        answers={1: {key: "YES"}, 2: {key: "NO"}},
    )
    cands = [cand(1, extra), cand(2, extra), cand(3, extra)]

    items = select_items(answered, cands, NOW)

    assert [(i.scholarship_id, i.result) for i in items] == [
        (1, "ELIGIBLE"),
        (3, "CONDITIONAL"),  # 답하지 않음
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"created_at": NOW - timedelta(hours=49)},
        {"status": "CLOSED"},
        {"notice_kind": "결과"},
        {"notice_kind": None},
    ],
)
def test_new_match_excludes_old_closed_and_unlisted(change):
    assert select_items(recipient(), [cand(1, **change)], NOW) == []


def test_new_match_includes_always_and_criteria_notice():
    items = select_items(recipient(), [cand(1, status="ALWAYS", notice_kind="기준안내")], NOW)

    assert picked(items) == [(1, "NEW_MATCH")]


def test_new_match_excludes_applied_and_already_sent():
    who = recipient(applied=[1], sent=[(2, "NEW_MATCH", None)])

    assert picked(select_items(who, [cand(1), cand(2), cand(3)], NOW)) == [(3, "NEW_MATCH")]


# --- 마감 임박 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deadline", "kind"),
    [
        (kst(2026, 11, 14, 23, 59), "DEADLINE_D3"),
        (kst(2026, 11, 14, 0, 0), "DEADLINE_D3"),  # 서울 날짜 경계
        (kst(2026, 11, 12, 23, 59), "DEADLINE_D1"),
        (kst(2026, 11, 12, 0, 0), "DEADLINE_D1"),
        (kst(2026, 11, 13, 23, 59), None),  # 2일 전
        (kst(2026, 11, 15, 0, 0), None),  # 4일 전
        (kst(2026, 11, 11, 23, 59), None),  # 당일
    ],
)
def test_deadline_days(deadline, kind):
    who = recipient(bookmarked=[1])

    items = select_items(who, [cand(1, created_at=OLD, apply_end=deadline)], NOW)

    assert picked(items) == ([(1, kind)] if kind else [])


def test_deadline_uses_seoul_date_for_utc_values():
    deadline = datetime(2026, 11, 14, 14, 59, tzinfo=UTC)  # 서울 11-14 23:59
    now = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)  # 서울 11-11 09:00
    who = recipient(bookmarked=[1])

    items = select_items(who, [cand(1, created_at=OLD, apply_end=deadline)], now)

    assert picked(items) == [(1, "DEADLINE_D3")]


@pytest.mark.parametrize(
    ("bookmarked", "applied", "status"),
    [
        ((), (), "OPEN"),
        ((), (1,), "OPEN"),
        ((1,), (1,), "OPEN"),  # 관심이어도 이미 신청완료면 알리지 않음 (7.4, 2026-11-14)
        ((1,), (), "CLOSED"),
    ],
)
def test_deadline_requires_bookmark_not_applied_and_open(bookmarked, applied, status):
    c = cand(1, created_at=OLD, apply_end=kst(2026, 11, 14, 23, 59), status=status)

    assert select_items(recipient(bookmarked=bookmarked, applied=applied), [c], NOW) == []


def test_deadline_is_sent_regardless_of_match_result():
    who = recipient(bookmarked=[1])
    c = cand(1, INELIGIBLE, created_at=OLD, apply_end=kst(2026, 11, 12, 23, 59))

    items = select_items(who, [c], NOW)

    assert [(i.kind, i.result) for i in items] == [("DEADLINE_D1", None)]


def test_same_deadline_is_not_sent_twice():
    deadline = kst(2026, 11, 14, 23, 59)
    who = recipient(bookmarked=[1], sent=[(1, "DEADLINE_D3", deadline)])

    assert select_items(who, [cand(1, created_at=OLD, apply_end=deadline)], NOW) == []


def test_sent_deadline_matches_across_timezones():
    deadline = kst(2026, 11, 14, 23, 59)
    as_utc = deadline.astimezone(UTC)  # DB는 UTC로 돌려줌
    who = recipient(bookmarked=[1], sent=[(1, "DEADLINE_D3", as_utc)])

    assert select_items(who, [cand(1, created_at=OLD, apply_end=deadline)], NOW) == []


def test_extended_deadline_notifies_again():
    old_deadline = kst(2026, 11, 10, 23, 59)
    who = recipient(bookmarked=[1], sent=[(1, "DEADLINE_D3", old_deadline)])
    c = cand(1, created_at=OLD, apply_end=kst(2026, 11, 14, 23, 59))

    assert picked(select_items(who, [c], NOW)) == [(1, "DEADLINE_D3")]


# --- 묶기·순서 ------------------------------------------------------------------


def test_bookmarked_new_scholarship_appears_once_as_deadline():
    who = recipient(bookmarked=[1])

    items = select_items(who, [cand(1, apply_end=kst(2026, 11, 14, 23, 59))], NOW)

    assert picked(items) == [(1, "DEADLINE_D3")]


def test_order_is_deadlines_first_then_new_by_deadline():
    who = recipient(bookmarked=[1, 2])
    cands = [
        cand(5),  # 새 장학금, 마감 없음
        cand(4, apply_end=kst(2026, 11, 30, 23, 59)),
        cand(3, apply_end=kst(2026, 11, 20, 23, 59)),
        cand(2, created_at=OLD, apply_end=kst(2026, 11, 14, 23, 59)),
        cand(1, created_at=OLD, apply_end=kst(2026, 11, 12, 23, 59)),
    ]

    assert picked(select_items(who, cands, NOW)) == [
        (1, "DEADLINE_D1"),
        (2, "DEADLINE_D3"),
        (3, "NEW_MATCH"),
        (4, "NEW_MATCH"),
        (5, "NEW_MATCH"),
    ]
