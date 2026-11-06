"""Google 로그인 (T20, 계획서 7.3). Google 토큰 교환·공개키는 목으로 바꿉니다(네트워크 없음)."""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.api import google
from app.core.security import AUTH_COOKIE, decode_access_token, hash_password
from app.parsers.dates import now_kst
from tests.api.test_auth import PASSWORD, add_user, api, factory, get_user, login  # noqa: F401
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

CLIENT_ID = "test-client.apps.googleusercontent.com"
REDIRECT_URI = "https://cbnu-scholarship.vercel.app/api/auth/google/callback"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    """PyJWKClient 대역: 항상 테스트 공개키를 돌려줍니다."""

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key(), as_dict=True)},
            algorithm="RS256",
        )


def id_token(
    *,
    sub: str = "google-sub-1",
    email: str = "student@gmail.com",
    email_verified: bool = True,
    aud: str = CLIENT_ID,
    iss: str = "https://accounts.google.com",
    exp_in: int = 3600,
    key=SIGNING_KEY,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_in,
    }
    return jwt.encode(claims, key, algorithm="RS256")


class FakeGoogle:
    """토큰 엔드포인트 목. 받은 요청을 기록하고 지정한 ID 토큰을 돌려줍니다."""

    def __init__(self) -> None:
        self.token = id_token()
        self.status = 200
        self.requests: list[dict[str, list[str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == google.TOKEN_URL
        self.requests.append(parse_qs(request.content.decode()))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"id_token": self.token, "access_token": "at"})


@pytest.fixture
def fake_google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def gapi(api: TestClient, fake_google: FakeGoogle) -> TestClient:  # noqa: F811
    oauth = google.GoogleOAuth(
        client_id=CLIENT_ID,
        client_secret="test-secret",
        redirect_uri=REDIRECT_URI,
        transport=httpx.MockTransport(fake_google.handler),
        jwks=FakeJwks(),
    )
    api.app.dependency_overrides[google.get_google_oauth] = lambda: oauth
    return api


def start(client: TestClient, next_path: str | None = None) -> dict[str, list[str]]:
    params = {"next": next_path} if next_path is not None else {}
    response = client.get("/auth/google/login", params=params, follow_redirects=False)
    assert response.status_code == 302
    assert google.FLOW_COOKIE in response.cookies
    return parse_qs(urlparse(response.headers["location"]).query)


def callback(client: TestClient, state: str, code: str | None = "auth-code"):
    params = {"state": state, **({"code": code} if code else {})}
    return client.get("/auth/google/callback", params=params, follow_redirects=False)


def login_with_google(client: TestClient, next_path: str | None = None):
    query = start(client, next_path)
    return callback(client, query["state"][0])


def session_user_id(response) -> int:
    return decode_access_token(response.cookies[AUTH_COOKIE]).user_id


# --- /login -------------------------------------------------------------------------


def test_login_redirects_to_google_with_pkce(gapi):
    response = gapi.get("/auth/google/login", follow_redirects=False)
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == google.AUTHORIZE_URL
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]  # 요청 호스트가 아니라 설정값
    assert query["scope"] == ["openid email profile"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=lax" in set_cookie
    assert "max-age=600" in set_cookie


def test_code_verifier_matches_challenge(gapi, fake_google):
    query = start(gapi)
    callback(gapi, query["state"][0])
    sent = fake_google.requests[0]
    assert sent["code"] == ["auth-code"]
    assert sent["redirect_uri"] == [REDIRECT_URI]
    assert create_s256_code_challenge(sent["code_verifier"][0]) == query["code_challenge"][0]


def test_unconfigured_google_is_503(api):  # noqa: F811
    assert api.get("/auth/google/login", follow_redirects=False).status_code == 503


# --- 계정 찾기·연결·생성 (7.3) ---------------------------------------------------------


def test_rule1_same_google_sub_logs_into_that_account(gapi, factory):  # noqa: F811
    user_id = add_user(factory, email="old@gmail.com", google_sub="google-sub-1")
    response = login_with_google(gapi)
    assert response.status_code == 302
    assert session_user_id(response) == user_id
    assert get_user(factory, user_id).email == "old@gmail.com"  # 이메일은 바꾸지 않음


def test_rule2_same_email_links_and_invalidates_password(gapi, factory):  # noqa: F811
    user_id = add_user(
        factory, email="student@gmail.com", password_hash=hash_password(PASSWORD), token_version=3
    )
    old_session = login(gapi, email="student@gmail.com").cookies[AUTH_COOKIE]

    response = login_with_google(gapi)

    assert session_user_id(response) == user_id
    user = get_user(factory, user_id)
    assert user.google_sub == "google-sub-1"
    assert user.password_hash is None  # 비밀번호 무효화
    assert user.token_version == 4  # 기존 세션 끊기
    gapi.cookies.set(AUTH_COOKIE, old_session)
    assert gapi.get("/auth/me").status_code == 401
    assert login(gapi, email="student@gmail.com").status_code == 400  # Google로 로그인 안내


def test_rule2_email_case_is_ignored(gapi, factory, fake_google):  # noqa: F811
    user_id = add_user(factory, email="student@gmail.com", password_hash=hash_password(PASSWORD))
    fake_google.token = id_token(email="Student@Gmail.com")
    assert session_user_id(login_with_google(gapi)) == user_id


def test_rule3_new_account_without_password(gapi, factory):  # noqa: F811
    response = login_with_google(gapi)
    user = get_user(factory, session_user_id(response))
    assert user.email == "student@gmail.com"
    assert user.google_sub == "google-sub-1"
    assert user.password_hash is None
    assert user.privacy_agreed_at is None
    assert response.headers["location"] == google.CONSENT_PATH  # 동의 전이면 동의 화면


def test_email_linked_to_other_google_account_is_rejected(gapi, factory):  # noqa: F811
    add_user(factory, email="student@gmail.com", google_sub="someone-else")
    response = login_with_google(gapi)
    assert response.headers["location"] == google.LOGIN_ERROR_PATH
    assert AUTH_COOKIE not in response.cookies


def test_consented_user_goes_to_next(gapi, factory):  # noqa: F811
    add_user(
        factory, email="student@gmail.com", google_sub="google-sub-1", privacy_agreed_at=now_kst()
    )
    response = login_with_google(gapi, "/scholarships/3?tab=all")
    assert response.headers["location"] == "/scholarships/3?tab=all"
    assert 'google_oauth=""' in response.headers.get_list("set-cookie")[0]  # 흐름 쿠키 삭제


def test_admin_requires_google_login(gapi, factory, fake_google):  # noqa: F811
    add_user(
        factory,
        email="admin@gmail.com",
        password_hash=hash_password(PASSWORD),
        privacy_agreed_at=now_kst(),
    )
    assert login(gapi, email="admin@gmail.com").json()["is_admin"] is False
    fake_google.token = id_token(sub="admin-sub", email="admin@gmail.com")
    login_with_google(gapi)
    assert gapi.get("/auth/me").json()["is_admin"] is True


# --- 오류·보안 -----------------------------------------------------------------------


def test_state_mismatch_is_rejected(gapi, fake_google, factory):  # noqa: F811
    start(gapi)
    response = callback(gapi, "attacker-state")
    assert response.headers["location"] == google.LOGIN_ERROR_PATH
    assert AUTH_COOKIE not in response.cookies
    assert fake_google.requests == []  # 토큰 교환도 하지 않음


def test_callback_without_flow_cookie_is_rejected(gapi, fake_google):
    query = start(gapi)
    gapi.cookies.clear()
    assert callback(gapi, query["state"][0]).headers["location"] == google.LOGIN_ERROR_PATH
    assert fake_google.requests == []


def test_tampered_flow_cookie_is_rejected(gapi):
    query = start(gapi)
    forged = jwt.encode(
        {"aud": google.FLOW_AUDIENCE, "state": "s", "cv": "v", "next": "/", "exp": 2**31},
        "wrong-secret-" + "x" * 32,
        algorithm="HS256",
    )
    gapi.cookies.set(google.FLOW_COOKIE, forged)
    assert callback(gapi, query["state"][0]).headers["location"] == google.LOGIN_ERROR_PATH


def test_user_cancelled_consent(gapi, fake_google):
    query = start(gapi)
    response = callback(gapi, query["state"][0], code=None)
    assert response.headers["location"] == google.LOGIN_ERROR_PATH
    assert fake_google.requests == []


def test_token_exchange_failure(gapi, fake_google):
    fake_google.status = 400
    assert login_with_google(gapi).headers["location"] == google.LOGIN_ERROR_PATH


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"aud": "other-client"},
        {"iss": "https://evil.example.com"},
        {"exp_in": -120},
        {"email_verified": False},
        {"key": OTHER_KEY},  # 서명이 Google 키가 아님
    ],
    ids=["aud", "iss", "exp", "email_verified", "signature"],
)
def test_invalid_id_token_is_rejected(gapi, fake_google, factory, token_kwargs):  # noqa: F811
    fake_google.token = id_token(**token_kwargs)
    response = login_with_google(gapi)
    assert response.headers["location"] == google.LOGIN_ERROR_PATH
    assert AUTH_COOKIE not in response.cookies


@pytest.mark.parametrize(
    ("next_path", "expected"),
    [
        (None, "/dashboard"),
        ("/profile", "/profile"),
        ("https://evil.com", "/dashboard"),
        ("//evil.com", "/dashboard"),
        ("/\\evil.com", "/dashboard"),
        ("evil.com", "/dashboard"),
        ("/ok\r\nSet-Cookie: x=1", "/dashboard"),
    ],
)
def test_external_next_is_blocked(gapi, factory, next_path, expected):  # noqa: F811
    add_user(
        factory, email="student@gmail.com", google_sub="google-sub-1", privacy_agreed_at=now_kst()
    )
    assert login_with_google(gapi, next_path).headers["location"] == expected
