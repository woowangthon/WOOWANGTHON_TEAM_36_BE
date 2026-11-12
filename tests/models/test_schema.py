from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from sqlalchemy import DateTime, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Base

SEOUL = ZoneInfo("Asia/Seoul")
TABLES = {
    "users",
    "user_profiles",
    "sources",
    "notices",
    "notice_attachments",
    "scholarships",
    "user_scholarship_states",
    "user_condition_answers",
    "crawl_runs",
    "notification_logs",
    "notification_runs",
}


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


# --- 마이그레이션 ----------------------------------------------------------


def test_upgrade_head_creates_all_tables(migrated_engine):
    assert _tables(migrated_engine) == TABLES


def test_sources_seeded(migrated_engine):
    with migrated_engine.connect() as c:
        rows = c.execute(text("SELECT code, is_internal FROM sources ORDER BY code")).all()
    assert rows == [("AIBC", True), ("KOSAF", False), ("SW_DEPT", True)]


def test_downgrade_base_then_reapply(migrated_engine, alembic_config):
    command.downgrade(alembic_config, "base")
    assert _tables(migrated_engine) == set()
    command.upgrade(alembic_config, "head")
    assert _tables(migrated_engine) == TABLES


def test_models_match_migration(migrated_engine, alembic_config):
    """모델을 바꾸고 마이그레이션을 빠뜨리면 실패합니다(alembic check)."""
    command.check(alembic_config)


def test_all_time_columns_are_timestamptz(migrated_engine):
    model_cols = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, DateTime)
    ]
    assert model_cols, "시각 컬럼이 하나도 없습니다"
    with migrated_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type LIKE 'timestamp%'"
            )
        ).all()
    assert {f"{t}.{col}" for t, col, _ in rows} == set(model_cols)
    assert {dt for _, _, dt in rows} == {"timestamp with time zone"}


# --- 유니크 제약 -----------------------------------------------------------


def _insert_notice(conn, source="SW_DEPT", external_id="1154830"):
    conn.execute(
        text(
            "INSERT INTO notices (source_code, external_id, title, posted_at, url, body_text,"
            " content_hash) VALUES (:s, :e, '제목', :p, 'https://example.com', '본문', 'h')"
        ),
        {"s": source, "e": external_id, "p": datetime(2026, 11, 1, 9, 0, tzinfo=SEOUL)},
    )


def _insert_user(conn, email, google_sub=None):
    conn.execute(
        text("INSERT INTO users (email, google_sub) VALUES (:e, :g)"),
        {"e": email, "g": google_sub},
    )


def test_notice_unique_per_source_and_external_id(conn):
    _insert_notice(conn)
    _insert_notice(conn, source="KOSAF")  # 다른 출처의 같은 ID는 허용
    with pytest.raises(IntegrityError, match="uq_notices_source_code_external_id"):
        with conn.begin_nested():
            _insert_notice(conn)


def test_google_sub_unique_but_nullable(conn):
    _insert_user(conn, "a@example.com")
    _insert_user(conn, "b@example.com")  # google_sub NULL은 여러 개 허용
    _insert_user(conn, "c@example.com", google_sub="sub-1")
    with pytest.raises(IntegrityError, match="uq_users_google_sub"):
        with conn.begin_nested():
            _insert_user(conn, "d@example.com", google_sub="sub-1")


def test_email_unique(conn):
    _insert_user(conn, "a@example.com")
    with pytest.raises(IntegrityError, match="uq_users_email"):
        with conn.begin_nested():
            _insert_user(conn, "a@example.com")


def test_status_check_constraint(conn):
    _insert_notice(conn)
    with pytest.raises(IntegrityError, match="ck_scholarships_status"):
        with conn.begin_nested():
            conn.execute(
                text(
                    "INSERT INTO scholarships (notice_id, name, status, criteria)"
                    " SELECT id, '장학금', 'EXPIRED', '{}' FROM notices LIMIT 1"
                )
            )


