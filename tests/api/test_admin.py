"""관리자 API (T27, 계획서 7.1·7.2·7.3·9.3). 테스트 DB와 가짜 크롤러·추출기를 씁니다."""

import ast
import inspect
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.api import admin as admin_api
from app.core import security
from app.core.security import AUTH_COOKIE
from app.models import CrawlRun, Notice, Scholarship
from app.parsers.dates import kst
from app.scheduler.jobs import run_crawl
from tests.api.test_auth import add_user, api, factory, signup  # noqa: F401
from tests.api.test_scholarships import add_notice, add_scholarship
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401
from tests.scheduler.test_jobs import NOW, FakeCrawler, FakeExtractor, FakeSite, result


@pytest.fixture(autouse=True)
def _clean_runs(migrated_engine):  # noqa: F811
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE crawl_runs RESTART IDENTITY CASCADE"))
    yield
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE crawl_runs RESTART IDENTITY CASCADE"))


def login_as(api, user_id: int) -> None:  # noqa: F811
    api.cookies.set(AUTH_COOKIE, security.create_access_token(user_id, 0))


@pytest.fixture
def admin(api, factory):  # noqa: F811
    """ADMIN_EMAILS에 있고 Google로 확인된 계정 (7.3)."""
    login_as(api, add_user(factory, email="admin@gmail.com", google_sub="g-admin"))
    return api


def add_run(factory, source: str, status: str, started_hour: int, error: str | None = None):  # noqa: F811
    with factory() as session:
        started = kst(2026, 11, 6, started_hour, 0)
        session.add(
            CrawlRun(
                source_code=source,
                status=status,
                started_at=started,
                finished_at=started + timedelta(minutes=1) if status != "RUNNING" else None,
                new_count=1,
                error=error,
            )
        )
        session.commit()


def set_status(factory, notice_id: int, extract_status: str) -> None:  # noqa: F811
    with factory() as session:
        session.get(Notice, notice_id).extract_status = extract_status
        session.commit()


# --- 권한 ---------------------------------------------------------------------------

ENDPOINTS = [("get", "/admin/crawl-runs"), ("post", "/admin/notices/1/reextract")]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_requires_login(api, method, path):  # noqa: F811
    assert api.request(method, path).status_code == 401


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_student_is_forbidden(api, factory, method, path):  # noqa: F811
    notice_id = add_notice(factory)
    signup(api)
    response = api.request(method, path.replace("/1/", f"/{notice_id}/"))
    assert response.status_code == 403
    assert response.json()["detail"] == "관리자만 접근할 수 있습니다"
    with factory() as session:
        assert session.get(Notice, notice_id).extract_status == "DONE"  # 바뀌지 않음


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_admin_email_without_google_is_forbidden(api, factory, method, path):  # noqa: F811
    """이메일 가입은 소유 확인을 하지 않으므로 ADMIN_EMAILS에 있어도 관리자가 아닙니다(7.3)."""
    login_as(api, add_user(factory, email="admin@gmail.com", password_hash="x"))
    assert api.request(method, path).status_code == 403


# --- 수집 기록·공지 목록 ------------------------------------------------------------


def test_crawl_runs_and_notices(admin, factory):  # noqa: F811
    add_run(factory, "SW_DEPT", "DONE", 9)
    add_run(factory, "KOSAF", "FAILED", 10, error="HTTP 503")
    old_failed = add_notice(factory, title="실패", posted_at=kst(2026, 10, 1))
    set_status(factory, old_failed, "FAILED")
    pending = add_notice(factory, title="대기", posted_at=kst(2026, 10, 2))
    set_status(factory, pending, "PENDING")
    recent = add_notice(factory, title="최근", posted_at=kst(2026, 11, 3))
    older = add_notice(factory, title="이전", posted_at=kst(2026, 11, 1), kind="결과")
    add_scholarship(factory, recent, "A")
    add_scholarship(factory, recent, "B")

    response = admin.get("/admin/crawl-runs")

    assert response.status_code == 200
    body = response.json()
    assert [(r["source_code"], r["status"]) for r in body["items"]] == [
        ("KOSAF", "FAILED"),
        ("SW_DEPT", "DONE"),
    ]
    assert body["items"][0]["error"] == "HTTP 503"
    assert body["items"][0]["started_at"].endswith("+09:00")
    # 실패·대기 공지가 먼저(오래된 것부터), 그다음 최근 공지
    assert [n["id"] for n in body["notices"]] == [old_failed, pending, recent, older]
    first, _, latest, result_notice = body["notices"]
    assert first["extract_status"] == "FAILED"
    assert latest["scholarship_count"] == 2
    assert result_notice["notice_kind"] == "결과"
    assert result_notice["scholarship_count"] == 0
    assert latest["posted_at"].endswith("+09:00")


