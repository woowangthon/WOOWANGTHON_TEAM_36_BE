"""비밀번호 찾기 (계획서 7.3): 인증번호 받기 → 번호 확인 → 새 비밀번호."""

# 픽스처(api, factory)를 가져와 인자로 받으므로 재정의 경고는 끕니다.
# ruff: noqa: F811

from datetime import timedelta

import jwt
import pytest

from app.api import auth as auth_api
from app.core import security
from app.core.mail import MailError, password_reset_message
from app.core.security import (
    AUTH_COOKIE,
    create_reset_code_token,
    create_reset_token,
    create_signup_code_token,
    create_signup_token,
    hash_password,
)
from app.parsers.dates import now_kst
from tests.api.test_auth import (  # noqa: F401
    PASSWORD,
    add_user,
    api,
    factory,
    get_user,
    login,
    reset_limiters,
    signup,
)
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

EMAIL = "student@example.com"
NEW_PASSWORD = "newpass34!"


@pytest.fixture
def sent(api, monkeypatch):
    """MAIL_MOCK을 끄고 메일 발송을 가로챕니다. 실제 무작위 번호가 쓰입니다."""
    monkeypatch.delenv("MAIL_MOCK")
    security.get_settings.cache_clear()
    mails: list[tuple[str, str]] = []
    monkeypatch.setattr(
        auth_api, "send_password_reset_code", lambda to, code: mails.append((to, code))
    )
    return mails


def request_code(api, email=EMAIL):
    return api.post("/auth/password-reset/code", json={"email": email})


def verify(api, code_token, code):
    return api.post("/auth/password-reset/verify", json={"code_token": code_token, "code": code})


def set_password(api, reset_token, password=NEW_PASSWORD):
    return api.post(
        "/auth/password-reset", json={"reset_token": reset_token, "new_password": password}
    )


def reset(api, code_token, code, password=NEW_PASSWORD):
    """2·3단계를 이어서. 번호가 틀리면 2단계 응답을 돌려줍니다."""
    checked = verify(api, code_token, code)
    if checked.status_code != 200:
        return checked
    return set_password(api, checked.json()["reset_token"], password)


def test_reset_with_real_code_changes_password_and_logs_in(api, sent, factory):
    user_id = add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))

    body = request_code(api, email="Student@Example.COM").json()
    [(to, code)] = sent
    assert to == EMAIL
    payload = jwt.decode(body["code_token"], options={"verify_signature": False})
    assert payload["purpose"] == "password_reset_code" and code not in str(payload)

    response = reset(api, body["code_token"], code)

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert AUTH_COOKIE in response.cookies
    user = get_user(factory, user_id)
    assert user.token_version == 1
    assert login(api, password=PASSWORD).status_code == 401
    assert login(api, password=NEW_PASSWORD).status_code == 200


def test_unknown_email_is_404_without_mail(api, sent):
    response = request_code(api, email="nobody@example.com")

    assert response.status_code == 404
    assert response.json()["detail"] == "가입되지 않은 이메일입니다"
    assert sent == []


def test_deleted_user_cannot_verify(api):
    code_token = create_reset_code_token("gone@example.com", "000000")

    assert verify(api, code_token, "000000").status_code == 400


def test_wrong_code_is_400(api, sent, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    code_token = request_code(api).json()["code_token"]
    [(_, code)] = sent
    wrong = "000000" if code != "000000" else "111111"

    response = reset(api, code_token, wrong)

    assert response.status_code == 400
    assert response.json()["detail"] == "인증번호가 맞지 않아요"
    assert login(api, password=PASSWORD).status_code == 200


def test_expired_code_is_400(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    old = create_reset_code_token(EMAIL, "000000", now=now_kst() - timedelta(minutes=11))

    response = reset(api, old, "000000")

    assert response.status_code == 400
    assert response.json()["detail"] == "인증번호가 만료됐어요. 다시 받아 주세요"


def test_signup_code_token_cannot_reset(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    signup_code_token = create_signup_code_token(EMAIL, "000000")

    response = reset(api, signup_code_token, "000000")

    assert response.status_code == 400
    assert login(api, password=PASSWORD).status_code == 200


def test_reset_code_token_cannot_be_used_for_signup(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    reset_token = request_code(api).json()["code_token"]

    response = api.post("/auth/signup/verify", json={"code_token": reset_token, "code": "000000"})

    assert response.status_code == 400


def test_reset_logs_out_other_sessions(api, factory):
    signup(api)
    old_cookie = api.cookies.get(AUTH_COOKIE)
    reset_limiters()  # 가입 인증번호와 발송 제한을 같이 씀
    code_token = request_code(api).json()["code_token"]

    reset(api, code_token, "000000")
    api.cookies.clear()
    api.cookies.set(AUTH_COOKIE, old_cookie)

    assert api.get("/auth/me").status_code == 401


def test_google_only_account_can_set_password(api, factory):
    user_id = add_user(factory, email=EMAIL, google_sub="google-1", password_hash=None)
    code_token = request_code(api).json()["code_token"]

    response = reset(api, code_token, "000000")

    assert response.status_code == 200
    assert response.json()["has_password"] is True
    assert get_user(factory, user_id).google_sub == "google-1"
    assert login(api, password=NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("password", ["short1!", "noDigits!!", "nospecial12"])
def test_weak_new_password_is_422(api, factory, password):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    code_token = request_code(api).json()["code_token"]

    assert reset(api, code_token, "000000", password=password).status_code == 422


def test_mail_failure_is_502(api, factory, monkeypatch):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))

    def fail(to, code):
        raise MailError("smtp")

    monkeypatch.setattr(auth_api, "send_password_reset_code", fail)

    assert request_code(api).status_code == 502


def test_message_has_code():
    subject, body = password_reset_message("123456")
    assert "123456" in subject and "123456" in body


def test_wrong_code_does_not_give_reset_token(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    code_token = request_code(api).json()["code_token"]

    response = verify(api, code_token, "123456")

    assert response.status_code == 400
    assert "reset_token" not in response.json()


def test_expired_reset_token_is_400(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))
    old = create_reset_token(EMAIL, now=now_kst() - timedelta(minutes=31))

    response = set_password(api, old)

    assert response.status_code == 400
    assert login(api, password=PASSWORD).status_code == 200


def test_signup_token_cannot_set_password(api, factory):
    add_user(factory, email=EMAIL, password_hash=hash_password(PASSWORD))

    assert set_password(api, create_signup_token(EMAIL)).status_code == 400
    assert login(api, password=PASSWORD).status_code == 200
