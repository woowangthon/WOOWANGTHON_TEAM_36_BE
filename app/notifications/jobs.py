"""하루 한 번 알림 메일 발송 (계획서 7.4, 9.3).

    uv run python -m app.notifications.jobs           # 정기 실행과 같은 1회 실행
    uv run python -m app.notifications.jobs --force   # 9시 조건과 오늘 실행 여부를 무시

실행 흐름(`run_daily_notifications`)
1. 메일 설정이나 `APP_BASE_URL`이 없으면 건너뜁니다. 서울 시각 9시 전에도 건너뜁니다.
2. 시작 잠금: 한 트랜잭션에서 `pg_advisory_xact_lock` → 오늘 `NOTIFICATION_RUNS` 행 확인.
   `DONE`이거나 1시간 안에 시작한 `RUNNING`이면 건너뛰고, 아니면 `RUNNING`으로 기록합니다
   (수집 작업과 같은 방식, 9.3).
3. 후보 장학금(최근 48시간에 새로 저장된 모집 중 장학금 + 마감이 4일 안인 장학금)을
   한 번에 읽습니다.
4. 수신자(동의 완료 + 프로필 있음 + 알림 켬)와 그들의 관심·신청 상태, 발송 기록을 한 번씩 읽습니다.
   사용자 수가 늘어도 조회 쿼리 수는 같습니다(9.8).
5. SMTP에 한 번 접속해 사용자마다 한 통씩 보내고, 성공한 항목만 `NOTIFICATION_LOGS`에 남깁니다.
   한 통이 실패하면 그 사용자만 건너뛰어 다음 날 다시 시도됩니다(새 장학금 기준이 48시간인 이유).
6. `NOTIFICATION_RUNS`를 `DONE`/`FAILED`로 마감합니다. `FAILED`면 다음 시각 수집이 다시 시도합니다.

요청 안에서 끝까지 동기로 실행합니다(`BackgroundTasks` 쓰지 않음, 9.3).
판정은 규칙 엔진만 쓰고 LLM을 부르지 않습니다(5.3). 로그에는 `user_id`만 남기고
이메일 주소·프로필 값·메일 내용·수신 거부 토큰은 남기지 않습니다(4.2).
"""

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.profile import profile_from_row
from app.api.scholarships import load_condition_answers, unread_attachments_exist
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import configure_logging, log_event
from app.core.mail import MailError, OutgoingMail, SendFn, mail_configured, mail_session
from app.core.security import create_unsubscribe_token
from app.extraction.schemas import ScholarshipCriteria
from app.models import (
    Notice,
    NotificationLog,
    NotificationRun,
    Scholarship,
    User,
    UserProfile,
    UserScholarshipState,
)
from app.notifications.message import build_message, unsubscribe_url
from app.notifications.select import (
    NEW_MATCH,
    NEW_STATUSES,
    NEW_WINDOW,
    Candidate,
    Item,
    Recipient,
    SentKey,
    select_items,
)
from app.parsers.dates import now_kst, to_kst

logger = logging.getLogger(__name__)

NOTIFY_LOCK_KEY = 36_2026_12  # pg_advisory_xact_lock 고정 키 (알림 전용, 수집은 36_2026_11)
SEND_HOUR = 9  # 서울 시각. 이 시각 이후 첫 수집 때 보냄
MAX_MAILS_PER_RUN = 450  # Gmail 하루 약 500통 한도에서 가입 인증번호 몫을 남김
RUNNING_STALE_AFTER = timedelta(hours=1)  # 1시간 지난 RUNNING은 비정상 종료로 보고 무시
DEADLINE_LOOKAHEAD = timedelta(days=4)  # 마감 3일 전 알림까지 들어오는 범위

OpenMail = Callable[[], AbstractContextManager[SendFn]]


@dataclass
class NotifyOutcome:
    skipped: bool
    sent: int = 0
    failed: int = 0


# --- 실행 진입점 ------------------------------------------------------------


