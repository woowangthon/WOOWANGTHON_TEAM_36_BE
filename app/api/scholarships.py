"""맞춤 목록·상세·내 상태 (계획서 5.3, 7.1, 7.2, 9.8).

- 판정은 요청마다 규칙 엔진(`app.matching`)으로 계산합니다. LLM을 부르지 않습니다(5.3).
- 목록은 장학금·공지·출처·내 상태를 한 번의 JOIN 쿼리로 읽어 공지 수와 관계없이 쿼리 수가
  같습니다(9.8 N+1 금지).
- 기본 목록은 `OPEN`·`ALWAYS`만, 공지가 `모집`·`기준안내`인 것만 보여 줍니다.
- 공지에 읽지 못한 첨부가 있으면 판정을 "지원 가능"이 아니라 "확인 필요"로 낮춥니다(D8).
  목록은 같은 쿼리의 EXISTS 열로 확인해 쿼리 수를 늘리지 않습니다.
  `state`를 주면 내 장학금 화면용으로 마감(`CLOSED`)된 것도 포함합니다.
- 프로필 값은 로그에 남기지 않습니다(4.2).
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import and_, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.profile import _to_response as profile_from_row
from app.core.db import get_db
from app.core.security import ConsentedUser
from app.extraction.schemas import BenefitType, ScholarshipCriteria
from app.matching.engine import evaluate
from app.models import (
    Notice,
    NoticeAttachment,
    Scholarship,
    Source,
    User,
    UserProfile,
    UserScholarshipState,
)
from app.models.notice import UNREAD_ATTACHMENT_STATUSES
from app.parsers.dates import now_kst, to_kst
from app.schemas.api.profile import Profile
from app.schemas.api.scholarships import (
    MatchResult,
    NoticeAttachmentText,
    NoticeRef,
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


def _summary(
    s: Scholarship, notice: Notice, source: Source, state: str | None, result: MatchResult
) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "source_code": source.code,
        "source_name": source.name,
        "is_internal": source.is_internal,
        "benefit_amount_text": s.benefit_amount_text,
        "benefit_type": s.benefit_type,
        "apply_end": _kst(s.apply_end),
        "status": s.status,
        "deadline_conflict": s.deadline_conflict,
        "posted_at": _kst(notice.posted_at),
        "result": result,
        "my_state": state,
    }


def _unread_attachments_exist():
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
        select(Scholarship, Notice, Source, UserScholarshipState.state, _unread_attachments_exist())
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
    items = []
    for s, notice, src, my_state, has_unread in db.execute(query):
        criteria = ScholarshipCriteria.model_validate(s.criteria)
        outcome = evaluate(profile, criteria, today, has_unread_attachments=has_unread).result
        if outcome in wanted:
            items.append(
                ScholarshipSummary.model_validate(_summary(s, notice, src, my_state, outcome))
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
    evaluation = evaluate(profile, criteria, now_kst().date(), has_unread_attachments=bool(unread))
    siblings = db.execute(
        select(Scholarship.id, Scholarship.name, Scholarship.status)
        .where(Scholarship.notice_id == notice.id, Scholarship.id != s.id)
        .order_by(Scholarship.id)
    ).all()
    return ScholarshipDetail.model_validate(
        {
            **_summary(s, notice, src, my_state, evaluation.result),
            "apply_start": _kst(s.apply_start),
            "conditions": evaluation.checks,
            "extra_conditions": evaluation.extra_conditions,
            "basic_conditions": evaluation.basic_conditions,
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
            "siblings": [SiblingScholarship(id=i, name=n, status=st) for i, n, st in siblings],
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
