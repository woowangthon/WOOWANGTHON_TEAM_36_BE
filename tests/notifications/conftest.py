"""알림 작업 테스트용 fixture. DB는 tests/models/conftest.py의 테스트 DB를 같이 씁니다."""

from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.mail import MailError, OutgoingMail
from app.models import Notice, Scholarship, User, UserProfile, UserScholarshipState
from app.parsers.dates import kst
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

TABLES = (
    "notification_logs, notification_runs, user_scholarship_states, user_profiles, users, "
    "scholarships, notices"
)
NO_LIMIT = {"target_department": "제한없음"}
ELIGIBLE = {**NO_LIMIT, "min_gpa": 3.0}
CONDITIONAL = {**NO_LIMIT, "max_income_bracket": 8}  # 프로필 소득분위 모름
INELIGIBLE = {**NO_LIMIT, "min_gpa": 4.0}

_counter = iter(range(1, 1_000_000))


@pytest.fixture
def session_factory(migrated_engine):  # noqa: F811
    """알림 작업은 직접 커밋하므로 롤백 대신 테스트 앞뒤로 표를 비웁니다."""

    def truncate() -> None:
        with migrated_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))

    truncate()
    yield sessionmaker(bind=migrated_engine)
    truncate()


@pytest.fixture(autouse=True)
def _notify_env(monkeypatch):
    monkeypatch.setenv("MAIL_MOCK", "1")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-" + "x" * 48)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


class Outbox:
    """`open_mail` 대역. 보낸 메일을 모으고, 실패를 흉내 냅니다."""

    def __init__(self) -> None:
        self.mails: list[OutgoingMail] = []
        self.fail_for: set[str] = set()
        self.open_fails = False
        self.opened = 0
        self.before_send = None  # 메일 한 통을 보내기 직전에 부를 함수(mail)

    @contextmanager
    def __call__(self):
        self.opened += 1
        if self.open_fails:
            raise MailError("메일 발송 실패: SMTPAuthenticationError")
        yield self._send

    def _send(self, mail: OutgoingMail) -> None:
        if self.before_send is not None:
            self.before_send(mail)
        if mail.to in self.fail_for:
            raise MailError("메일 발송 실패: SMTPRecipientsRefused")
        self.mails.append(mail)

    @property
    def recipients(self) -> list[str]:
        return [mail.to for mail in self.mails]


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


def add_user(
    factory,
    email: str = "student@example.com",
    *,
    with_profile: bool = True,
    consented: bool = True,
    notifications: bool = True,
) -> int:
    with factory() as session:
        user = User(
            email=email,
            token_version=0,
            privacy_agreed_at=kst(2026, 11, 1) if consented else None,
            email_notifications=notifications,
        )
        session.add(user)
        session.flush()
        if with_profile:
            session.add(
                UserProfile(
                    user_id=user.id,
                    name="길동",
                    gender="여",
                    birth_year=2004,
                    gpa=3.6,
                    score_100=88.0,
                    credits=15,
                    gpa_total=3.5,
                    score_100_total=87.0,
                    completed_semesters=5,
                    grade=3,
                    extra_semester=False,
                    enrollment_status="재학",
                    department="소프트웨어학부",
                    aibc_member=False,
                )
            )
        session.commit()
        return user.id


def add_scholarship(
    factory,
    name: str = "장학금",
    *,
    criteria: dict | None = None,
    status: str = "OPEN",
    kind: str = "모집",
    apply_end: datetime | None = None,
    created_at: datetime | None = None,
    display_name: str | None = None,
) -> int:
    n = next(_counter)
    with factory() as session:
        notice = Notice(
            source_code="SW_DEPT",
            external_id=f"ext-{n}",
            title="공지",
            posted_at=kst(2026, 11, 1),
            url=f"https://example.com/notice/{n}",
            body_text="본문",
            content_hash=f"{n:064d}",
            notice_kind=kind,
            extract_status="DONE",
        )
        session.add(notice)
        session.flush()
        scholarship = Scholarship(
            notice_id=notice.id,
            name=name,
            display_name=display_name,
            benefit_type="등록금성",
            apply_end=apply_end,
            status=status,
            criteria={"scholarship_name": name, **(ELIGIBLE if criteria is None else criteria)},
            created_at=created_at or kst(2026, 10, 1),
        )
        session.add(scholarship)
        session.commit()
        return scholarship.id


def bookmark(factory, user_id: int, scholarship_id: int, state: str = "BOOKMARKED") -> None:
    with factory() as session:
        session.add(
            UserScholarshipState(user_id=user_id, scholarship_id=scholarship_id, state=state)
        )
        session.commit()
