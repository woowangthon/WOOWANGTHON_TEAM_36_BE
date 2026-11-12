"""알림 대상 선정 (계획서 7.4). DB·HTTP·LLM에 의존하지 않는 순수 함수입니다.

수신자 한 명과 후보 장학금 목록을 받아 그 사람에게 보낼 항목을 돌려줍니다.

- 새 장학금(`NEW_MATCH`): 최근 48시간 안에 처음 저장됐고 모집 중이며, 대시보드 목록에 나오는
  공지 종류이고, 규칙 엔진 판정(조건 답 포함, 5.3)이 지원 불가가 아닌 것.
  신청완료로 표시한 것과 이미 알린 것은 뺍니다.
- 마감 임박(`DEADLINE_D3`, `DEADLINE_D1`): 관심 표시한 모집 중 장학금의 마감일이 서울 날짜로
  3일 뒤 또는 1일 뒤인 것. 같은 마감으로 이미 알렸으면 빼고, 마감이 연장되면 다시 알립니다.
  관심 장학금은 판정과 관계없이 알립니다.
- 한 장학금이 둘 다 해당하면 마감 항목만 넣습니다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.extraction.schemas import ScholarshipCriteria
from app.matching.engine import evaluate
from app.parsers.dates import to_kst
from app.schemas.api.profile import Profile
from app.schemas.api.scholarships import ConditionAnswer, MatchResult

NEW_MATCH = "NEW_MATCH"
NEW_WINDOW = timedelta(hours=48)  # 하루 발송이 통째로 실패해도 다음 날 보낼 수 있게 이틀
NEW_NOTICE_KINDS = ("모집", "기준안내")  # 대시보드 목록과 같은 기준
NEW_STATUSES = ("OPEN", "ALWAYS")
DEADLINE_KINDS = {3: "DEADLINE_D3", 1: "DEADLINE_D1"}  # 남은 날 수 → 알림 종류

# (scholarship_id, kind, apply_end). NEW_MATCH의 apply_end는 None
SentKey = tuple[int, str, datetime | None]


@dataclass(frozen=True)
class Candidate:
    scholarship_id: int
    name: str
    status: str
    notice_kind: str | None
    apply_end: datetime | None
    created_at: datetime
    criteria: ScholarshipCriteria
    has_unread: bool  # 공지에 읽지 못한 첨부가 있음(판정을 확인 필요로 낮춤)


@dataclass(frozen=True)
class Recipient:
    user_id: int
    profile: Profile
    states: dict[int, str]  # scholarship_id -> BOOKMARKED | APPLIED
    sent: frozenset[SentKey]
    # scholarship_id -> {condition_key: 답}. 자격을 좁히는 조건에 사용자가 답한 값(5.3)
    answers: Mapping[int, Mapping[str, ConditionAnswer]] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    scholarship_id: int
    name: str
    kind: str  # NEW_MATCH | DEADLINE_D3 | DEADLINE_D1
    apply_end: datetime | None
    result: MatchResult | None  # 마감 알림은 판정하지 않으므로 None


def _deadline_kind(recipient: Recipient, c: Candidate, now: datetime) -> str | None:
    if recipient.states.get(c.scholarship_id) != "BOOKMARKED":
        return None
    if c.status != "OPEN" or c.apply_end is None:
        return None
    days_left = (to_kst(c.apply_end).date() - to_kst(now).date()).days
    kind = DEADLINE_KINDS.get(days_left)
    if kind is None or (c.scholarship_id, kind, c.apply_end) in recipient.sent:
        return None
    return kind


def _new_match(recipient: Recipient, c: Candidate, now: datetime) -> MatchResult | None:
    if c.created_at <= now - NEW_WINDOW:
        return None
    if c.status not in NEW_STATUSES or c.notice_kind not in NEW_NOTICE_KINDS:
        return None
    if recipient.states.get(c.scholarship_id) == "APPLIED":
        return None
    if (c.scholarship_id, NEW_MATCH, None) in recipient.sent:
        return None
    result = evaluate(
        recipient.profile,
        c.criteria,
        to_kst(now).date(),
        has_unread_attachments=c.has_unread,
        answers=recipient.answers.get(c.scholarship_id),
    ).result
    return None if result == "INELIGIBLE" else result


def _by_deadline(item: Item) -> tuple[bool, datetime | None, int]:
    """마감 빠른 순, 마감 없는 것은 뒤."""
    return (item.apply_end is None, item.apply_end, item.scholarship_id)


def select_items(
    recipient: Recipient, candidates: Sequence[Candidate], now: datetime
) -> list[Item]:
    """수신자에게 보낼 항목. 마감 임박 → 새 장학금 순이고, 각각 마감 빠른 순입니다."""
    deadlines: list[Item] = []
    new: list[Item] = []
    for c in candidates:
        if kind := _deadline_kind(recipient, c, now):
            deadlines.append(Item(c.scholarship_id, c.name, kind, c.apply_end, None))
        elif result := _new_match(recipient, c, now):
            new.append(Item(c.scholarship_id, c.name, NEW_MATCH, c.apply_end, result))
    return sorted(deadlines, key=_by_deadline) + sorted(new, key=_by_deadline)
