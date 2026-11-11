"""메일 발송: 가입 인증번호(계획서 7.3), 장학금 알림(7.4).

Gmail SMTP(`smtp.gmail.com:587`, STARTTLS)에 앱 비밀번호로 접속해 보냅니다. 새 라이브러리 없이
표준 라이브러리 `smtplib`을 쓰고, 앱 시작을 늦추지 않도록 보낼 때 import합니다(9.8).
받는 주소·인증번호·SMTP 비밀번호는 로그와 예외 메시지에 넣지 않습니다(4.2).

`mail_session()`은 접속·로그인을 한 번 하고 여러 통을 보냅니다(알림은 사용자 수만큼 보냄).
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from app.core.config import get_settings

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 10
SENDER_NAME = "장학금 매칭"


class MailError(Exception):
    """메일을 보내지 못함. 메시지에 주소·번호를 넣지 않습니다."""


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


SendFn = Callable[[OutgoingMail], None]  # 실패하면 MailError


def mail_configured() -> bool:
    """보낼 수 있는 설정인지. `MAIL_MOCK=1`이거나 SMTP 주소·비밀번호가 모두 있어야 합니다."""
    settings = get_settings()
    return settings.mail_mock or bool(settings.smtp_user and settings.smtp_password)


def _mail_error(exc: BaseException) -> MailError:
    # 예외 문자열에 받는 주소가 들어갈 수 있어 종류만 남깁니다.
    return MailError(f"메일 발송 실패: {type(exc).__name__}")


@contextmanager
def mail_session() -> Iterator[SendFn]:
    """접속·로그인을 한 번 하고, 메일을 한 통씩 보내는 함수를 돌려줍니다.

    접속·로그인 실패는 들어올 때 `MailError`, 한 통의 실패는 그 호출에서 `MailError`입니다.
    한 통이 실패해도 같은 접속으로 다음 메일을 보낼 수 있습니다.
    `MAIL_MOCK=1`이면 접속하지 않고, 보내는 함수는 아무것도 하지 않습니다.
    """
    settings = get_settings()
    if settings.mail_mock:
        yield lambda mail: None
        return
    if not settings.smtp_user or settings.smtp_password is None:
        raise MailError("SMTP 설정이 없습니다")

    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formataddr

    sender = formataddr((SENDER_NAME, settings.smtp_user))
    errors = (smtplib.SMTPException, OSError)
    try:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
    except errors as exc:
        raise _mail_error(exc) from None

    def send(mail: OutgoingMail) -> None:
        message = EmailMessage()
        message["Subject"] = mail.subject
        message["From"] = sender
        message["To"] = mail.to
        for name, value in mail.headers.items():
            message[name] = value
        message.set_content(mail.text)
        if mail.html is not None:
            message.add_alternative(mail.html, subtype="html")
        try:
            smtp.send_message(message)
        except errors as exc:
            raise _mail_error(exc) from None

    try:
        try:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        except errors as exc:
            raise _mail_error(exc) from None
        yield send
    finally:
        with suppress(*errors):  # 이미 끊긴 접속을 닫다가 난 오류는 무시
            smtp.quit()


def signup_code_message(code: str) -> tuple[str, str]:
    """(제목, 본문). 스팸으로 분류되지 않게 짧고 평범하게 씁니다."""
    subject = f"[{SENDER_NAME}] 인증번호 {code}"
    body = (
        f"장학금 매칭 회원가입 인증번호는 {code} 입니다.\n"
        "가입 화면에 입력해 주세요. 10분 동안 유효합니다.\n\n"
        "본인이 요청하지 않았다면 이 메일은 무시하셔도 됩니다.\n"
    )
    return subject, body


def send_signup_code(to: str, code: str) -> None:
    """인증번호 메일을 보냅니다. `MAIL_MOCK=1`이면 보내지 않습니다."""
    subject, body = signup_code_message(code)
    with mail_session() as send:
        send(OutgoingMail(to=to, subject=subject, text=body))
