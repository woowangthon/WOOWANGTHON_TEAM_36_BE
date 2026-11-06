"""프로필 API (T21, 계획서 4.1·4.2·7.2). 테스트 DB를 씁니다."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core import security
from app.core.crypto import CryptoError, decrypt_json, field_aad
from app.core.db import get_db
from app.core.security import AUTH_COOKIE, create_access_token
from app.main import create_app
from app.models import User, UserProfile
from app.parsers.dates import kst
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

PROFILE = {
    "name": "홍길동",
    "gender": "여",
    "birth_year": 2004,
    "gpa": 3.8,
    "credits": 18,
    "grade": 3,
    "extra_semester": False,
    "enrollment_status": "재학",
    "department": "소프트웨어학부",
    "aibc_member": False,
    "income_bracket": 3.0,
    "self_region": {"sido": "충청북도", "sigungu": "청주시"},
    "parent_region": {"sido": "충청남도", "sigungu": "논산시"},
    "special_flags": ["다자녀", "보훈"],
    "full_tuition_waiver": "아니오",
}
REQUIRED_ONLY = {
    k: v
    for k, v in PROFILE.items()
    if k
    not in {
        "income_bracket",
        "self_region",
        "parent_region",
        "special_flags",
        "full_tuition_waiver",
    }
}
AGREED = kst(2026, 11, 1, 9, 0)
TABLES = "user_scholarship_states, user_profiles, users"


@pytest.fixture
def factory(migrated_engine):  # noqa: F811
    def truncate() -> None:
        with migrated_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))

    truncate()
    yield sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    truncate()


@pytest.fixture
def app_client(factory, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-" + "x" * 48)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    security.login_limiter.reset()

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = override
    return TestClient(app, base_url="https://testserver")


def make_user(factory, *, privacy=True, sensitive=True, email="s@example.com") -> int:
    with factory() as session:
        user = User(
            email=email,
            token_version=0,
            privacy_agreed_at=AGREED if privacy else None,
            sensitive_agreed_at=AGREED if sensitive else None,
        )
        session.add(user)
        session.commit()
        return user.id


@pytest.fixture
def api(app_client, factory) -> TestClient:
    """개인정보·민감정보에 모두 동의한 사용자로 로그인한 클라이언트."""
    login_as(app_client, make_user(factory))
    return app_client


def login_as(client: TestClient, user_id: int) -> TestClient:
    client.cookies.set(AUTH_COOKIE, create_access_token(user_id, 0))
    return client


def stored(factory, user_id: int = 1) -> UserProfile:
    with factory() as session:
        return session.get(UserProfile, user_id)


# --- 조회·저장 --------------------------------------------------------------------


def test_get_before_input_is_404(api):
    assert api.get("/profile").status_code == 404


def test_put_then_get_returns_decrypted_values(api):
    response = api.put("/profile", json=PROFILE)

    assert response.status_code == 200
    assert response.json() == PROFILE
    assert api.get("/profile").json() == PROFILE
    assert api.get("/auth/me").json()["profile_completed"] is True


def test_required_only_profile(api, factory):
    body = api.put("/profile", json=REQUIRED_ONLY).json()

    assert body == {
        **REQUIRED_ONLY,
        "income_bracket": None,
        "self_region": None,
        "parent_region": None,
        "special_flags": None,
        "full_tuition_waiver": None,
    }
    row = stored(factory)
    assert row.enc_income_bracket is None and row.enc_special_flags is None


def test_put_updates_existing_profile(api, factory):
    api.put("/profile", json=PROFILE)

    changed = {**PROFILE, "gpa": 4.1, "grade": 4, "income_bracket": None, "self_region": None}
    assert api.put("/profile", json=changed).json() == changed
    row = stored(factory)
    assert (row.gpa, row.grade) == (4.1, 4)
    assert row.enc_income_bracket is None  # null로 보내면 지움
    assert row.enc_self_region is None
    assert row.enc_parent_region is not None


@pytest.mark.parametrize("bracket", [0.0, 0.5, 1.0, 10.0])
def test_income_bracket_values(api, bracket):
    assert api.put("/profile", json={**PROFILE, "income_bracket": bracket}).status_code == 200


# --- 암호화 저장 -------------------------------------------------------------------


def test_sensitive_fields_are_encrypted_in_db(api, factory, migrated_engine):  # noqa: F811
    api.put("/profile", json=PROFILE)

    with migrated_engine.connect() as connection:
        raw = connection.execute(
            text(
                "SELECT enc_income_bracket, enc_self_region, enc_parent_region, enc_special_flags "
                "FROM user_profiles WHERE user_id = 1"
            )
        ).one()
        dump = connection.execute(
            text("SELECT row_to_json(p)::text FROM user_profiles p WHERE user_id = 1")
        ).scalar_one()
    for value in raw:
        assert isinstance(value, bytes) and len(value) > 28
    blob = b"".join(raw)
    for plain in ("청주", "논산", "충청", "보훈", "다자녀"):
        assert plain.encode() not in blob
        assert plain not in dump  # 행 전체 어디에도 평문이 없음
    assert decrypt_json(raw[3], aad=field_aad(1, "special_flags")) == ["다자녀", "보훈"]


def test_ciphertext_is_bound_to_user(api, factory, app_client):
    """다른 사용자 행으로 암호문을 옮겨 붙이면 복호화되지 않습니다."""
    api.put("/profile", json=PROFILE)
    other = make_user(factory, email="o@example.com")
    login_as(app_client, other)
    app_client.put("/profile", json=REQUIRED_ONLY)
    with factory() as session:
        session.get(UserProfile, other).enc_income_bracket = session.get(
            UserProfile, 1
        ).enc_income_bracket
        session.commit()
    row = stored(factory, other)
    with pytest.raises(CryptoError):
        decrypt_json(row.enc_income_bracket, aad=field_aad(other, "income_bracket"))


def test_profile_values_are_not_logged(api, caplog):
    caplog.set_level("DEBUG")
    api.put("/profile", json=PROFILE)
    api.get("/profile")
    for plain in ("홍길동", "논산시", "보훈", "3.8"):
        assert plain not in caplog.text


# --- 민감정보 동의 ----------------------------------------------------------------


def test_special_flags_without_sensitive_consent_is_rejected(app_client, factory):
    login_as(app_client, make_user(factory, sensitive=False))

    response = app_client.put("/profile", json=PROFILE)

    assert response.status_code == 400
    assert "민감정보" in response.json()["detail"]
    assert stored(factory) is None  # 아무것도 저장하지 않음


@pytest.mark.parametrize("flags", [None, []])
def test_no_special_flags_without_consent_is_ok(app_client, factory, flags):
    login_as(app_client, make_user(factory, sensitive=False))
    response = app_client.put("/profile", json={**PROFILE, "special_flags": flags})
    assert response.status_code == 200
    assert stored(factory).enc_income_bracket is not None


def test_withdrawn_consent_removes_flags_and_blocks_new_ones(api, factory):
    api.put("/profile", json=PROFILE)

    assert api.delete("/auth/consent/sensitive").status_code == 204

    assert api.get("/profile").json()["special_flags"] is None
    assert api.put("/profile", json=PROFILE).status_code == 400
    assert api.get("/profile").json()["income_bracket"] == 3.0  # 다른 항목은 그대로


# --- 인증·동의 -----------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "put"])
def test_requires_login(app_client, method):
    response = app_client.request(method, "/profile", json=PROFILE)
    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "put"])
def test_requires_privacy_consent(app_client, factory, method):
    login_as(app_client, make_user(factory, privacy=False))
    response = app_client.request(method, "/profile", json=PROFILE)
    assert response.status_code == 403


# --- 검증 실패 (4.1) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"grade": 0},
        {"grade": 5},
        {"gpa": -0.1},
        {"gpa": 4.6},
        {"credits": 31},
        {"birth_year": 1899},
        {"gender": "기타"},
        {"enrollment_status": "제적"},
        {"department": "컴퓨터공학과"},
        {"income_bracket": 1.5},
        {"income_bracket": 11},
        {"special_flags": ["한부모"]},
        {"full_tuition_waiver": "몰라"},
        {"self_region": {"sido": ""}},
        {"self_region": {"sido": "충청북도", "detail": "상당로 1"}},  # 상세 주소 거부
        {"name": ""},
        {"unknown_field": 1},
    ],
)
def test_invalid_profile_is_422(api, factory, overrides):
    assert api.put("/profile", json={**PROFILE, **overrides}).status_code == 422
    assert stored(factory) is None


@pytest.mark.parametrize("missing", list(REQUIRED_ONLY))
def test_missing_required_field_is_422(api, missing):
    body = {k: v for k, v in PROFILE.items() if k != missing}
    assert api.put("/profile", json=body).status_code == 422
