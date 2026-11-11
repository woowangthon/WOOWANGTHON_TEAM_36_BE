"""POST /internal/crawl (T17, 계획서 9.3). 수집 자체는 목으로 바꿔 확인합니다."""

import pytest

from app.notifications import jobs as notify_jobs
from app.parsers.dates import kst
from app.scheduler import jobs
from app.schemas.api.admin import CrawlRunOut

TOKEN = "test-cron-token"


@pytest.fixture
def calls(monkeypatch):
    called: list[dict] = []
    run = CrawlRunOut(
        id=1,
        source_code="SW_DEPT",
        status="DONE",
        started_at=kst(2026, 11, 5, 10, 0),
        finished_at=kst(2026, 11, 5, 10, 1),
        new_count=2,
        error=None,
    )

    def fake_run_crawl(**kwargs):
        called.append(kwargs)
        return jobs.CrawlOutcome(skipped=False, runs=[run])

    monkeypatch.setattr(jobs, "run_crawl", fake_run_crawl)
    monkeypatch.setenv("CRON_TOKEN", TOKEN)
    return called


@pytest.fixture(autouse=True)
def notified(monkeypatch):
    """알림 발송(7.4)은 대역으로 바꿔 호출만 기록합니다(실제 작업은 tests/notifications)."""
    seen: list[dict] = []

    def fake(**kwargs):
        seen.append(kwargs)
        return notify_jobs.NotifyOutcome(skipped=True)

    monkeypatch.setattr(notify_jobs, "run_daily_notifications", fake)
    return seen


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Token": "wrong"}, {"X-Cron-Token": ""}])
def test_wrong_token_is_forbidden(client, calls, headers):
    response = client.post("/internal/crawl", headers=headers)
    assert response.status_code == 403
    assert calls == []


def test_unset_token_is_forbidden(client, monkeypatch):
    monkeypatch.delenv("CRON_TOKEN", raising=False)
    response = client.post("/internal/crawl", headers={"X-Cron-Token": ""})
    assert response.status_code == 403


def test_valid_token_runs_crawl(client, calls):
    response = client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN})

    assert response.status_code == 200
    assert calls == [{}]
    body = response.json()
    assert body["skipped"] is False
    assert body["runs"][0]["source_code"] == "SW_DEPT"
    assert body["runs"][0]["new_count"] == 2


# --- 수집 뒤 알림 발송 (7.4, 9.3) ----------------------------------------------------


def test_crawl_runs_notifications_after_crawl(client, calls, notified):
    response = client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN})

    assert response.status_code == 200
    assert calls == [{}]
    assert notified == [{}]


def test_wrong_token_does_not_run_notifications(client, calls, notified):
    assert client.post("/internal/crawl", headers={"X-Cron-Token": "wrong"}).status_code == 403
    assert notified == []


def test_notification_error_does_not_fail_crawl(client, calls, monkeypatch, caplog):
    def boom(**kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(notify_jobs, "run_daily_notifications", boom)

    response = client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN})

    assert response.status_code == 200
    assert response.json()["runs"][0]["source_code"] == "SW_DEPT"
    assert "알림 발송 작업 실패" in caplog.text


def test_notifications_run_even_when_crawl_is_skipped(client, monkeypatch, notified):
    monkeypatch.setenv("CRON_TOKEN", TOKEN)
    monkeypatch.setattr(jobs, "run_crawl", lambda **kw: jobs.CrawlOutcome(skipped=True))

    response = client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN})

    assert response.json()["skipped"] is True
    assert notified == [{}]
