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
    "name": "길동",
    "gender": "여",
    "birth_year": 2004,
    "completed_semesters": 5,
    "enrollment_status": "재학",
    "aibc_member": False,
    "is_transfer": False,
    "is_foreign": False,
    "gpa": 3.8,
    "score_100": 90.5,  # 서버가 평점으로 계산하는 값과 같게 (3.8 × 10 + 52.5)
    "credits": 18,
    "gpa_total": 3.65,
    "score_100_total": 89.0,  # 3.65 × 10 + 52.5
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
        "enrollment_status",  # 화면에서 묻지 않음. 빠지면 재학 (2026-11-10)
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
        "enrollment_status": "재학",
        "income_bracket": None,
        "self_region": None,
        "parent_region": None,
        "special_flags": None,
        "full_tuition_waiver": None,
    }
    row = stored(factory)
    assert row.enc_income_bracket is None and row.enc_special_flags is None
    assert row.enrollment_status == "재학"


def test_put_updates_existing_profile(api, factory):
    api.put("/profile", json=PROFILE)

    changed = {
        **PROFILE,
        "gpa": 4.1,
        "score_100": 93.5,  # 평점이 바뀌면 100점 환산도 다시 계산됨
        "completed_semesters": 7,
        "income_bracket": None,
        "self_region": None,
    }
    assert api.put("/profile", json=changed).json() == changed
    row = stored(factory)
    assert (row.gpa, row.completed_semesters) == (4.1, 7)
    assert (row.grade, row.extra_semester, row.department) == (4, False, "소프트웨어학부")
    assert row.enc_income_bracket is None  # null로 보내면 지움
    assert row.enc_self_region is None
    assert row.enc_parent_region is not None


# --- 편입생·외국인 (T39) -----------------------------------------------------------


def test_put_profile_transfer_foreign_roundtrip(api, factory):
    body = {**PROFILE, "is_transfer": True, "is_foreign": True, "completed_semesters": 2}

    assert api.put("/profile", json=body).json() == body
    assert api.get("/profile").json() == body
    row = stored(factory)
    # 옛 칸은 판정용 이수학기(2 + 4 = 6) 기준
    assert (row.grade, row.extra_semester) == (4, False)


def test_put_profile_without_new_fields_defaults_false(api):
    old_screen = {k: v for k, v in PROFILE.items() if k not in {"is_transfer", "is_foreign"}}

    response = api.put("/profile", json=old_screen)

    assert response.status_code == 200
    assert (response.json()["is_transfer"], response.json()["is_foreign"]) == (False, False)


def test_put_profile_transfer_semesters_over_12_rejected(api):
    response = api.put("/profile", json={**PROFILE, "is_transfer": True, "completed_semesters": 13})

    assert response.status_code == 422
    assert "편입 후 마친 학기는 0~12" in response.text


def test_put_profile_non_transfer_16_ok(api):
    response = api.put("/profile", json={**PROFILE, "completed_semesters": 16})

    assert response.status_code == 200


def test_put_profile_rejects_string_bool(api):
    assert api.put("/profile", json={**PROFILE, "is_foreign": "yes"}).status_code == 422


def test_me_includes_is_transfer(api):
    assert api.get("/auth/me").json()["is_transfer"] is None

    api.put("/profile", json={**PROFILE, "is_transfer": True, "completed_semesters": 1})

    assert api.get("/auth/me").json()["is_transfer"] is True


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
    for plain in ("길동", "논산시", "보훈", "3.8"):
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
        {"completed_semesters": -1},
        {"completed_semesters": 17},
        {"completed_semesters": 2.5},
        {"gpa": -0.1},
        {"gpa": 4.6},
        {"credits": 31},
        {"gpa_total": 4.6},
        {"birth_year": 1899},
        {"gender": "기타"},
        {"enrollment_status": "제적"},
        {"department": "컴퓨터공학과"},
        {"grade": 0},
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


# 100점 환산은 서버가 평점으로 계산하므로 빠져도 됩니다 (#41)
@pytest.mark.parametrize(
    "missing",
    # 편입생·외국인은 옛 화면 대비로 빠지면 false (T39)
    [
        k
        for k in REQUIRED_ONLY
        if k not in ("score_100", "score_100_total", "is_transfer", "is_foreign")
    ],
)
def test_missing_required_field_is_422(api, missing):
    body = {k: v for k, v in PROFILE.items() if k != missing}
    assert api.put("/profile", json=body).status_code == 422


def errors(response) -> str:
    return " ".join(e["msg"] for e in response.json()["detail"])


# --- 입력 검증 규칙 (4.3, 프로필 입력 정의서) ---------------------------------------