def run_daily_notifications(
    *,
    force: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    clock: Callable[[], datetime] = now_kst,
    open_mail: OpenMail = mail_session,
) -> NotifyOutcome:
    settings = get_settings()
    if not mail_configured() or not settings.app_base_url:
        log_event(logger, "메일 설정이나 APP_BASE_URL이 없어 알림을 건너뜀", logging.WARNING)
        return NotifyOutcome(skipped=True)

    now = clock()
    if not force and to_kst(now).hour < SEND_HOUR:
        return NotifyOutcome(skipped=True)

    factory = session_factory or get_sessionmaker()
    if not start_run(factory, now, force=force):
        return NotifyOutcome(skipped=True)

    outcome = NotifyOutcome(skipped=False)
    status = "FAILED"
    try:
        with factory() as session:
            candidates = load_candidates(session, now)
            recipients = load_recipients(session, candidates) if candidates else []
            if recipients:
                _send_all(
                    session, recipients, candidates, now, settings.app_base_url, open_mail, outcome
                )
        status = "DONE"
    except MailError as error:  # SMTP 접속·로그인 실패: 다음 시각 수집이 다시 시도
        log_event(logger, "알림 메일 서버 접속 실패", logging.ERROR, error=str(error))
    finally:
        finish_run(factory, now, status, outcome, clock())
    log_event(logger, "알림 발송 완료", status=status, sent=outcome.sent, failed=outcome.failed)
    return outcome


def start_run(factory: sessionmaker[Session], now: datetime, *, force: bool) -> bool:
    """하루 한 번만 실행되게 합니다(9.3). 오늘 이미 했거나 진행 중이면 False.

    Neon pooler는 트랜잭션마다 연결을 바꿔 쓰므로 트랜잭션 단위 잠금을 잡고, 같은 트랜잭션 안에서
    오늘 행 확인과 기록을 한 뒤 커밋합니다. 잠금은 커밋하면 풀립니다.
    """
    today = to_kst(now).date()
    with factory() as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": NOTIFY_LOCK_KEY})
        run = session.get(NotificationRun, today)
        if run is None:
            session.add(NotificationRun(run_date=today, status="RUNNING", started_at=now))
            return True
        running = run.status == "RUNNING" and run.started_at > now - RUNNING_STALE_AFTER
        if not force and (run.status == "DONE" or running):
            return False
        run.status = "RUNNING"
        run.started_at = now
        run.finished_at = None
        run.sent_count = 0
        run.failed_count = 0
        return True


def finish_run(
    factory: sessionmaker[Session],
    now: datetime,
    status: str,
    outcome: NotifyOutcome,
    finished_at: datetime,
) -> None:
    with factory() as session, session.begin():
        run = session.get_one(NotificationRun, to_kst(now).date())
        run.status = status
        run.finished_at = finished_at
        run.sent_count = outcome.sent
        run.failed_count = outcome.failed


# --- 조회 ------------------------------------------------------------------


def load_candidates(session: Session, now: datetime) -> list[Candidate]:
    """새 장학금 후보와 마감이 가까운 장학금을 쿼리 한 번으로 읽습니다."""
    query = (
        select(Scholarship, Notice.notice_kind, unread_attachments_exist())
        .join(Notice, Scholarship.notice_id == Notice.id)
        .where(
            or_(
                and_(
                    Scholarship.created_at > now - NEW_WINDOW,
                    Scholarship.status.in_(NEW_STATUSES),
                ),
                and_(
                    Scholarship.status == "OPEN",
                    Scholarship.apply_end >= now,
                    Scholarship.apply_end < now + DEADLINE_LOOKAHEAD,
                ),
            )
        )
        .order_by(Scholarship.id)
    )
    candidates = []
    for s, notice_kind, has_unread in session.execute(query):
        try:
            criteria = ScholarshipCriteria.model_validate(s.criteria)
        except ValidationError:  # 옛 추출 결과 등: 이 장학금만 빼고 계속
            log_event(
                logger, "조건 형식이 맞지 않아 알림에서 뺌", logging.WARNING, scholarship=s.id
            )
            continue
        candidates.append(
            Candidate(
                scholarship_id=s.id,
                name=s.name,
                status=s.status,
                notice_kind=notice_kind,
                apply_end=s.apply_end,
                created_at=s.created_at,
                criteria=criteria,
                has_unread=has_unread,
            )
        )
    return candidates


