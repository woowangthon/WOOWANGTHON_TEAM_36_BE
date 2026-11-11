"""알림 수신 설정·수신 거부 API (T35, 계획서 7.2·7.4). 테스트 DB를 씁니다."""

import pytest
from fastapi.testclient import TestClient

from app.core.security import AUTH_COOKIE, create_unsubscribe_token
from app.models import User
from tests.api.test_auth import api, factory, signup  # noqa: F401
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401


@pytest.fixture
def anonymous(api) -> TestClient:  # noqa: F811
    """같은 앱에 쿠키 없이 요청하는 클라이언트(메일 링크를 누른 사람)."""
    return TestClient(api.app, base_url="https://testserver")


def setting(factory, user_id: int) -> bool:  # noqa: F811
    with factory() as session:
        return session.get_one(User, user_id).email_notifications


def unsubscribe(client, token):
    return client.post("/notifications/unsubscribe", json={"token": token})


# --- 수신 설정 ------------------------------------------------------------------


def test_me_has_email_notifications_on_by_default(api):  # noqa: F811
    assert signup(api).json()["email_notifications"] is True
    assert api.get("/auth/me").json()["email_notifications"] is True


def test_toggle_requires_login(api):  # noqa: F811
    response = api.put("/auth/me/notifications", json={"email_notifications": False})

    assert response.status_code == 401


def test_toggle_off_and_on(api):  # noqa: F811
    signup(api)

    off = api.put("/auth/me/notifications", json={"email_notifications": False})

    assert off.status_code == 200
    assert off.json()["email_notifications"] is False
    assert api.get("/auth/me").json()["email_notifications"] is False

    on = api.put("/auth/me/notifications", json={"email_notifications": True})

    assert on.json()["email_notifications"] is True
    assert api.get("/auth/me").json()["email_notifications"] is True


@pytest.mark.parametrize(
    "body", [{}, {"email_notifications": "아니오"}, {"email_notifications": False, "x": 1}]
)
def test_toggle_rejects_bad_body(api, body):  # noqa: F811
    signup(api)

    assert api.put("/auth/me/notifications", json=body).status_code == 422


# --- 수신 거부 (로그인 없이) ---------------------------------------------------------


def test_unsubscribe_with_valid_token_needs_no_login(api, anonymous, factory):  # noqa: F811
    user_id = signup(api).json()["id"]

    response = unsubscribe(anonymous, create_unsubscribe_token(user_id))

    assert response.status_code == 204
    assert setting(factory, user_id) is False
    assert api.get("/auth/me").json()["email_notifications"] is False


def test_unsubscribe_is_idempotent(api, anonymous, factory):  # noqa: F811
    user_id = signup(api).json()["id"]
    token = create_unsubscribe_token(user_id)

    assert unsubscribe(anonymous, token).status_code == 204
    assert unsubscribe(anonymous, token).status_code == 204
    assert setting(factory, user_id) is False


def test_unsubscribe_only_changes_that_user(api, anonymous, factory):  # noqa: F811
    first = signup(api, email="a@example.com").json()["id"]
    second = signup(api, email="b@example.com").json()["id"]

    unsubscribe(anonymous, create_unsubscribe_token(first))

    assert setting(factory, first) is False
    assert setting(factory, second) is True


@pytest.mark.parametrize("token", ["", "abc", "a.b.c"])
def test_unsubscribe_rejects_bad_token(api, anonymous, token):  # noqa: F811
    response = unsubscribe(anonymous, token)

    assert response.status_code == 400
    assert response.json()["detail"] == "유효하지 않은 링크입니다"


def test_unsubscribe_rejects_login_jwt(api, anonymous, factory):  # noqa: F811
    user_id = signup(api).json()["id"]

    response = unsubscribe(anonymous, api.cookies.get(AUTH_COOKIE))

    assert response.status_code == 400
    assert setting(factory, user_id) is True


def test_unsubscribe_for_deleted_user_is_204(api, anonymous):  # noqa: F811
    assert unsubscribe(anonymous, create_unsubscribe_token(999_999)).status_code == 204


def test_unsubscribe_token_is_not_logged(api, anonymous, caplog):  # noqa: F811
    user_id = signup(api).json()["id"]
    token = create_unsubscribe_token(user_id)
    caplog.set_level("DEBUG")

    unsubscribe(anonymous, token)

    assert token not in caplog.text


def test_unsubscribe_rejects_extra_fields_and_long_token(api, anonymous):  # noqa: F811
    assert (
        anonymous.post("/notifications/unsubscribe", json={"token": "x" * 1025}).status_code == 422
    )
    assert (
        anonymous.post("/notifications/unsubscribe", json={"token": "a", "user_id": 1}).status_code
        == 422
    )