@pytest.mark.parametrize(
    ("name", "saved"),
    [
        ("권모", "권모"),
        (" 권모 ", "권모"),  # 앞뒤 공백 제거 (1-3)
        ("kwonmo", "kwonmo"),
        ("권모Kim2", "권모Kim2"),
        ("가나다라마바사아자차", "가나다라마바사아자차"),  # 10자
        ("\u1100\u1161\u11a8\u1100\u1161", "각가"),  # 분해된 한글은 완성형으로 (1-6f)
    ],
)
def test_nickname_accepted(api, factory, name, saved):
    assert api.put("/profile", json={**PROFILE, "name": name}).status_code == 200
    assert stored(factory).name == saved


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "권",
        "가나다라마바사아자차카",
        "1234",
        "권 모",
        "권모!",
        "ㅋㅋ",
        "朴朴",
        "권모😀",
        "권모\n",
        "권모\t",
    ],
)
def test_nickname_rejected(api, factory, name):
    response = api.put("/profile", json={**PROFILE, "name": name})
    assert response.status_code == 422
    assert "닉네임" in errors(response)
    assert stored(factory) is None


def test_birth_year_range_uses_seoul_year(api, monkeypatch):
    from app.schemas.api import profile as schema

    monkeypatch.setattr(schema, "now_kst", lambda: kst(2026, 1, 1))
    assert api.put("/profile", json={**PROFILE, "birth_year": 1950}).status_code == 200
    assert api.put("/profile", json={**PROFILE, "birth_year": 2011}).status_code == 200
    for year in (1949, 2012):
        response = api.put("/profile", json={**PROFILE, "birth_year": year})
        assert response.status_code == 422
        assert "출생연도는 1950~2011" in errors(response)


@pytest.mark.parametrize("field", ["gpa", "gpa_total"])
def test_scores_allow_two_decimals_only(api, field):
    assert api.put("/profile", json={**PROFILE, field: 3.45}).status_code == 200
    response = api.put("/profile", json={**PROFILE, field: 3.455})
    assert response.status_code == 422
    assert "소수 둘째 자리" in errors(response)


def test_freshman_without_grades(api, factory):
    """이수학기 0이면 성적 5칸을 보내도 버리고 null로 저장합니다(4.1)."""
    body = api.put("/profile", json={**PROFILE, "completed_semesters": 0}).json()

    assert body["completed_semesters"] == 0
    for field in ("gpa", "score_100", "credits", "gpa_total", "score_100_total"):
        assert body[field] is None
    row = stored(factory)
    assert (row.gpa, row.grade, row.extra_semester) == (None, 1, False)


GRADES = ("gpa", "score_100", "credits", "gpa_total", "score_100_total")


def test_freshman_can_omit_grades(api):
    body = {k: v for k, v in REQUIRED_ONLY.items() if k not in GRADES}
    assert api.put("/profile", json={**body, "completed_semesters": 0}).status_code == 200


def test_transfer_first_semester_keeps_prior_grades(api):
    """편입 첫 학기(편입 후 0학기)는 전 학교 성적을 저장합니다(4.1)."""
    body = api.put(
        "/profile", json={**PROFILE, "completed_semesters": 0, "is_transfer": True}
    ).json()

    assert body["gpa"] == PROFILE["gpa"]
    assert body["gpa_total"] == PROFILE["gpa_total"]
    assert body["credits"] == PROFILE["credits"]
    assert body["score_100"] is not None


@pytest.mark.parametrize("field", ["gpa", "credits", "gpa_total"])
def test_transfer_first_semester_requires_prior_grades(api, field):
    body = {k: v for k, v in REQUIRED_ONLY.items() if k != field}
    response = api.put("/profile", json={**body, "completed_semesters": 0, "is_transfer": True})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("gpa", "직전학기 평점을 입력하세요"),
        ("credits", "직전학기 이수학점을 입력하세요"),
        ("gpa_total", "전체 평점평균을 입력하세요"),
    ],
)
def test_grades_required_after_first_semester(api, field, message):
    """평점·이수학점·전체 평점평균은 이수학기 1 이상이면 필수입니다(2026-11-10 결정)."""
    body = {k: v for k, v in REQUIRED_ONLY.items() if k != field}
    response = api.put("/profile", json={**body, "completed_semesters": 1})
    assert response.status_code == 422
    assert errors(response) == message


