"""가입 인증번호 (계획서 7.3): 번호 받기 → 확인 → 가입. 실제 메일은 보내지 않습니다."""

# 픽스처(api, factory)를 가져와 인자로 받으므로 재정의 경고는 끕니다.
# ruff: noqa: F811

from datetime import timedelta

import jwt
import pytest

from app.api import auth as auth_api
from app.core import security
from app.core.mail import MailError, signup_code_message
from app.core.security import (
    AUTH_COOKIE,
    WindowRateLimiter,
    create_access_token,
    create_signup_code_token,
    create_signup_token,
)
from app.parsers.dates import now_kst
from tests.api.test_auth import (  # noqa: F401
    PASSWORD,
    add_user,
    api,
    factory,
    get_user,
    reset_limiters,
    signup_token,
)
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

EMAIL = "student@example.com"


@pytest.fixture
def sent(api, monkeypatch):
    """MAIL_MOCK을 끄고 메일 발송을 가로챕니다. 실제 무작위 번호가 쓰입니다."""
    monkeypatch.delenv("MAIL_MOCK")
    security.get_settings.cache_clear()
    mails: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_api, "send_signup_code", lambda to, code: mails.append((to, code)))
    return mails


def request_code(api, email=EMAIL):
    return api.post("/auth/signup/code", json={"email": email})


def verify(api, code_token, code):
    return api.post("/auth/signup/verify", json={"code_token": code_token, "code": code})


# --- 1단계: 인증번호 받기 ------------------------------------------------------------


def test_code_is_sent_and_not_in_token(api, sent):
    response = request_code(api, email="Student@Example.COM")

    assert response.status_code == 200
    body = response.json()
    assert (body["expires_in"], body["resend_after"]) == (600, 60)
    [(to, code)] = sent
    assert to == EMAIL  # 소문자로 정리
    assert len(code) == 6 and code.isdigit()
    payload = jwt.decode(body["code_token"], options={"verify_signature": False})
    assert payload["purpose"] == "signup_code" and payload["email"] == EMAIL
    assert code not in body["code_token"] and code not in str(payload)  # 번호는 토큰에 없음


def test_full_signup_with_real_code(api, sent, factory):
    code_token = request_code(api).json()["code_token"]
    [(_, code)] = sent

    token = verify(api, code_token, code).json()["signup_token"]
    response = api.post("/auth/signup", json={"signup_token": token, "password": PASSWORD})

    assert response.status_code == 201
    assert response.json()["email"] == EMAIL
    assert get_user(factory, 1).email == EMAIL
    assert api.get("/auth/me").status_code == 200


def test_code_for_registered_email_is_409(api, sent, factory):
    add_user(factory, email=EMAIL)
    response = request_code(api, email="STUDENT@example.com")
    assert response.status_code == 409
    assert response.json()["detail"] == "이미 가입된 이메일입니다"
    assert sent == []


@pytest.mark.parametrize("email", ["not-an-email", "", "a@b", "x" * 321 + "@example.com"])
def test_code_invalid_email_is_422(api, sent, email):
    assert request_code(api, email=email).status_code == 422
    assert sent == []


def test_code_again_within_a_minute_is_429(api, sent):
    assert request_code(api).status_code == 200
    response = request_code(api)
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert len(sent) == 1
    assert request_code(api, email="other@example.com").status_code == 200  # 다른 이메일은 됨


def test_mail_failure_is_502_without_address_in_log(api, monkeypatch, caplog):
    def fail(to, code):
        raise MailError("메일 발송 실패: SMTPAuthenticationError")

    monkeypatch.setattr(auth_api, "send_signup_code", fail)
    caplog.set_level("DEBUG")

    response = request_code(api)

    assert response.status_code == 502
    assert response.json()["detail"] == "메일을 보내지 못했어요. 잠시 후 다시 시도하세요"
    assert EMAIL not in caplog.text
    assert "code_token" not in response.text


def test_code_and_email_are_not_logged(api, sent, caplog):
    caplog.set_level("DEBUG")
    code_token = request_code(api).json()["code_token"]
    [(_, code)] = sent
    token = verify(api, code_token, code).json()["signup_token"]
    api.post("/auth/signup", json={"signup_token": token, "password": PASSWORD})

    assert code not in caplog.text
    assert EMAIL not in caplog.text
    assert code_token not in caplog.text and token not in caplog.text


# --- 2단계: 인증번호 확인 ------------------------------------------------------------


def test_wrong_code_is_400(api, sent):
    code_token = request_code(api).json()["code_token"]
    [(_, code)] = sent
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    response = verify(api, code_token, wrong)

    assert response.status_code == 400
    assert response.json()["detail"] == "인증번호가 맞지 않아요"


def test_expired_code_is_400(api):
    old = now_kst() - timedelta(minutes=11)
    code_token = create_signup_code_token(EMAIL, "123456", now=old)

    response = verify(api, code_token, "123456")

    assert response.status_code == 400
    assert response.json()["detail"] == "인증번호가 만료됐어요. 다시 받아 주세요"


