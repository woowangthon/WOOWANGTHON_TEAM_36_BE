"""맞춤 목록·상세·내 상태 (계획서 5.3, 7.1, 7.2, 9.8).

- 판정은 요청마다 규칙 엔진(`app.matching`)으로 계산합니다. LLM을 부르지 않습니다(5.3).
- 목록은 장학금·공지·출처·내 상태를 한 번의 JOIN 쿼리로 읽어 공지 수와 관계없이 쿼리 수가
  같습니다(9.8 N+1 금지).
- 기본 목록은 `OPEN`·`ALWAYS`만, 공지가 `모집`·`기준안내`인 것만 보여 줍니다.
- 공지에 읽지 못한 첨부가 있으면 판정을 "지원 가능"이 아니라 "확인 필요"로 낮춥니다(D8).
  목록은 같은 쿼리의 EXISTS 열로 확인해 쿼리 수를 늘리지 않습니다.
  `state`를 주면 내 장학금 화면용으로 마감(`CLOSED`)된 것도 포함합니다.
- 자격을 좁히는 조건(`extra_conditions`)에 사용자가 답한 값(✓/✗)을 판정에 반영합니다(5.3).
  답은 암호화해 저장하고 목록·상세 모두 쿼리 한 번으로 읽습니다.
- 프로필 값과 조건 답은 로그에 남기지 않습니다(4.2). 사용자는 `user_id`로만 기록합니다.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import and_, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.profile import profile_from_row
from app.core.crypto import decrypt, encrypt, field_aad
from app.core.db import get_db
from app.core.logging import log_event
from app.core.security import ConsentedUser
from app.extraction.schemas import BenefitType, ScholarshipCriteria
from app.matching.engine import Evaluation, evaluate
from app.matching.engine import condition_key as key_of
from app.models import (
    Notice,
    NoticeAttachment,
    Scholarship,
    Source,
    User,
    UserConditionAnswer,
    UserProfile,
    UserScholarshipState,
)
from app.models.notice import UNREAD_ATTACHMENT_STATUSES
from app.parsers.dates import now_kst, to_kst
from app.schemas.api.profile import Profile
from app.schemas.api.scholarships import (
    ConditionAnswer,
    ConditionAnswerRequest,
    ConditionAnswerResponse,
    ConditionTextOut,
    MatchResult,
    NoticeAttachmentText,
    NoticeRef,
    OpenCondition,
    ResultFilter,
    ScholarshipDetail,
    ScholarshipListResponse,
    ScholarshipSummary,
    SiblingScholarship,
    SortOrder,
    SourceFilter,
    StateFilter,
    StateRequest,
    StateResponse,
    UnreadAttachment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scholarships", tags=["scholarships"])

DB = Annotated[Session, Depends(get_db)]
AUTH_ERRORS = {401: {"description": "로그인 필요"}, 403: {"description": "개인정보 동의 필요"}}
NOT_FOUND = {404: {"description": "장학금 없음 또는 프로필 미입력"}}

LISTED_STATUSES = ("OPEN", "ALWAYS")
LISTED_NOTICE_KINDS = ("모집", "기준안내")  # 결과·기타 공지는 장학금 목록에 넣지 않음(T23)
RESULT_FILTERS: dict[ResultFilter, tuple[MatchResult, ...]] = {
    "eligible": ("ELIGIBLE",),
    "conditional": ("CONDITIONAL",),
    "all": ("ELIGIBLE", "CONDITIONAL", "INELIGIBLE"),
}
STATE_FILTERS = {"bookmarked": "BOOKMARKED", "applied": "APPLIED"}
# 등록금성·생활비성 필터에 '혼합'도 포함합니다(둘 다 해당).
BENEFIT_FILTERS: dict[str, tuple[str, ...]] = {
    "등록금성": ("등록금성", "혼합"),
    "생활비성": ("생활비성", "혼합"),
    "혼합": ("혼합",),
}


def _kst(value: datetime | None) -> datetime | None:
    """DB는 UTC로 돌려주므로 응답은 한국 시간(+09:00)으로 바꿉니다."""
    return to_kst(value) if value is not None else None


def _not_found(detail: str = "장학금을 찾을 수 없습니다") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _load_profile(db: Session, user: User) -> Profile:
    """판정에 쓸 프로필(복호화). 아직 입력하지 않았으면 404(프론트는 프로필 입력 화면으로 이동)."""
    row = db.get(UserProfile, user.id)
    if row is None:
        raise _not_found("프로필을 입력하세요")
    return profile_from_row(row)


def _answer_aad(user_id: int, scholarship_id: int, key: str) -> bytes:
    """답 암호문을 사용자·장학금·조건에 묶습니다. 다른 행으로 옮기면 복호화가 실패합니다."""
    return field_aad(user_id, f"cond:{scholarship_id}:{key}")


def load_condition_answers(
    db: Session, user_ids: Iterable[int] | None, scholarship_ids: Iterable[int] | None
) -> dict[int, dict[int, dict[str, ConditionAnswer]]]:
    """{user_id: {scholarship_id: {condition_key: 답}}}. 쿼리 한 번입니다.

    `None`이면 그 기준으로 거르지 않습니다. 복호화에 실패한 답은 답하지 않은 것('?')으로 둡니다.
    """
    query = select(UserConditionAnswer)
    if user_ids is not None:
        query = query.where(UserConditionAnswer.user_id.in_(list(user_ids)))
    if scholarship_ids is not None:
        query = query.where(UserConditionAnswer.scholarship_id.in_(list(scholarship_ids)))
    answers: dict[int, dict[int, dict[str, ConditionAnswer]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in db.scalars(query):
        try:
            value = decrypt(
                row.enc_answer, aad=_answer_aad(row.user_id, row.scholarship_id, row.condition_key)
            )
        except Exception as error:
            log_event(
                logger,
                "조건 답을 읽지 못해 건너뜀",
                logging.ERROR,
                user_id=row.user_id,
                error=type(error).__name__,
            )
            continue
        if value in ("YES", "NO"):
            answers[row.user_id][row.scholarship_id][row.condition_key] = value
    return answers


def _summary(
    s: Scholarship, notice: Notice, source: Source, state: str | None, evaluation: Evaluation
) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "display_name": s.display_name or s.name,
        "source_code": source.code,
        "source_name": source.name,
        "is_internal": source.is_internal,
        "benefit_amount_text": s.benefit_amount_text,
        "benefit_type": s.benefit_type,
        "apply_end": _kst(s.apply_end),
        "status": s.status,
        "deadline_conflict": s.deadline_conflict,
        "posted_at": _kst(notice.posted_at),
        "result": evaluation.result,
        "my_state": state,
        "open_conditions": [
            OpenCondition(key=c.key, text=c.text, summary=c.summary)
            for c in evaluation.extra_checks
            if c.answer is None
        ],
        "other_unknown": evaluation.other_unknown,
    }


def unread_attachments_exist():
    """공지에 읽지 못한 첨부가 있는지(상관 EXISTS). 목록 쿼리 한 번에 함께 읽습니다."""
    return (
        exists()
        .where(
            NoticeAttachment.notice_id == Notice.id,
            NoticeAttachment.status.in_(UNREAD_ATTACHMENT_STATUSES),
        )
        .label("has_unread")
    )


def _base_query(user_id: int):
    """장학금 + 공지 + 출처 + 내 상태 + 못 읽은 첨부 여부를 한 번에 읽는 쿼리."""
    return (
        select(Scholarship, Notice, Source, UserScholarshipState.state, unread_attachments_exist())
        .join(Notice, Scholarship.notice_id == Notice.id)
        .join(Source, Notice.source_code == Source.code)
        .outerjoin(
            UserScholarshipState,
            and_(
                UserScholarshipState.scholarship_id == Scholarship.id,
                UserScholarshipState.user_id == user_id,
            ),
        )
    )


@router.get("", response_model=ScholarshipListResponse, responses={**AUTH_ERRORS, **NOT_FOUND})
def list_scholarships(
    user: ConsentedUser,
    db: DB,
    result: ResultFilter = "all",
    sort: SortOrder = "deadline",
    source: SourceFilter | None = None,
    benefit_type: BenefitType | None = None,
    state: StateFilter | None = None,
) -> ScholarshipListResponse:
    """장학금 단위 맞춤 목록(판정 결과·내 상태 포함).

    기본은 CLOSED를 빼고, state를 주면 내 장학금 화면용으로 마감된 것도 포함합니다.
    정렬: deadline = 마감 임박순(마감일 없는 상시는 뒤), latest = 게시일 최신순.
    """
    profile = _load_profile(db, user)
    query = _base_query(user.id).where(Notice.notice_kind.in_(LISTED_NOTICE_KINDS))
    if state is None:
        query = query.where(Scholarship.status.in_(LISTED_STATUSES))
    else:
        query = query.where(UserScholarshipState.state == STATE_FILTERS[state])
    if source is not None:
        query = query.where(Source.is_internal.is_(source == "internal"))
    if benefit_type is not None:
        query = query.where(Scholarship.benefit_type.in_(BENEFIT_FILTERS[benefit_type]))
    if sort == "deadline":
        query = query.order_by(
            Scholarship.apply_end.asc().nulls_last(), Notice.posted_at.desc(), Scholarship.id
        )
    else:
        query = query.order_by(Notice.posted_at.desc(), Scholarship.id)

    today = now_kst().date()
    wanted = RESULT_FILTERS[result]
    answers = load_condition_answers(db, [user.id], None).get(user.id, {})
    items = []
    for s, notice, src, my_state, has_unread in db.execute(query):
        criteria = ScholarshipCriteria.model_validate(s.criteria)
        evaluation = evaluate(
            profile, criteria, today, has_unread_attachments=has_unread, answers=answers.get(s.id)
        )
        if evaluation.result in wanted:
            items.append(
                ScholarshipSummary.model_validate(_summary(s, notice, src, my_state, evaluation))
            )
    return ScholarshipListResponse(items=items)


@router.get(
    "/{scholarship_id}", response_model=ScholarshipDetail, responses={**AUTH_ERRORS, **NOT_FOUND}
)
def get_scholarship(scholarship_id: int, user: ConsentedUser, db: DB) -> ScholarshipDetail:
    """상세 + 조건별 판정 + 같은 공지의 다른 장학금. 마감된 장학금도 봅니다(내 장학금 화면)."""
    profile = _load_profile(db, user)
    row = db.execute(_base_query(user.id).where(Scholarship.id == scholarship_id)).first()
    if row is None:
        raise _not_found()
    s, notice, src, my_state, _ = row
    attachments = db.scalars(
        select(NoticeAttachment)
        .where(
            NoticeAttachment.notice_id == notice.id,
            NoticeAttachment.status.in_(("OK", *UNREAD_ATTACHMENT_STATUSES)),
        )
        .order_by(NoticeAttachment.position)
    ).all()
    unread = [a for a in attachments if a.status in UNREAD_ATTACHMENT_STATUSES]
    read = [a for a in attachments if a.status == "OK"]
    criteria = ScholarshipCriteria.model_validate(s.criteria)
    answers = load_condition_answers(db, [user.id], [s.id]).get(user.id, {}).get(s.id)
    evaluation = evaluate(
        profile,
        criteria,
        now_kst().date(),
        has_unread_attachments=bool(unread),
        answers=answers,
    )
    siblings = db.execute(
        select(Scholarship.id, Scholarship.name, Scholarship.display_name, Scholarship.status)
        .where(Scholarship.notice_id == notice.id, Scholarship.id != s.id)
        .order_by(Scholarship.id)
    ).all()
    return ScholarshipDetail.model_validate(
        {
            **_summary(s, notice, src, my_state, evaluation),
            "apply_start": _kst(s.apply_start),
            "conditions": evaluation.checks,
            "extra_conditions": evaluation.extra_conditions,
            "extra_checks": evaluation.extra_checks,
            "basic_conditions": evaluation.basic_conditions,
            "basic_items": [
                ConditionTextOut(text=i.text, summary=i.summary) for i in evaluation.basic_items
            ],
            "notes": evaluation.notes,
            "required_documents": criteria.required_documents,
            "notice": NoticeRef(
                id=notice.id,
                title=notice.title,
                url=notice.url,
                posted_at=_kst(notice.posted_at),
                body_text=notice.body_text,
                attachments=[
                    NoticeAttachmentText(name=a.name, url=a.url, text=a.text) for a in read
                ],
            ),
            "siblings": [
                SiblingScholarship(id=i, name=n, display_name=dn or n, status=st)
                for i, n, dn, st in siblings
            ],
            "unread_attachments": [
                UnreadAttachment(name=a.name, url=a.url, reason=a.status) for a in unread
            ],
            "input_truncated": notice.input_truncated,
        }
    )


@router.put(
    "/{scholarship_id}/state",
    response_model=StateResponse,
    responses={**AUTH_ERRORS, 404: {"description": "장학금 없음"}},
)
def put_state(
    scholarship_id: int, body: StateRequest, user: ConsentedUser, db: DB
) -> StateResponse:
    """관심 / 신청완료. 장학금마다 상태는 하나이며 새 값으로 바꿉니다."""
    if db.get(Scholarship, scholarship_id) is None:
        raise _not_found()
    upsert = insert(UserScholarshipState).values(
        user_id=user.id, scholarship_id=scholarship_id, state=body.state
    )
    db.execute(
        upsert.on_conflict_do_update(
            index_elements=["user_id", "scholarship_id"], set_={"state": body.state}
        )
    )
    db.commit()
    return StateResponse(scholarship_id=scholarship_id, state=body.state)


@router.delete(
    "/{scholarship_id}/state", status_code=status.HTTP_204_NO_CONTENT, responses=AUTH_ERRORS
)
def delete_state(scholarship_id: int, user: ConsentedUser, db: DB) -> Response:
    """상태를 지웁니다. 상태가 없어도 204."""
    state = db.get(UserScholarshipState, (user.id, scholarship_id))
    if state is not None:
        db.delete(state)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


CONDITION_ERRORS = {**AUTH_ERRORS, 404: {"description": "장학금·조건 없음 또는 프로필 미입력"}}


def _condition_target(db: Session, scholarship_id: int, key: str) -> Scholarship:
    """장학금의 지금 `extra_conditions`에 그 key가 있어야 합니다(재추출로 바뀐 문장은 404)."""
    s = db.get(Scholarship, scholarship_id)
    if s is None:
        raise _not_found()
    criteria = ScholarshipCriteria.model_validate(s.criteria)
    if key not in {key_of(item.text) for item in criteria.extra_conditions}:
        raise _not_found("조건을 찾을 수 없습니다")
    return s


def _answer_response(
    db: Session, user: User, s: Scholarship, key: str, answer: ConditionAnswer | None
) -> ConditionAnswerResponse:
    """답을 반영한 판정을 다시 계산해 돌려줍니다. 화면은 이 값으로 배지를 바로 바꿉니다."""
    profile = _load_profile(db, user)
    has_unread = db.scalar(
        select(
            exists().where(
                NoticeAttachment.notice_id == s.notice_id,
                NoticeAttachment.status.in_(UNREAD_ATTACHMENT_STATUSES),
            )
        )
    )
    answers = load_condition_answers(db, [user.id], [s.id]).get(user.id, {}).get(s.id)
    evaluation = evaluate(
        profile,
        ScholarshipCriteria.model_validate(s.criteria),
        now_kst().date(),
        has_unread_attachments=bool(has_unread),
        answers=answers,
    )
    return ConditionAnswerResponse(
        scholarship_id=s.id, key=key, answer=answer, result=evaluation.result
    )


@router.put(
    "/{scholarship_id}/conditions/{condition_key}",
    response_model=ConditionAnswerResponse,
    responses=CONDITION_ERRORS,
)
def put_condition_answer(
    scholarship_id: int,
    condition_key: str,
    body: ConditionAnswerRequest,
    user: ConsentedUser,
    db: DB,
) -> ConditionAnswerResponse:
    """자격을 좁히는 조건 1줄에 답합니다: YES(맞아요, 충족) / NO(안 맞아요, 불충족). 새 값 교체."""
    s = _condition_target(db, scholarship_id, condition_key)
    enc_answer = encrypt(body.answer, aad=_answer_aad(user.id, s.id, condition_key))
    answered_at = now_kst()
    upsert = insert(UserConditionAnswer).values(
        user_id=user.id,
        scholarship_id=s.id,
        condition_key=condition_key,
        enc_answer=enc_answer,
        answered_at=answered_at,
    )
    db.execute(
        upsert.on_conflict_do_update(
            index_elements=["user_id", "scholarship_id", "condition_key"],
            set_={"enc_answer": enc_answer, "answered_at": answered_at},
        )
    )
    db.commit()
    return _answer_response(db, user, s, condition_key, body.answer)


@router.delete(
    "/{scholarship_id}/conditions/{condition_key}",
    response_model=ConditionAnswerResponse,
    responses=CONDITION_ERRORS,
)
def delete_condition_answer(
    scholarship_id: int, condition_key: str, user: ConsentedUser, db: DB
) -> ConditionAnswerResponse:
    """답을 지워 다시 '?'(확인 필요)로 돌립니다. 답이 없어도 같은 응답입니다."""
    s = _condition_target(db, scholarship_id, condition_key)
    row = db.get(UserConditionAnswer, (user.id, s.id, condition_key))
    if row is not None:
        db.delete(row)
        db.commit()
    return _answer_response(db, user, s, condition_key, None)
