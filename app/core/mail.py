"""가입 인증번호 메일 (계획서 7.3).

Gmail SMTP(`smtp.gmail.com:587`, STARTTLS)에 앱 비밀번호로 접속해 보냅니다. 새 라이브러리 없이
표준 라이브러리 `smtplib`을 쓰고, 앱 시작을 늦추지 않도록 보낼 때 import합니다(9.8).
받는 주소·인증번호·SMTP 비밀번호는 로그와 예외 메시지에 넣지 않습니다(4.2).
"""

from app.core.config import get_settings

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 10
SENDER_NAME = "장학금 매칭"


class MailError(Exception):
    """메일을 보내지 못함. 메시지에 주소·번호를 넣지 않습니다."""


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
    settings = get_settings()
    if settings.mail_mock:
        return
    if not settings.smtp_user or settings.smtp_password is None:
        raise MailError("SMTP 설정이 없습니다")

    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formataddr

    subject, body = signup_code_message(code)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((SENDER_NAME, settings.smtp_user))
    message["To"] = to
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # 예외 문자열에 받는 주소가 들어갈 수 있어 종류만 남깁니다.
        raise MailError(f"메일 발송 실패: {type(exc).__name__}") from None
