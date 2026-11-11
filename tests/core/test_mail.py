"""메일 발송 (계획서 7.3 가입 인증번호, 7.4 알림). SMTP 서버에는 접속하지 않습니다."""

import smtplib

import pytest

from app.core.mail import (
    MailError,
    OutgoingMail,
    mail_configured,
    mail_session,
    send_signup_code,
)

USER = "service@example.com"
PASSWORD = "app-password-value"


class FakeSMTP:
    """`smtplib.SMTP` 대역. 인스턴스를 모아 두고 호출을 기록합니다."""

    instances: list["FakeSMTP"] = []
    fail_for: set[str] = set()

    def __init__(self, host, port, timeout=None):
        self.address = (host, port)
        self.tls = False
        self.logins: list[tuple[str, str]] = []
        self.sent = []
        self.closed = False
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, message):
        if message["To"] in FakeSMTP.fail_for:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        self.sent.append(message)

    def quit(self):
        self.closed = True


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_for = set()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", USER)
    monkeypatch.setenv("SMTP_PASSWORD", PASSWORD)


def test_session_connects_once_and_sends_each_mail(fake_smtp, smtp_env):
    with mail_session() as send:
        send(OutgoingMail("a@example.com", "제목1", "본문1"))
        send(OutgoingMail("b@example.com", "제목2", "본문2"))

    [smtp] = fake_smtp.instances
    assert smtp.address == ("smtp.gmail.com", 587)
    assert smtp.tls and smtp.logins == [(USER, PASSWORD)]
    assert [(m["To"], m["Subject"]) for m in smtp.sent] == [
        ("a@example.com", "제목1"),
        ("b@example.com", "제목2"),
    ]
    assert USER in smtp.sent[0]["From"]
    assert smtp.closed


def test_html_and_headers_are_attached(fake_smtp, smtp_env):
    mail = OutgoingMail(
        "a@example.com", "제목", "본문", html="<p>본문</p>", headers={"List-Unsubscribe": "<u>"}
    )

    with mail_session() as send:
        send(mail)

    message = fake_smtp.instances[0].sent[0]
    assert message["List-Unsubscribe"] == "<u>"
    assert "본문" in message.get_body(("plain",)).get_content()
    assert "<p>본문</p>" in message.get_body(("html",)).get_content()


def test_failed_mail_raises_mail_error_and_session_continues(fake_smtp, smtp_env):
    fake_smtp.fail_for = {"a@example.com"}

    with mail_session() as send:
        with pytest.raises(MailError) as error:
            send(OutgoingMail("a@example.com", "제목", "본문"))
        send(OutgoingMail("b@example.com", "제목", "본문"))

    assert "a@example.com" not in str(error.value)
    assert [m["To"] for m in fake_smtp.instances[0].sent] == ["b@example.com"]


def test_connect_failure_raises_mail_error(monkeypatch, smtp_env):
    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)

    with pytest.raises(MailError) as error, mail_session():
        pass

    assert PASSWORD not in str(error.value)


def test_login_failure_raises_mail_error_and_closes(fake_smtp, smtp_env, monkeypatch):
    def reject(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(FakeSMTP, "login", reject)

    with pytest.raises(MailError), mail_session():
        pass

    assert fake_smtp.instances[0].closed


def test_missing_settings_raise_mail_error(fake_smtp):
    with pytest.raises(MailError), mail_session():
        pass

    assert fake_smtp.instances == []


def test_mock_mode_never_connects(fake_smtp, monkeypatch):
    monkeypatch.setenv("MAIL_MOCK", "1")

    with mail_session() as send:
        send(OutgoingMail("a@example.com", "제목", "본문"))

    assert fake_smtp.instances == []


def test_error_in_caller_body_is_not_swallowed(fake_smtp, smtp_env):
    with pytest.raises(ZeroDivisionError), mail_session():
        1 / 0  # noqa: B018

    assert fake_smtp.instances[0].closed


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"MAIL_MOCK": "1"}, True),
        ({"SMTP_USER": USER, "SMTP_PASSWORD": PASSWORD}, True),
        ({"SMTP_USER": USER}, False),
        ({"SMTP_PASSWORD": PASSWORD}, False),
    ],
)
def test_mail_configured(monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert mail_configured() is expected


def test_signup_code_goes_through_the_session(fake_smtp, smtp_env):
    send_signup_code("a@example.com", "123456")

    [message] = fake_smtp.instances[0].sent
    assert message["To"] == "a@example.com"
    assert message["Subject"] == "[장학금 매칭] 인증번호 123456"
    assert "123456" in message.get_content()