def test_score_100_computed_from_gpa(api, factory):
    """100점 환산은 평점 × 10 + 52.5로 서버가 계산합니다. 보낸 값은 무시합니다(2026-11-10)."""
    body = {k: v for k, v in REQUIRED_ONLY.items() if k not in ("score_100", "score_100_total")}
    saved = api.put("/profile", json={**body, "completed_semesters": 1}).json()
    assert (saved["score_100"], saved["score_100_total"]) == (90.5, 89.0)  # 평점 3.8, 3.65

    hacked = api.put("/profile", json={**PROFILE, "score_100": 100.1, "score_100_total": -1})
    assert hacked.status_code == 200
    assert (hacked.json()["score_100"], hacked.json()["score_100_total"]) == (90.5, 89.0)
    row = stored(factory)
    assert (row.score_100, row.score_100_total) == (90.5, 89.0)


@pytest.mark.parametrize("status", ["재학", "휴학"])
def test_enrollment_status_from_old_screen_kept(api, factory, status):
    """열려 있던 옛 화면이 보내는 재학상태는 전환 기간에 그대로 받습니다."""
    assert (
        api.put("/profile", json={**REQUIRED_ONLY, "enrollment_status": status}).status_code == 200
    )
    assert stored(factory).enrollment_status == status


@pytest.mark.parametrize(
    ("semesters", "grade", "extra"), [(1, 1, False), (2, 2, False), (8, 4, True)]
)
def test_legacy_columns_are_written_for_rollback(api, factory, semesters, grade, extra):
    api.put("/profile", json={**PROFILE, "completed_semesters": semesters})
    row = stored(factory)
    assert (row.grade, row.extra_semester, row.department) == (grade, extra, "소프트웨어학부")


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_aibc_member_is_strict(api, value):
    assert api.put("/profile", json={**PROFILE, "aibc_member": value}).status_code == 422


def test_aibc_member_unknown_is_null(api):
    assert api.put("/profile", json={**PROFILE, "aibc_member": None}).json()["aibc_member"] is None


@pytest.mark.parametrize("status", ["수료", "졸업유예"])
def test_removed_enrollment_status_is_rejected(api, status):
    assert api.put("/profile", json={**PROFILE, "enrollment_status": status}).status_code == 422


def test_duplicate_special_flags_are_merged(api):
    flags = api.put("/profile", json={**PROFILE, "special_flags": ["보훈", "보훈", "다자녀"]})
    assert flags.json()["special_flags"] == ["보훈", "다자녀"]


def test_empty_special_flags_without_consent_is_null(app_client, factory):
    """동의 없이 보낸 빈 목록은 '해당 없음'이 아니라 '모름'(17-4)."""
    login_as(app_client, make_user(factory, sensitive=False))
    response = app_client.put("/profile", json={**PROFILE, "special_flags": []})
    assert response.json()["special_flags"] is None
    assert stored(factory).enc_special_flags is None


def test_unknown_field_is_rejected(api):
    assert api.put("/profile", json={**PROFILE, "nickname": "권모"}).status_code == 422


# --- 전환 기간: 열려 있던 옛 화면 (배포 ①~③) ----------------------------------------


OLD_SCREEN = {
    **{k: v for k, v in PROFILE.items() if k != "completed_semesters"},
    "grade": 3,
    "extra_semester": False,
    "department": "기타",
    "full_tuition_waiver": "모름",
}


@pytest.mark.parametrize(
    ("grade", "extra", "semesters"), [(1, False, 1), (3, False, 5), (4, False, 7), (2, True, 8)]
)
def test_old_screen_grade_is_converted(api, factory, grade, extra, semesters):
    body = {**OLD_SCREEN, "grade": grade, "extra_semester": extra}
    response = api.put("/profile", json=body)

    assert response.status_code == 200
    assert response.json()["completed_semesters"] == semesters
    assert response.json()["full_tuition_waiver"] is None  # 모름 → null
    assert "grade" not in response.json() and "department" not in response.json()
    assert stored(factory).department == "소프트웨어학부"  # 학과는 받고 무시


def test_new_field_wins_over_old_grade(api):
    body = {**OLD_SCREEN, "completed_semesters": 2}
    assert api.put("/profile", json=body).json()["completed_semesters"] == 2


def test_missing_semesters_and_grade_is_rejected(api):
    body = {k: v for k, v in OLD_SCREEN.items() if k != "grade"}
    response = api.put("/profile", json=body)
    assert response.status_code == 422
    assert "이수학기" in errors(response)


