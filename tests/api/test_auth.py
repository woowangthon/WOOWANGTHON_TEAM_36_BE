"""이메일 가입·로그인·동의·탈퇴 API (T19, 계획서 7.2·7.3·4.2). 테스트 DB를 씁니다."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.core import security
from app.core.crypto import encrypt_json, field_aad
from app.core.db import get_db
from app.core.security import AUTH_COOKIE, hash_password
from app.main import create_app
from app.models import Notice, Scholarship, User, UserProfile, UserScholarshipState
from app.parsers.dates import kst
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

PASSWORD = "password12!"  # 새 규칙: 8~16자, 영문·숫자·특수문자 (T37)
TABLES = "user_scholarship_states, user_profiles, users, scholarships, notices"


@pytest.fixture
def factory(migrated_engine):  # noqa: F811
    def truncate() -> None:
        with migrated_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))

    truncate()
    yield sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    truncate()


@pytest.fixture
def api(factory, monkeypatch) -> TestClient:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-" + "x" * 48)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@gmail.com")
    monkeypatch.setenv("MAIL_MOCK", "1")  # 메일을 보내지 않고 인증번호 000000 (7.3)
    reset_limiters()

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = override
    yield TestClient(app, base_url="https://testserver")  # Secure 쿠키를 보내려면 https
    reset_limiters()


def reset_limiters() -> None:
    for limiter in (
        security.login_limiter,
        security.signup_code_limiter,
        security.signup_verify_limiter,
    ):
        limiter.reset()


def signup_token(api: TestClient, email: str = "student@example.com") -> str:
    """인증번호 받기 → 확인까지 하고 `signup_token`을 돌려줍니다(MAIL_MOCK 번호 000000)."""
    code = api.post("/auth/signup/code", json={"email": email})
    assert code.status_code == 200, code.text
    verify = api.post(
        "/auth/signup/verify",
        json={"code_token": code.json()["code_token"], "code": security.MOCK_SIGNUP_CODE},
    )
    assert verify.status_code == 200, verify.text
    return verify.json()["signup_token"]


def signup(api: TestClient, email: str = "student@example.com", password: str = PASSWORD):
    """인증번호 3단계를 거쳐 가입합니다. 이미 가입된 이메일이면 1단계의 409 응답을 돌려줍니다."""
    code = api.post("/auth/signup/code", json={"email": email})
    if code.status_code != 200:
        return code
    verify = api.post(
        "/auth/signup/verify",
        json={"code_token": code.json()["code_token"], "code": security.MOCK_SIGNUP_CODE},
    )
    assert verify.status_code == 200, verify.text
    return api.post(
        "/auth/signup", json={"signup_token": verify.json()["signup_token"], "password": password}
    )


def login(api: TestClient, email: str = "student@example.com", password: str = PASSWORD, **kw):
    return api.post("/auth/login", json={"email": email, "password": password}, **kw)


def add_user(factory, **fields) -> int:
    with factory() as session:
        user = User(**{"email": "g@gmail.com", "token_version": 0, **fields})
        session.add(user)
        session.commit()
        return user.id


def get_user(factory, user_id: int) -> User | None:
    with factory() as session:
        return session.get(User, user_id)


def add_profile(factory, user_id: int) -> None:
    with factory() as session:
        session.add(
            UserProfile(
                user_id=user_id,
                name="홍길동",
                gender="여",
                birth_year=2004,
                gpa=3.8,
                credits=18,
                completed_semesters=5,
                grade=3,
                extra_semester=False,
                enrollment_status="재학",
                department="소프트웨어학부",
                aibc_member=False,
                enc_income_bracket=encrypt_json(3, aad=field_aad(user_id, "income_bracket")),
                enc_special_flags=encrypt_json(["보훈"], aad=field_aad(user_id, "special_flags")),
            )
        )
        session.commit()


def cookie_header(response) -> str:
    return response.headers.get("set-cookie", "").lower()


# --- signup -------------------------------------------------------------------


def test_signup_creates_user_and_sets_cookie(api, factory):
    response = signup(api, email="Student@Example.COM")

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": 1,
        "email": "student@example.com",  # 소문자로 저장
        "privacy_agreed": False,
        "sensitive_agreed": False,
        "profile_completed": False,
        "is_admin": False,
        "google_linked": False,
        "has_password": True,
        "nickname": None,  # 프로필 전
        "completed_semesters": None,
        "gpa": None,
        "email_notifications": True,  # 알림 메일은 기본 켬 (7.4)
    }
    header = cookie_header(response)
    assert header.startswith(f"{AUTH_COOKIE}=")
    assert "httponly" in header and "secure" in header and "samesite=lax" in header
    user = get_user(factory, 1)
    assert user.password_hash.startswith("$argon2")
    assert PASSWORD not in user.password_hash
    assert api.get("/auth/me").status_code == 200  # 발급된 쿠키로 바로 로그인 상태


def test_signup_duplicate_email_is_409(api):
    assert signup(api).status_code == 201
    reset_limiters()  # 같은 이메일 발송 제한(1분 1회)과 따로 확인
    assert signup(api, email="STUDENT@example.com").status_code == 409


def test_signup_with_google_account_email_is_409(api, factory):
    add_user(factory, email="g@gmail.com", google_sub="g-1")
    assert signup(api, email="g@gmail.com").status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"password": "short"},
        {},
        {"password": PASSWORD, "is_admin": True},
        {"password": PASSWORD, "email": "other@example.com"},  # 이메일 바꿔치기 불가
    ],
)
def test_signup_validation_is_422(api, body):
    token = signup_token(api, email="a@example.com")
    assert api.post("/auth/signup", json={"signup_token": token, **body}).status_code == 422


def test_signup_without_token_is_422(api):
    assert api.post("/auth/signup", json={"password": PASSWORD}).status_code == 422


# --- login --------------------------------------------------------------------


def test_login_success(api):
    signup(api)
    api.cookies.clear()

    response = login(api, email="STUDENT@example.com")

    assert response.status_code == 200
    assert response.json()["email"] == "student@example.com"
    assert cookie_header(response).startswith(f"{AUTH_COOKIE}=")
    assert api.get("/auth/me").status_code == 200


def test_login_wrong_password_is_401(api):
    signup(api)
    api.cookies.clear()
    response = login(api, password="wrong-password")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_login_unknown_email_is_401_with_same_message(api):
    signup(api)
    api.cookies.clear()
    unknown = login(api, email="nobody@example.com")
    wrong = login(api, password="wrong-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()  # 가입 여부를 드러내지 않음


def test_google_only_account_gets_guidance(api, factory):
    add_user(factory, email="g@gmail.com", google_sub="g-1", password_hash=None)
    response = login(api, email="g@gmail.com")
    assert response.status_code == 400
    assert "Google로 로그인" in response.json()["detail"]


def test_login_rate_limit_is_429(api):
    signup(api)
    api.cookies.clear()
    codes = [
        login(
            api, password="wrong-password", headers={"X-Forwarded-For": f"203.0.113.{i}"}
        ).status_code
        for i in range(11)
    ]
    assert codes == [401] * 10 + [429]
    # 맞는 비밀번호여도 제한 중에는 429
    assert login(api, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429


def test_login_validation_is_422(api):
    assert api.post("/auth/login", json={"email": "a@example.com"}).status_code == 422


# --- logout -------------------------------------------------------------------


def test_logout_clears_cookie(api):
    signup(api)
    response = api.post("/auth/logout")
    assert response.status_code == 204
    header = cookie_header(response)
    assert header.startswith(f"{AUTH_COOKIE}=") and "max-age=0" in header
    assert api.get("/auth/me").status_code == 401


def test_logout_without_login_is_204(api):
    assert api.post("/auth/logout").status_code == 204


# --- me -----------------------------------------------------------------------


def test_me_requires_login(api):
    assert api.get("/auth/me").status_code == 401


def test_me_reports_profile_and_admin(api, factory):
    user_id = add_user(
        factory,
        email="admin@gmail.com",
        google_sub="g-admin",
        privacy_agreed_at=kst(2026, 11, 1, 9, 0),
    )
    add_profile(factory, user_id)
    api.cookies.set(AUTH_COOKIE, security.create_access_token(user_id, 0))

    body = api.get("/auth/me").json()

    assert body["profile_completed"] is True
    assert body["is_admin"] is True
    assert body["google_linked"] is True
    assert body["has_password"] is False
    assert body["privacy_agreed"] is True
    # '내 정보' 요약 3칸. 민감 필드(소득분위 등)는 응답에 없음
    assert (body["nickname"], body["completed_semesters"], body["gpa"]) == ("홍길동", 5, 3.8)
    assert not {"income_bracket", "self_region", "parent_region", "special_flags"} & body.keys()


def test_me_rejects_old_token_version(api, factory):
    signup(api)
    with factory() as session:
        session.get(User, 1).token_version = 1  # 비밀번호 무효화 등으로 세션 끊김
        session.commit()
    assert api.get("/auth/me").status_code == 401


# --- consent ------------------------------------------------------------------


def test_consent_saves_both(api, factory):
    signup(api)

    response = api.post("/auth/consent", json={"privacy": True, "sensitive": True})

    assert response.status_code == 200
    assert response.json()["privacy_agreed"] is True
    assert response.json()["sensitive_agreed"] is True
    user = get_user(factory, 1)
    assert user.privacy_agreed_at is not None and user.sensitive_agreed_at is not None


def test_consent_without_sensitive(api, factory):
    signup(api)
    body = api.post("/auth/consent", json={"privacy": True, "sensitive": False}).json()
    assert (body["privacy_agreed"], body["sensitive_agreed"]) == (True, False)


def test_consent_requires_privacy(api, factory):
    signup(api)
    response = api.post("/auth/consent", json={"privacy": False, "sensitive": True})
    assert response.status_code == 400
    assert get_user(factory, 1).privacy_agreed_at is None


def test_consent_keeps_first_agreed_time(api, factory):
    signup(api)
    api.post("/auth/consent", json={"privacy": True, "sensitive": True})
    first = get_user(factory, 1).privacy_agreed_at
    api.post("/auth/consent", json={"privacy": True, "sensitive": True})
    assert get_user(factory, 1).privacy_agreed_at == first


def test_consent_sensitive_false_clears_special_flags(api, factory):
    signup(api)
    api.post("/auth/consent", json={"privacy": True, "sensitive": True})
    add_profile(factory, 1)

    api.post("/auth/consent", json={"privacy": True, "sensitive": False})

    with factory() as session:
        profile = session.get(UserProfile, 1)
        assert profile.enc_special_flags is None
        assert profile.enc_income_bracket is not None  # 다른 암호화 필드는 그대로


def test_consent_requires_login(api):
    response = api.post("/auth/consent", json={"privacy": True, "sensitive": False})
    assert response.status_code == 401


def test_consent_validation_is_422(api):
    signup(api)
    assert api.post("/auth/consent", json={"privacy": True}).status_code == 422


# --- 민감정보 동의 철회 ---------------------------------------------------------------


def test_withdraw_sensitive_consent_deletes_special_flags(api, factory):
    signup(api)
    api.post("/auth/consent", json={"privacy": True, "sensitive": True})
    add_profile(factory, 1)

    response = api.delete("/auth/consent/sensitive")

    assert response.status_code == 204
    user = get_user(factory, 1)
    assert user.sensitive_agreed_at is None
    assert user.privacy_agreed_at is not None
    with factory() as session:
        assert session.get(UserProfile, 1).enc_special_flags is None
    assert api.get("/auth/me").json()["sensitive_agreed"] is False


def test_withdraw_without_profile_is_204(api):
    signup(api)
    assert api.delete("/auth/consent/sensitive").status_code == 204


def test_withdraw_requires_login(api):
    assert api.delete("/auth/consent/sensitive").status_code == 401


# --- 탈퇴 ---------------------------------------------------------------------


def test_delete_me_removes_all_rows(api, factory):
    signup(api)
    add_profile(factory, 1)
    other = add_user(factory, email="other@example.com")
    add_profile(factory, other)
    with factory() as session:
        notice = Notice(
            source_code="SW_DEPT",
            external_id="1",
            title="장학생 모집",
            posted_at=kst(2026, 11, 1, 9, 0),
            url="https://example.test/1",
            body_text="본문",
            attachments=[],
            content_hash="0" * 64,
            extract_status="DONE",
        )
        notice.scholarships.append(Scholarship(name="A", status="OPEN", criteria={}))
        session.add(notice)
        session.flush()
        scholarship_id = notice.scholarships[0].id
        session.add_all(
            [
                UserScholarshipState(user_id=1, scholarship_id=scholarship_id, state="BOOKMARKED"),
                UserScholarshipState(user_id=other, scholarship_id=scholarship_id, state="APPLIED"),
            ]
        )
        session.commit()

    response = api.delete("/auth/me")

    assert response.status_code == 204
    assert "max-age=0" in cookie_header(response)
    with factory() as session:
        assert session.get(User, 1) is None
        assert session.get(UserProfile, 1) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserScholarshipState)
                .where(UserScholarshipState.user_id == 1)
            )
            == 0
        )
        # 다른 사용자와 장학금은 그대로
        assert session.get(User, other) is not None
        assert session.get(UserProfile, other) is not None
        assert session.scalar(select(func.count()).select_from(UserScholarshipState)) == 1
        assert session.get(Scholarship, scholarship_id) is not None


def test_deleted_user_cookie_no_longer_works(api):
    signup(api)
    token = api.cookies.get(AUTH_COOKIE)
    api.delete("/auth/me")
    api.cookies.set(AUTH_COOKIE, token)
    assert api.get("/auth/me").status_code == 401


def test_can_sign_up_again_after_delete(api):
    signup(api)
    api.delete("/auth/me")
    reset_limiters()  # 같은 이메일 발송 제한(1분 1회)과 따로 확인
    assert signup(api).status_code == 201


def test_delete_me_requires_login(api):
    assert api.delete("/auth/me").status_code == 401


def test_password_is_not_logged(api, caplog):
    caplog.set_level("DEBUG")
    signup(api, password="very-secret-pass")
    login(api, password="very-secret-pass")
    login(api, password="wrong-secret-pass")
    assert "very-secret-pass" not in caplog.text
    assert "wrong-secret-pass" not in caplog.text


def test_hash_helper_is_argon2():
    assert hash_password("x").startswith("$argon2")


def test_signup_422_does_not_echo_password(client):
    """기본 422는 보낸 값을 그대로 돌려줘 비밀번호가 응답에 실렸습니다(정의서 18-2)."""
    response = client.post("/auth/signup", json={"signup_token": 1, "password": "secret-pass-123"})
    assert response.status_code == 422
    assert "secret-pass-123" not in response.text
    assert [e["msg"] for e in response.json()["detail"]] == ["글자로 입력하세요"]


# --- 비밀번호 규칙 (T37) ------------------------------------------------------------

LENGTH = "비밀번호는 8~16자로 입력하세요"
LETTER = "영문을 1자 이상 넣어 주세요"
DIGIT = "숫자를 1자 이상 넣어 주세요"
SPECIAL = "특수문자를 1자 이상 넣어 주세요"
CHARSET = "영문·숫자·특수문자만 쓸 수 있어요"


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("abc12!", LENGTH),
        ("abcdefgh12345678!", LENGTH),
        ("abcdefgh", DIGIT),
        ("12345678!", LETTER),
        ("abcd1234", SPECIAL),
        ("abcd 12!", CHARSET),
        ("비밀번호12!a", CHARSET),
    ],
)
def test_signup_password_rules(api, password, message):
    response = api.post(
        "/auth/signup", json={"signup_token": signup_token(api), "password": password}
    )

    assert response.status_code == 422
    assert message in response.text


@pytest.mark.parametrize("password", ["abcd123!", "Abcdefgh1234567!"])
def test_signup_password_rules_accept(api, password):
    assert signup(api, password=password).status_code == 201


def test_login_still_accepts_old_style_password(api, factory):
    add_user(factory, email="old@example.com", password_hash=hash_password("password123"))

    assert login(api, email="old@example.com", password="password123").status_code == 200