def test_recent_notices_are_limited_but_attention_is_not(admin, factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(admin_api, "RECENT_NOTICE_LIMIT", 2)
    for day in range(1, 5):
        add_notice(factory, posted_at=kst(2026, 11, day))
    for day in range(1, 4):
        set_status(factory, add_notice(factory, posted_at=kst(2026, 9, day)), "FAILED")

    statuses = [n["extract_status"] for n in admin.get("/admin/crawl-runs").json()["notices"]]

    assert statuses == ["FAILED", "FAILED", "FAILED", "DONE", "DONE"]


# --- 재추출 -------------------------------------------------------------------------


def test_reextract_only_marks_pending(admin, factory, monkeypatch):  # noqa: F811
    """요청 안에서 추출하지 않습니다(9.3). 추출 함수가 불리면 실패하게 해 둡니다."""

    def forbidden(*args, **kwargs):
        raise AssertionError("재추출 API가 추출 함수를 불렀습니다")

    monkeypatch.setattr("app.extraction.extractor.extract", forbidden)
    monkeypatch.setattr("app.extraction.extract", forbidden)
    notice_id = add_notice(factory)

    response = admin.post(f"/admin/notices/{notice_id}/reextract")

    assert response.status_code == 202
    assert response.json() == {"notice_id": notice_id, "extract_status": "PENDING"}
    with factory() as session:
        notice = session.get(Notice, notice_id)
        assert notice.extract_status == "PENDING"
        assert notice.extracted_at is None  # 추출 시각도 그대로


def test_reextract_unknown_notice(admin):
    response = admin.post("/admin/notices/999/reextract")
    assert response.status_code == 404
    assert response.json()["detail"] == "공지를 찾을 수 없습니다"


def test_admin_module_does_not_import_extraction():
    """관리자 API는 추출 모듈을 import하지 않습니다(8.1, 9.3)."""
    tree = ast.parse(inspect.getsource(admin_api))
    modules = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in getattr(node, "names", [])
    ]
    assert not [m for m in modules if m and m.startswith("app.extraction")]


def test_reextract_then_crawl_keeps_scholarship_id(admin, factory):  # noqa: F811
    """재추출 요청 → 다음 수집이 PENDING 공지를 추출하고, 같은 이름의 장학금 id를 유지합니다."""
    site, extractor = FakeSite(), FakeExtractor()
    site.post("100", title="2026 장학생 모집")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", kst(2026, 11, 12, 18, 0))))

    def crawl(at):
        return run_crawl(
            sources=["SW_DEPT"],
            session_factory=factory,
            crawler_factory=lambda code: FakeCrawler(site),
            extract_fn=extractor,
            clock=lambda: at,
        )

    crawl(NOW)
    with factory() as session:
        notice = session.scalars(select(Notice)).one()
        before = session.scalars(select(Scholarship)).one()
    assert len(extractor.requests) == 1

    assert admin.post(f"/admin/notices/{notice.id}/reextract").status_code == 202
    assert len(extractor.requests) == 1  # 요청 안에서는 추출하지 않음

    # 공지 내용은 그대로지만 재추출 결과가 달라짐(예: 프롬프트 수정 후 마감 연장 반영)
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", kst(2026, 11, 20, 18, 0))))
    crawl(NOW + timedelta(hours=1))

    assert len(extractor.requests) == 2
    with factory() as session:
        after = session.scalars(select(Scholarship)).one()
        assert session.get(Notice, notice.id).extract_status == "DONE"
    assert after.id == before.id
    assert after.apply_end == kst(2026, 11, 20, 18, 0)