def test_get_reads_rows_written_by_old_server(api, factory):
    """옛 서버가 쓴 행(이수학기 없음, 수료, 모름, 이름 규칙 밖)도 조회가 실패하지 않습니다(4.3)."""
    api.put("/profile", json=PROFILE)
    with factory() as session:
        row = session.get(UserProfile, 1)
        row.completed_semesters = None
        row.grade, row.extra_semester = 4, True
        row.enrollment_status = "수료"
        row.full_tuition_waiver = "모름"
        row.name = "홍 길동!"
        row.gpa = 3.555
        session.commit()

    body = api.get("/profile").json()

    assert body["completed_semesters"] == 8
    assert body["enrollment_status"] == "재학"
    assert body["full_tuition_waiver"] is None
    assert (body["name"], body["gpa"]) == ("홍 길동!", 3.555)
    assert api.get("/scholarships").status_code == 200


# --- 주소 목록 검사 (정의서 15-4·15-5·15-6·15-9, P6) ---------------------------------


@pytest.mark.parametrize(
    "region",
    [
        {"sido": "충청북도", "sigungu": "청주시"},
        {"sido": "충청북도", "sigungu": None},  # 시/도만 (15-2)
        {"sido": "충청북도", "sigungu": ""},  # 빈 시/군구는 시/도만으로
        {"sido": "세종특별자치시", "sigungu": None},  # 시/군/구 없음 (15-6)
        {"sido": "서울특별시", "sigungu": "중구"},  # 같은 이름 시/군/구 (15-9)
        {"sido": "부산광역시", "sigungu": "중구"},
    ],
)
def test_region_in_list_accepted(api, factory, region):
    response = api.put("/profile", json={**PROFILE, "self_region": region, "parent_region": region})
    assert response.status_code == 200
    expected = {**region, "sigungu": region["sigungu"] or None}
    assert response.json()["self_region"] == expected


@pytest.mark.parametrize(
    ("field", "region", "message"),
    [
        ("self_region", {"sido": "아무시도"}, "목록에 있는 시/도를 고르세요"),  # 15-4
        ("self_region", {"sido": "충북"}, "목록에 있는 시/도를 고르세요"),  # 줄임말은 화면에 없음
        ("self_region", {"sido": "충청북도", "sigungu": "강남구"}, "고른 시/도에 있는"),  # 15-5
        ("parent_region", {"sido": "세종특별자치시", "sigungu": "조치원읍"}, "고른 시/도에 있는"),
        ("parent_region", {"sido": "전남광주통합특별시"}, "목록에 있는 시/도를 고르세요"),  # 15-7
    ],
)
def test_region_out_of_list_rejected(api, factory, field, region, message):
    response = api.put("/profile", json={**PROFILE, field: region})
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", field]
    assert message in error["msg"]
    assert stored(factory) is None


def test_region_list_matches_matching_rule_names():
    """주소 목록의 시/도 이름은 판정 규칙의 정식 이름과 같아야 합니다(regions.ts 머리말)."""
    from app.matching.rules import SIDO_ALIASES
    from app.schemas.api.regions import REGIONS

    assert set(REGIONS) == set(SIDO_ALIASES.values())
    assert all(len(set(names)) == len(names) for names in REGIONS.values())


def test_saved_out_of_list_region_still_readable(api, factory):
    """목록 검사는 저장(PUT)에만 둡니다. 저장된 목록 밖 주소로 조회가 실패하지 않습니다(15-8)."""
    from app.core.crypto import encrypt_json

    assert api.put("/profile", json=PROFILE).status_code == 200
    with factory() as db:
        row = db.query(UserProfile).one()
        row.enc_self_region = encrypt_json(
            {"sido": "아무시도", "sigungu": None}, aad=field_aad(row.user_id, "self_region")
        )
        db.commit()
    response = api.get("/profile")
    assert response.status_code == 200
    assert response.json()["self_region"] == {"sido": "아무시도", "sigungu": None}


# --- 422 응답 모양 (정의서 18-2, P6) -------------------------------------------------


def test_422_has_korean_message_per_field_without_input(api):
    body = {**PROFILE, "name": "1234", "gender": "기타", "birth_year": "이천사"}
    del body["aibc_member"]
    response = api.put("/profile", json=body)
    assert response.status_code == 422
    detail = {tuple(e["loc"]): e for e in response.json()["detail"]}
    assert detail[("body", "name")]["msg"] == "닉네임은 숫자만으로 만들 수 없어요"  # 머리말 없음
    assert detail[("body", "gender")]["msg"] == "목록에 있는 값을 고르세요"
    assert detail[("body", "birth_year")]["msg"] == "정수로 입력하세요"
    assert detail[("body", "aibc_member")]["msg"] == "입력하세요"
    for error in detail.values():
        assert set(error) == {"loc", "msg", "type"}  # 보낸 값(input)을 돌려주지 않음