def load_recipients(session: Session, candidates: list[Candidate]) -> list[tuple[str, Recipient]]:
    """(이메일, 수신자) 목록. 사용자 수와 관계없이 쿼리 4번입니다(상태·기록·조건 답·수신자)."""
    ids = [c.scholarship_id for c in candidates]
    states: dict[int, dict[int, str]] = defaultdict(dict)
    for row in session.execute(
        select(
            UserScholarshipState.user_id,
            UserScholarshipState.scholarship_id,
            UserScholarshipState.state,
        ).where(UserScholarshipState.scholarship_id.in_(ids))
    ):
        states[row.user_id][row.scholarship_id] = row.state

    sent: dict[int, set[SentKey]] = defaultdict(set)
    for row in session.execute(
        select(
            NotificationLog.user_id,
            NotificationLog.scholarship_id,
            NotificationLog.kind,
            NotificationLog.apply_end,
        ).where(NotificationLog.scholarship_id.in_(ids))
    ):
        sent[row.user_id].add((row.scholarship_id, row.kind, row.apply_end))

    answers = load_condition_answers(session, None, ids)

    recipients = []
    users = (
        select(User.id, User.email, UserProfile)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(User.privacy_agreed_at.is_not(None), User.email_notifications.is_(True))
        .order_by(User.id)
    )
    for user_id, email, profile_row in session.execute(users):
        try:
            profile = profile_from_row(profile_row)
        except Exception as error:  # 복호화 실패·옛 형식: 이 사용자만 건너뜀
            log_event(
                logger,
                "프로필을 읽지 못해 알림에서 뺌",
                logging.ERROR,
                user_id=user_id,
                error=type(error).__name__,
            )
            continue
        recipients.append(
            (
                email,
                Recipient(
                    user_id=user_id,
                    profile=profile,
                    states=states.get(user_id, {}),
                    sent=frozenset(sent.get(user_id, ())),
                    answers=answers.get(user_id, {}),
                ),
            )
        )
    return recipients


# --- 발송 ------------------------------------------------------------------


def _send_all(
    session: Session,
    recipients: list[tuple[str, Recipient]],
    candidates: list[Candidate],
    now: datetime,
    base_url: str,
    open_mail: OpenMail,
    outcome: NotifyOutcome,
) -> None:
    """수신자마다 보낼 항목을 고르고, 있으면 한 통을 보냅니다. 보낼 사람이 있을 때만 접속합니다."""
    pending = [
        (email, recipient, items)
        for email, recipient in recipients
        if (items := select_items(recipient, candidates, now))
    ]
    if not pending:
        return
    session.rollback()  # 읽기 트랜잭션을 닫고, 아래에서 사용자 단위로 커밋
    with open_mail() as send:
        for email, recipient, items in pending:
            if outcome.sent + outcome.failed >= MAX_MAILS_PER_RUN:
                log_event(
                    logger,
                    "실행당 발송 상한에 닿아 나머지는 보내지 않음",
                    logging.WARNING,
                    limit=MAX_MAILS_PER_RUN,
                    remaining=len(pending) - outcome.sent - outcome.failed,
                )
                break
            if _send_one(session, send, email, recipient.user_id, items, now, base_url):
                outcome.sent += 1
            else:
                outcome.failed += 1


def _send_one(
    session: Session,
    send: SendFn,
    email: str,
    user_id: int,
    items: list[Item],
    now: datetime,
    base_url: str,
) -> bool:
    token = create_unsubscribe_token(user_id)
    content = build_message(items, base_url=base_url, unsubscribe_token=token)
    mail = OutgoingMail(
        to=email,
        subject=content.subject,
        text=content.text,
        html=content.html,
        headers={"List-Unsubscribe": f"<{unsubscribe_url(base_url, token)}>"},
    )
    try:
        send(mail)
        session.add_all(
            NotificationLog(
                user_id=user_id,
                scholarship_id=item.scholarship_id,
                kind=item.kind,
                apply_end=None if item.kind == NEW_MATCH else item.apply_end,
                sent_at=now,
            )
            for item in items
        )
        session.commit()
        return True
    except Exception as error:  # 발송 실패, 탈퇴로 인한 기록 실패 등: 이 사용자만 건너뜀
        session.rollback()
        log_event(
            logger, "알림 발송 실패", logging.ERROR, user_id=user_id, error=type(error).__name__
        )
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="장학금 알림 메일 발송 (계획서 7.4)")
    parser.add_argument(
        "--force", action="store_true", help="9시 조건과 오늘 실행 여부를 무시하고 실행"
    )
    args = parser.parse_args(argv)

    configure_logging()
    outcome = run_daily_notifications(force=args.force)
    if outcome.skipped:
        print("알림을 건너뛰었습니다(설정 없음, 9시 전, 또는 오늘 이미 실행).")
        return 0
    print(f"알림 발송: 성공 {outcome.sent}통, 실패 {outcome.failed}통")
    return 0


if __name__ == "__main__":
    sys.exit(main())