def test_code_from_other_email_does_not_match(api):
    code_token = create_signup_code_token("other@example.com", "123456")
    tampered = jwt.encode(
        {**jwt.decode(code_token, options={"verify_signature": False}), "email": EMAIL},
        "test-jwt-secret-" + "x" * 48,
        algorithm="HS256",
    )
    # 이메일만 바꾸면 HMAC이 맞지 않음
    assert verify(api, tampered, "123456").status_code == 400


@pytest.mark.parametrize(
    "make_token",
    [
        lambda: create_signup_token(EMAIL),  # 3단계용 토큰
        lambda: create_access_token(1, 0),  # 로그인 JWT
        lambda: jwt.encode({"purpose": "signup_code", "email": EMAIL, "exp": 9e9}, "wrong-key"),
        lambda: "not-a-token",
    ],
)
def test_other_tokens_cannot_verify(api, make_token):
    response = verify(api, make_token(), "123456")
    assert response.status_code == 400
    assert response.json()["detail"] == "이메일 인증을 처음부터 다시 해 주세요"


@pytest.mark.parametrize("code", ["12345", "1234567", "12a456", "", "١٢٣٤٥٦"])
def test_code_format_is_422(api, code):
    response = verify(api, create_signup_code_token(EMAIL, "123456"), code)
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "인증번호 6자리 숫자를 입력하세요"


def test_verify_limit_per_email_is_429(api):
    code_token = create_signup_code_token(EMAIL, "123456")
    for _ in range(5):
        assert verify(api, code_token, "000001").status_code == 400
    assert verify(api, code_token, "123456").status_code == 429  # 맞는 번호도 막힘
    reset_limiters()
    assert verify(api, code_token, "123456").status_code == 200


# --- 3단계: 가입 ----------------------------------------------------------------


def test_signup_uses_email_from_token(api, factory):
    token = create_signup_token(EMAIL)
    response = api.post("/auth/signup", json={"signup_token": token, "password": PASSWORD})
    assert response.status_code == 201
    assert get_user(factory, 1).email == EMAIL


def test_expired_signup_token_is_400(api, factory):
    token = create_signup_token(EMAIL, now=now_kst() - timedelta(minutes=31))
    response = api.post("/auth/signup", json={"signup_token": token, "password": PASSWORD})
    assert response.status_code == 400
    assert response.json()["detail"] == "인증 시간이 지났어요. 이메일 인증을 다시 해 주세요"
    assert get_user(factory, 1) is None


@pytest.mark.parametrize(
    "make_token",
    [
        lambda: create_signup_code_token(EMAIL, "123456"),  # 2단계를 건너뜀
        lambda: create_access_token(1, 0),
        lambda: "not-a-token",
    ],
)
def test_signup_rejects_other_tokens(api, factory, make_token):
    response = api.post("/auth/signup", json={"signup_token": make_token(), "password": PASSWORD})
    assert response.status_code == 400
    assert get_user(factory, 1) is None


def test_signup_after_someone_else_registered_is_409(api, factory):
    token = signup_token(api)
    add_user(factory, email=EMAIL, google_sub="g-1")  # 그 사이 Google로 가입
    response = api.post("/auth/signup", json={"signup_token": token, "password": PASSWORD})
    assert response.status_code == 409


def test_signup_token_is_not_a_session(api):
    api.cookies.set(AUTH_COOKIE, create_signup_token(EMAIL))
    assert api.get("/auth/me").status_code == 401


# --- 시도 제한·메일 내용 (단위) -------------------------------------------------------


def test_window_limiter_rules():
    now = [0.0]
    limiter = WindowRateLimiter(
        {"email_minute": (1, 60.0), "email_hour": (5, 3600.0), "ip_hour": (20, 3600.0)},
        "제한",
        clock=lambda: now[0],
    )
    for i in range(5):
        now[0] = i * 61.0
        limiter.hit(email_minute="a", email_hour="a", ip_hour="1.1.1.1")
    now[0] = 5 * 61.0
    with pytest.raises(Exception) as exc:  # 1시간 6번째
        limiter.hit(email_minute="a", email_hour="a", ip_hour="1.1.1.1")
    assert exc.value.status_code == 429
    now[0] = 3601.0  # 첫 시도가 1시간 창을 벗어남
    limiter.hit(email_minute="a", email_hour="a", ip_hour="1.1.1.1")


def test_window_limiter_ip_rule():
    now = [0.0]
    limiter = WindowRateLimiter({"ip_hour": (20, 3600.0)}, "제한", clock=lambda: now[0])
    for _ in range(20):
        limiter.hit(ip_hour="1.1.1.1")
    with pytest.raises(Exception) as exc:
        limiter.hit(ip_hour="1.1.1.1")
    assert exc.value.status_code == 429
    limiter.hit(ip_hour="2.2.2.2")


def test_signup_code_message_has_only_code_and_notice():
    subject, body = signup_code_message("482913")
    assert subject == "[장학금 매칭] 인증번호 482913"
    assert "482913" in body and "10분" in body and "무시" in body


def test_mail_without_smtp_settings_raises(monkeypatch):
    from app.core.mail import send_signup_code

    security.get_settings.cache_clear()
    with pytest.raises(MailError):
        send_signup_code(EMAIL, "123456")