def test_deleting_user_cascades_profile_and_states(conn):
    """4.2: 탈퇴 시 프로필·관심/신청완료 기록이 즉시 지워집니다."""
    _insert_user(conn, "a@example.com")
    _insert_notice(conn)
    conn.execute(
        text(
            "INSERT INTO user_profiles (user_id, name, gender, birth_year, gpa, credits, grade,"
            " extra_semester, enrollment_status, department, aibc_member)"
            " SELECT id, '홍길동', '여', 2004, 3.8, 18, 3, false, '재학', '소프트웨어학부', false"
            " FROM users"
        )
    )
    conn.execute(
        text(
            "INSERT INTO scholarships (notice_id, name, status, criteria)"
            " SELECT id, '장학금', 'OPEN', '{}' FROM notices"
        )
    )
    conn.execute(
        text(
            "INSERT INTO user_scholarship_states (user_id, scholarship_id, state)"
            " SELECT u.id, s.id, 'BOOKMARKED' FROM users u, scholarships s"
        )
    )
    conn.execute(text("DELETE FROM users"))
    assert conn.scalar(text("SELECT count(*) FROM user_profiles")) == 0
    assert conn.scalar(text("SELECT count(*) FROM user_scholarship_states")) == 0
    assert conn.scalar(text("SELECT count(*) FROM scholarships")) == 1


# --- 이메일 알림 (7.4) -------------------------------------------------------


def _insert_scholarship(conn):
    _insert_notice(conn)
    conn.execute(
        text(
            "INSERT INTO scholarships (notice_id, name, status, criteria)"
            " SELECT id, '장학금', 'OPEN', '{}' FROM notices"
        )
    )


def test_notification_columns(migrated_engine):
    insp = inspect(migrated_engine)
    users = {c["name"]: c for c in insp.get_columns("users")}
    assert users["email_notifications"]["nullable"] is False
    scholarships = {c["name"]: c for c in insp.get_columns("scholarships")}
    assert scholarships["created_at"]["nullable"] is False


def test_new_rows_get_notification_defaults(conn):
    _insert_user(conn, "a@example.com")
    _insert_scholarship(conn)
    assert conn.scalar(text("SELECT email_notifications FROM users")) is True
    assert conn.scalar(text("SELECT created_at IS NOT NULL FROM scholarships")) is True


def test_notification_log_kind_check_constraint(conn):
    _insert_user(conn, "a@example.com")
    _insert_scholarship(conn)
    with pytest.raises(IntegrityError, match="ck_notification_logs_kind"):
        with conn.begin_nested():
            conn.execute(
                text(
                    "INSERT INTO notification_logs (user_id, scholarship_id, kind, sent_at)"
                    " SELECT u.id, s.id, 'WRONG', now() FROM users u, scholarships s"
                )
            )


def test_deleting_user_removes_notification_logs(conn):
    """4.2: 탈퇴 시 알림 발송 기록도 지워집니다."""
    _insert_user(conn, "a@example.com")
    _insert_scholarship(conn)
    conn.execute(
        text(
            "INSERT INTO notification_logs (user_id, scholarship_id, kind, sent_at)"
            " SELECT u.id, s.id, 'NEW_MATCH', now() FROM users u, scholarships s"
        )
    )
    assert conn.scalar(text("SELECT count(*) FROM notification_logs")) == 1
    conn.execute(text("DELETE FROM users"))
    assert conn.scalar(text("SELECT count(*) FROM notification_logs")) == 0


def test_existing_scholarships_get_posted_at_as_created_at(migrated_engine, alembic_config):
    """기존 장학금은 공지 게시일을 created_at으로 받습니다(첫 발송에 몰리지 않게)."""
    command.downgrade(alembic_config, "a4c8e1f5b2d9")
    try:
        with migrated_engine.begin() as c:
            _insert_scholarship(c)
        command.upgrade(alembic_config, "head")
        with migrated_engine.connect() as c:
            created, posted = c.execute(
                text(
                    "SELECT s.created_at, n.posted_at FROM scholarships s"
                    " JOIN notices n ON n.id = s.notice_id"
                )
            ).one()
        assert created == posted
    finally:
        command.upgrade(alembic_config, "head")
        with migrated_engine.begin() as c:
            c.execute(text("TRUNCATE scholarships, notices RESTART IDENTITY CASCADE"))
