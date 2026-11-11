"""인증 core (T18, 계획서 7.3·9.2)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import security
from app.core.db import get_db
from app.core.security import (
    AUTH_COOKIE,
    TOKEN_TTL,
    AdminUser,
    ConsentedUser,
    CurrentUser,
    InvalidTokenError,
    LoginRateLimiter,
    check_login_rate,
    client_ip,
    create_access_token,
    decode_access_token,
    hash_password,
    issue_session,
    verify_password,
)
from app.models import User
from app.parsers.dates import kst, now_kst

SECRET = "test-jwt-secret-" + "x" * 48


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_EMAILS", "admin@gmail.com, Boss@Gmail.com")
    security.login_limiter.reset()
    yield
    security.login_limiter.reset()


# --- 비밀번호 ---------------------------------------------------------------


def test_password_hash_and_verify():
    hashed = hash_password("correct horse")
    assert hashed.startswith("$argon2")
    assert "correct horse" not in hashed
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong", hashed)


def test_google_only_account_has_no_password():
    assert not verify_password("anything", None)
    assert not verify_password("anything", "")
    assert not verify_password("anything", "broken-hash")


# --- JWT -------------------------------------------------------------------


def test_token_round_trip():
    claims = decode_access_token(create_access_token(7, 2))
    assert (claims.user_id, claims.token_version) == (7, 2)


def test_token_expires_after_7_days():
    issued = now_kst() - timedelta(days=1)
    token = create_access_token(1, 0, now=issued)
    decode_access_token(token, now=issued + TOKEN_TTL - timedelta(seconds=1))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, now=issued + TOKEN_TTL)


def test_expired_token_is_rejected():
    token = create_access_token(1, 0, now=kst(2026, 1, 1, 0, 0))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_signed_with_other_secret_is_rejected(monkeypatch):
    token = create_access_token(1, 0)
    monkeypatch.setenv("JWT_SECRET", "other-secret-" + "y" * 48)
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c"])
def test_garbage_token_is_rejected(token):
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_none_algorithm_is_rejected():
    import jwt

    token = jwt.encode({"sub": "1", "ver": 0, "exp": 9999999999}, None, algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


# --- 의존성 (DB 대신 가짜 세션) ----------------------------------------------------


def make_user(**kwargs) -> User:
    defaults = {
        "id": 1,
        "email": "user@example.com",
        "password_hash": None,
        "google_sub": None,
        "token_version": 0,
        "privacy_agreed_at": kst(2026, 11, 1, 9, 0),
        "sensitive_agreed_at": None,
    }
    return User(**{**defaults, **kwargs})


class FakeDB:
    def __init__(self, *users: User) -> None:
        self.users = {u.id: u for u in users}

    def get(self, model, user_id):
        assert model is User
        return self.users.get(user_id)


def make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db

    @app.get("/me")
    def me(user: CurrentUser):
        return {"id": user.id}

    @app.get("/profile")
    def profile(user: ConsentedUser):
        return {"id": user.id}

    @app.get("/admin")
    def admin(user: AdminUser):
        return {"id": user.id}

    @app.post("/login")
    def login(request: Request, email: str):
        check_login_rate(request, email)
        return {"ok": True}

    return TestClient(app, base_url="https://testserver")


def with_cookie(client: TestClient, user_id: int, version: int) -> TestClient:
    client.cookies.set(AUTH_COOKIE, create_access_token(user_id, version))
    return client


def test_current_user_with_valid_cookie():
    client = with_cookie(make_client(FakeDB(make_user(id=5, token_version=3))), 5, 3)
    assert client.get("/me").json() == {"id": 5}


def test_missing_cookie_is_401():
    assert make_client(FakeDB(make_user())).get("/me").status_code == 401


def test_token_version_mismatch_is_401():
    """비밀번호 무효화·탈퇴로 DB의 token_version이 올라가면 기존 쿠키는 401."""
    client = with_cookie(make_client(FakeDB(make_user(id=5, token_version=4))), 5, 3)
    assert client.get("/me").status_code == 401


def test_deleted_user_is_401():
    client = with_cookie(make_client(FakeDB()), 5, 0)
    assert client.get("/me").status_code == 401


def test_invalid_cookie_is_401():
    client = make_client(FakeDB(make_user()))
    client.cookies.set(AUTH_COOKIE, "forged.token.value")
    assert client.get("/me").status_code == 401


def test_consent_required_for_profile():
    db = FakeDB(make_user(id=1, privacy_agreed_at=None), make_user(id=2))
    client = make_client(db)
    with_cookie(client, 1, 0)
    assert client.get("/me").status_code == 200  # 로그인 자체는 됨
    assert client.get("/profile").status_code == 403
    with_cookie(client, 2, 0)
    assert client.get("/profile").status_code == 200


@pytest.mark.parametrize(
    ("email", "google_sub", "expected"),
    [
        ("admin@gmail.com", "g-1", 200),
        ("boss@gmail.com", "g-2", 200),  # 대소문자 무시
        ("admin@gmail.com", None, 403),  # 이메일 가입은 소유 확인이 없어 관리자 아님
        ("user@gmail.com", "g-3", 403),
    ],
)
def test_admin_requires_listed_google_account(email, google_sub, expected):
    client = make_client(FakeDB(make_user(email=email, google_sub=google_sub)))
    with_cookie(client, 1, 0)
    assert client.get("/admin").status_code == expected


def test_admin_requires_login():
    assert make_client(FakeDB()).get("/admin").status_code == 401


# --- 쿠키 ---------------------------------------------------------------------


def test_session_cookie_attributes():
    app = FastAPI()

    @app.post("/session")
    def session():
        from fastapi import Response

        response = Response()
        issue_session(response, make_user(id=9, token_version=1))
        return response

    header = TestClient(app).post("/session").headers["set-cookie"]
    lowered = header.lower()
    assert header.startswith(f"{AUTH_COOKIE}=")
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert f"max-age={7 * 24 * 3600}" in lowered
    token = header.split(";")[0].split("=", 1)[1]
    assert decode_access_token(token).user_id == 9


def test_clear_cookie():
    app = FastAPI()

    @app.post("/logout")
    def logout():
        from fastapi import Response

        response = Response()
        security.clear_auth_cookie(response)
        return response

    header = TestClient(app).post("/logout").headers["set-cookie"].lower()
    assert header.startswith(f"{AUTH_COOKIE}=")
    assert "max-age=0" in header


# --- 로그인 시도 제한 -------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ip_email_limit_is_5_per_minute():
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(5):
        limiter.hit("1.1.1.1", "a@example.com")
    with pytest.raises(Exception) as error:
        limiter.hit("1.1.1.1", "a@example.com")
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) >= 1
    limiter.hit("2.2.2.2", "a@example.com")  # 다른 IP는 아직 가능
    limiter.hit("1.1.1.1", "b@example.com")  # 다른 이메일도 가능


def test_limit_resets_after_one_minute():
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(5):
        limiter.hit("1.1.1.1", "a@example.com")
    clock.now += 60
    limiter.hit("1.1.1.1", "a@example.com")


def test_email_limit_is_10_regardless_of_ip():
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for i in range(10):
        limiter.hit(f"10.0.0.{i}", "A@Example.com ")
    with pytest.raises(Exception) as error:
        limiter.hit("10.0.0.99", "a@example.com")
    assert error.value.status_code == 429


def test_rotating_x_forwarded_for_is_blocked_on_11th_attempt():
    """X-Forwarded-For를 매번 바꿔 보내도 같은 이메일 11번째 시도에서 429 (완료 기준)."""
    client = make_client(FakeDB())
    codes = [
        client.post(
            "/login",
            params={"email": "victim@example.com"},
            headers={"X-Forwarded-For": f"203.0.113.{i}, 10.0.0.1"},
        ).status_code
        for i in range(11)
    ]
    assert codes == [200] * 10 + [429]


def test_same_ip_is_blocked_on_6th_attempt():
    client = make_client(FakeDB())
    codes = [
        client.post(
            "/login", params={"email": "a@example.com"}, headers={"X-Forwarded-For": "198.51.100.7"}
        ).status_code
        for _ in range(6)
    ]
    assert codes == [200] * 5 + [429]


def test_client_ip_uses_first_forwarded_value():
    def request(headers: dict[str, str], host: str | None = "127.0.0.1") -> Request:
        scope = {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 1234) if host else None,
        }
        return Request(scope)

    assert client_ip(request({"X-Forwarded-For": " 203.0.113.5 , 10.0.0.1"})) == "203.0.113.5"
    assert client_ip(request({})) == "127.0.0.1"
    assert client_ip(request({}, host=None)) == "unknown"


def test_secret_values_are_not_in_repr():
    claims = decode_access_token(create_access_token(1, 0))
    assert SECRET not in repr(claims)


# --- 수신 거부 토큰 (7.4) -----------------------------------------------------------


def test_unsubscribe_token_round_trip():
    assert security.read_unsubscribe_token(security.create_unsubscribe_token(7)) == 7


def test_unsubscribe_token_does_not_expire():
    import jwt

    payload = jwt.decode(security.create_unsubscribe_token(7), SECRET, algorithms=["HS256"])
    assert payload == {"sub": "7", "purpose": "unsubscribe"}  # exp 없음: 오래된 메일의 링크도 동작


def test_access_token_is_not_an_unsubscribe_token():
    with pytest.raises(InvalidTokenError):
        security.read_unsubscribe_token(create_access_token(7, 0))


def test_unsubscribe_token_is_not_an_access_token():
    with pytest.raises(InvalidTokenError):
        decode_access_token(security.create_unsubscribe_token(7))


def test_signup_token_is_not_an_unsubscribe_token():
    with pytest.raises(InvalidTokenError):
        security.read_unsubscribe_token(security.create_signup_token("a@example.com"))


@pytest.mark.parametrize("token", ["", "abc", "a.b.c"])
def test_garbage_unsubscribe_token_is_rejected(token):
    with pytest.raises(InvalidTokenError):
        security.read_unsubscribe_token(token)


def test_tampered_unsubscribe_token_is_rejected():
    token = security.create_unsubscribe_token(7)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidTokenError):
        security.read_unsubscribe_token(tampered)


def test_unsubscribe_token_with_non_numeric_subject_is_rejected():
    import jwt

    token = jwt.encode({"sub": "abc", "purpose": "unsubscribe"}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        security.read_unsubscribe_token(token)
