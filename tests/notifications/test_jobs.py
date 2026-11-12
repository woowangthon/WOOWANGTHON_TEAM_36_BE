"""하루 한 번 알림 발송 작업 (계획서 7.4, 9.3). 메일은 Outbox 대역으로 받습니다."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, event, select, update

from app.models import NotificationLog, NotificationRun, User, UserProfile
from app.notifications import jobs
from app.notifications.jobs import run_daily_notifications, start_run
from app.parsers.dates import kst
from tests.notifications.conftest import (
    CONDITIONAL,
    INELIGIBLE,
    add_scholarship,
    add_user,
    bookmark,
)

NOW = kst(2026, 11, 11, 9, 0)
FRESH = NOW - timedelta(hours=1)
D3 = kst(2026, 11, 14, 23, 59)


def run(factory, outbox, *, now=NOW, force=False):
    return run_daily_notifications(
        force=force, session_factory=factory, clock=lambda: now, open_mail=outbox
    )


def logs(factory) -> set[tuple[int, int, str]]:
    with factory() as session:
        rows = session.execute(
            select(NotificationLog.user_id, NotificationLog.scholarship_id, NotificationLog.kind)
        )
        return {tuple(row) for row in rows}


def run_rows(factory) -> list[NotificationRun]:
    with factory() as session:
        return list(session.scalars(select(NotificationRun).order_by(NotificationRun.run_date)))


# --- 기본 흐름 ------------------------------------------------------------------


def test_sends_one_mail_with_new_and_deadline_items(session_factory, outbox):
    user_id = add_user(session_factory, "a@example.com")
    new_id = add_scholarship(session_factory, "새장학금", created_at=FRESH)
    marked_id = add_scholarship(session_factory, "관심장학금", apply_end=D3)
    bookmark(session_factory, user_id, marked_id)

    outcome = run(session_factory, outbox)

    assert (outcome.skipped, outcome.sent, outcome.failed) == (False, 1, 0)
    [mail] = outbox.mails
    assert mail.to == "a@example.com"
    assert mail.subject == "[장학금 매칭] 새 장학금 1건, 마감 임박 1건"
    assert "새장학금" in mail.text and "관심장학금" in mail.text
    assert mail.html is not None
    assert mail.headers["List-Unsubscribe"].startswith("<https://app.example/unsubscribe?token=")
    assert logs(session_factory) == {
        (user_id, new_id, "NEW_MATCH"),
        (user_id, marked_id, "DEADLINE_D3"),
    }
    [row] = run_rows(session_factory)
    assert (row.run_date, row.status, row.sent_count, row.failed_count) == (
        NOW.date(),
        "DONE",
        1,
        0,
    )
    assert row.finished_at is not None


def test_deadline_log_keeps_the_deadline_and_new_match_log_does_not(session_factory, outbox):
    user_id = add_user(session_factory)
    add_scholarship(session_factory, "새장학금", created_at=FRESH, apply_end=kst(2026, 11, 30))
    marked_id = add_scholarship(session_factory, "관심장학금", apply_end=D3)
    bookmark(session_factory, user_id, marked_id)

    run(session_factory, outbox)

    with session_factory() as session:
        saved = {
            log.kind: log.apply_end
            for log in session.scalars(select(NotificationLog))  # noqa: F821
        }
    assert saved["NEW_MATCH"] is None
    assert saved["DEADLINE_D3"] == D3


def test_only_matching_new_scholarships_are_listed(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, "가능장학금", created_at=FRESH)
    add_scholarship(session_factory, "확인장학금", created_at=FRESH, criteria=CONDITIONAL)
    add_scholarship(session_factory, "불가장학금", created_at=FRESH, criteria=INELIGIBLE)

    run(session_factory, outbox)

    [mail] = outbox.mails
    assert "가능장학금" in mail.text and "확인장학금" in mail.text
    assert "불가장학금" not in mail.text


def test_user_with_nothing_to_send_gets_no_mail(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, "불가장학금", created_at=FRESH, criteria=INELIGIBLE)

    outcome = run(session_factory, outbox)

    assert (outcome.skipped, outcome.sent) == (False, 0)
    assert outbox.mails == []
    assert run_rows(session_factory)[0].status == "DONE"


def test_no_candidates_finishes_without_opening_mail(session_factory, outbox):
    add_user(session_factory)

    outcome = run(session_factory, outbox)

    assert (outcome.skipped, outcome.sent) == (False, 0)
    assert outbox.opened == 0
    assert run_rows(session_factory)[0].status == "DONE"


@pytest.mark.parametrize(
    "user",
    [{"notifications": False}, {"consented": False}, {"with_profile": False}],
)
def test_recipients_filter(session_factory, outbox, user):
    add_user(session_factory, **user)
    add_scholarship(session_factory, created_at=FRESH)

    run(session_factory, outbox)

    assert outbox.mails == []


# --- 하루 한 번 -----------------------------------------------------------------


def test_before_nine_is_skipped(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)

    outcome = run(session_factory, outbox, now=kst(2026, 11, 11, 8, 59))

    assert outcome.skipped is True
    assert outbox.mails == [] and run_rows(session_factory) == []


def test_second_call_same_day_is_skipped(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)
    run(session_factory, outbox)
    add_scholarship(session_factory, "나중장학금", created_at=NOW)

    outcome = run(session_factory, outbox, now=kst(2026, 11, 11, 10, 0))

    assert outcome.skipped is True
    assert len(outbox.mails) == 1


def test_next_day_sends_only_what_was_not_sent(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, "어제장학금", created_at=FRESH)
    run(session_factory, outbox)
    add_scholarship(session_factory, "오늘장학금", created_at=kst(2026, 11, 12, 8, 0))

    run(session_factory, outbox, now=kst(2026, 11, 12, 9, 0))

    assert len(outbox.mails) == 2
    assert "오늘장학금" in outbox.mails[1].text
    assert "어제장학금" not in outbox.mails[1].text
    assert [r.status for r in run_rows(session_factory)] == ["DONE", "DONE"]


def test_failed_run_is_retried(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)
    outbox.open_fails = True

    first = run(session_factory, outbox)

    assert (first.skipped, first.sent) == (False, 0)
    assert run_rows(session_factory)[0].status == "FAILED"

    outbox.open_fails = False
    second = run(session_factory, outbox, now=kst(2026, 11, 11, 10, 0))

    assert (second.skipped, second.sent) == (False, 1)
    [row] = run_rows(session_factory)
    assert (row.status, row.sent_count) == ("DONE", 1)


@pytest.mark.parametrize(
    ("age", "skipped"), [(timedelta(minutes=10), True), (timedelta(hours=2), False)]
)
def test_running_row_blocks_until_stale(session_factory, outbox, age, skipped):
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)
    with session_factory() as session:
        session.add(NotificationRun(run_date=NOW.date(), status="RUNNING", started_at=NOW - age))
        session.commit()

    outcome = run(session_factory, outbox)

    assert outcome.skipped is skipped
    assert len(outbox.mails) == (0 if skipped else 1)


def test_force_ignores_hour_and_done_row(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, created_at=kst(2026, 11, 11, 6, 0))
    early = kst(2026, 11, 11, 7, 0)
    run(session_factory, outbox, now=early, force=True)
    add_scholarship(session_factory, "나중장학금", created_at=kst(2026, 11, 11, 6, 30))

    outcome = run(session_factory, outbox, now=early, force=True)

    assert (outcome.skipped, outcome.sent) == (False, 1)
    assert len(outbox.mails) == 2


def test_concurrent_start_runs_once(session_factory):
    assert start_run(session_factory, NOW, force=False) is True
    assert start_run(session_factory, NOW, force=False) is False


# --- 설정 ----------------------------------------------------------------------


def test_skips_when_mail_not_configured(session_factory, outbox, monkeypatch):
    monkeypatch.delenv("MAIL_MOCK")
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)

    outcome = run(session_factory, outbox)

    assert outcome.skipped is True
    assert outbox.opened == 0 and run_rows(session_factory) == []


def test_skips_without_app_base_url(session_factory, outbox, monkeypatch):
    monkeypatch.delenv("APP_BASE_URL")
    add_user(session_factory)
    add_scholarship(session_factory, created_at=FRESH)

    outcome = run(session_factory, outbox)

    assert outcome.skipped is True
    assert outbox.opened == 0 and run_rows(session_factory) == []


# --- 실패 격리 -------------------------------------------------------------------


def test_one_failed_mail_does_not_stop_others(session_factory, outbox, caplog):
    failed_id = add_user(session_factory, "a@example.com")
    ok_id = add_user(session_factory, "b@example.com")
    add_scholarship(session_factory, created_at=FRESH)
    outbox.fail_for = {"a@example.com"}
    caplog.set_level("DEBUG")

    outcome = run(session_factory, outbox)

    assert (outcome.sent, outcome.failed) == (1, 1)
    assert outbox.recipients == ["b@example.com"]
    assert {user for user, _, _ in logs(session_factory)} == {ok_id}  # 실패는 기록하지 않음
    assert failed_id not in {user for user, _, _ in logs(session_factory)}
    [row] = run_rows(session_factory)
    assert (row.status, row.sent_count, row.failed_count) == ("DONE", 1, 1)
    assert "a@example.com" not in caplog.text and "b@example.com" not in caplog.text


def test_failed_user_gets_the_mail_next_day(session_factory, outbox):
    add_user(session_factory, "a@example.com")
    add_scholarship(session_factory, created_at=FRESH)
    outbox.fail_for = {"a@example.com"}
    run(session_factory, outbox)
    outbox.fail_for = set()

    run(session_factory, outbox, now=kst(2026, 11, 12, 9, 0))

    assert outbox.recipients == ["a@example.com"]


def test_mail_cap(session_factory, outbox, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_MAILS_PER_RUN", 2)
    for n in range(3):
        add_user(session_factory, f"u{n}@example.com")
    add_scholarship(session_factory, created_at=FRESH)

    outcome = run(session_factory, outbox)

    assert outcome.sent == 2
    assert len(outbox.mails) == 2


def test_broken_profile_skips_only_that_user(session_factory, outbox, caplog):
    broken_id = add_user(session_factory, "a@example.com")
    add_user(session_factory, "b@example.com")
    add_scholarship(session_factory, created_at=FRESH)
    with session_factory() as session:
        session.execute(
            update(UserProfile)
            .where(UserProfile.user_id == broken_id)
            .values(enc_income_bracket=b"garbage")
        )
        session.commit()
    caplog.set_level("DEBUG")

    outcome = run(session_factory, outbox)

    assert outbox.recipients == ["b@example.com"]
    assert outcome.sent == 1
    assert "a@example.com" not in caplog.text


def test_invalid_criteria_skips_only_that_scholarship(session_factory, outbox):
    add_user(session_factory)
    add_scholarship(session_factory, "깨진장학금", created_at=FRESH, criteria={"min_gpa": "높음"})
    add_scholarship(session_factory, "정상장학금", created_at=FRESH)

    outcome = run(session_factory, outbox)

    assert outcome.sent == 1
    assert "정상장학금" in outbox.mails[0].text
    assert "깨진장학금" not in outbox.mails[0].text


def test_user_deleted_mid_run_does_not_stop_run(session_factory, outbox):
    leaving_id = add_user(session_factory, "a@example.com")
    add_user(session_factory, "b@example.com")
    add_scholarship(session_factory, created_at=FRESH)

    def withdraw(mail):
        if mail.to == "a@example.com":
            with session_factory() as session:
                session.execute(delete(User).where(User.id == leaving_id))
                session.commit()

    outbox.before_send = withdraw

    outcome = run(session_factory, outbox)

    assert (outcome.sent, outcome.failed) == (1, 1)
    assert "b@example.com" in outbox.recipients
    assert run_rows(session_factory)[0].status == "DONE"


# --- 쿼리 수 (9.8 N+1 금지) ---------------------------------------------------------


def _count_selects(engine, call) -> int:
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # noqa: ARG001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_select_count_does_not_grow_with_users(session_factory, outbox, migrated_engine):
    add_user(session_factory, "u0@example.com")
    add_scholarship(session_factory, created_at=FRESH)
    small = _count_selects(migrated_engine, lambda: run(session_factory, outbox))

    for n in range(1, 6):
        add_user(session_factory, f"u{n}@example.com")
    add_scholarship(session_factory, "둘째장학금", created_at=kst(2026, 11, 12, 8, 0))
    next_day = kst(2026, 11, 12, 9, 0)
    large = _count_selects(migrated_engine, lambda: run(session_factory, outbox, now=next_day))

    assert len(outbox.mails) == 1 + 6
    assert small == large


def test_mail_uses_display_name(session_factory, outbox):
    """알림 메일에는 쉬운 이름(extract_v9)을 쓰고, 없으면 원래 이름을 씁니다."""
    add_user(session_factory, "a@example.com")
    add_scholarship(
        session_factory,
        "2026학년도 2학기 (재)두산연강재단 장학생 선발",
        display_name="두산연강재단 장학금",
        created_at=FRESH,
    )
    add_scholarship(session_factory, "옛 추출 장학금", created_at=FRESH)

    run(session_factory, outbox)

    [mail] = outbox.mails
    assert "두산연강재단 장학금" in mail.text
    assert "2026학년도 2학기" not in mail.text
    assert "옛 추출 장학금" in mail.text
