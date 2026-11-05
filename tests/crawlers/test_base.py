"""크롤러 기반 테스트 (T10). 실제 사이트에 요청하지 않고 MockTransport와 가짜 시계를 씁니다."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.crawlers.base import (
    MIN_INTERVAL_SECONDS,
    USER_AGENT,
    Attachment,
    Crawler,
    NoticeSummary,
    PoliteClient,
    RawNotice,
)

SEOUL = ZoneInfo("Asia/Seoul")


class FakeClock:
    """sleep을 실제로 기다리지 않고 시간만 앞으로 보냅니다. 요청 시각을 기록합니다."""

    def __init__(self, request_duration: float = 0.0) -> None:
        self.now = 1000.0
        self.request_duration = request_duration  # 요청 1건이 걸리는 시간(가짜)
        self.sleeps: list[float] = []
        self.request_times: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(clock: FakeClock, statuses: list[int] | None = None, **kwargs) -> PoliteClient:
    """요청이 올 때마다 statuses 순서대로 응답하는 클라이언트. 다 쓰면 200."""
    queue = list(statuses or [])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        clock.request_times.append(clock.now)
        clock.now += clock.request_duration
        seen.append(request)
        return httpx.Response(queue.pop(0) if queue else 200, text="ok")

    client = PoliteClient(clock=clock, transport=httpx.MockTransport(handler), **kwargs)
    client.seen = seen  # type: ignore[attr-defined]
    return client


def gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:], strict=False)]


# --- 요청 간격 ---


def test_first_request_does_not_wait():
    clock = FakeClock()
    make_client(clock).get("https://example.test/a")
    assert clock.sleeps == []


def test_consecutive_requests_are_at_least_one_second_apart():
    clock = FakeClock(request_duration=0.3)
    client = make_client(clock)
    for path in ("a", "b", "c", "d"):
        client.get(f"https://example.test/{path}")
    # 응답을 받은 뒤 1초를 기다리므로 시작 간격은 1초 + 요청 시간
    assert all(g >= MIN_INTERVAL_SECONDS for g in gaps(clock.request_times))
    assert clock.sleeps == pytest.approx([1.0, 1.0, 1.0])


def test_no_extra_wait_when_enough_time_has_passed():
    clock = FakeClock()
    client = make_client(clock)
    client.get("https://example.test/a")
    clock.now += 5  # 파싱 등으로 이미 5초가 지남
    client.get("https://example.test/b")
    assert clock.sleeps == []


def test_waits_only_the_remaining_time():
    clock = FakeClock()
    client = make_client(clock)
    client.get("https://example.test/a")
    clock.now += 0.4
    client.get("https://example.test/b")
    assert clock.sleeps == pytest.approx([0.6])


def test_interval_below_one_second_is_rejected():
    with pytest.raises(ValueError):
        PoliteClient(min_interval=0.5, clock=FakeClock())


def test_interval_counts_from_failed_request():
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.request_times.append(clock.now)
        if len(clock.request_times) == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200)

    client = PoliteClient(clock=clock, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectTimeout):
        client.get("https://example.test/a")
    client.get("https://example.test/b")
    assert gaps(clock.request_times) == pytest.approx([1.0])


# --- 헤더·타임아웃 ---


def test_sends_user_agent_with_contact():
    clock = FakeClock()
    client = make_client(clock)
    client.get("https://example.test/a", params={"page": 2})
    request = client.seen[0]  # type: ignore[attr-defined]
    assert request.headers["User-Agent"] == USER_AGENT
    assert "contact:" in USER_AGENT
    assert request.url.params["page"] == "2"


def test_timeout_is_ten_seconds():
    client = make_client(FakeClock())
    assert client._http.timeout == httpx.Timeout(10.0)


# --- 5xx 재시도 ---


def test_5xx_is_retried_once_with_interval():
    clock = FakeClock()
    client = make_client(clock, statuses=[503, 200])
    response = client.get("https://example.test/a")
    assert response.status_code == 200
    assert len(clock.request_times) == 2
    assert gaps(clock.request_times) == pytest.approx([1.0])


def test_5xx_twice_raises():
    clock = FakeClock()
    client = make_client(clock, statuses=[500, 502, 200])
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.get("https://example.test/a")
    assert error.value.response.status_code == 502
    assert len(clock.request_times) == 2  # 재시도는 1회만


def test_4xx_is_not_retried():
    clock = FakeClock()
    client = make_client(clock, statuses=[404])
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://example.test/a")
    assert len(clock.request_times) == 1


# --- 데이터 타입·인터페이스 ---


def test_raw_notice_requires_timezone():
    with pytest.raises(ValueError):
        RawNotice(
            external_id="1",
            title="t",
            posted_at=datetime(2026, 11, 5, 9, 0),  # noqa: DTZ001 (일부러 시간대 없이)
            url="https://example.test/1",
            body_text="본문",
        )


def test_notice_summary_allows_missing_posted_at():
    summary = NoticeSummary(external_id="1", title="t", url="https://example.test/1")
    assert summary.posted_at is None
    assert summary.is_pinned is False


def test_crawler_interface():
    class DummyCrawler(Crawler):
        source_code = "SW_DEPT"

        def fetch_list(self, page: int) -> list[NoticeSummary]:
            self.client.get("https://example.test/list", params={"page": page})
            return [NoticeSummary(external_id="7", title="장학", url="https://example.test/7")]

        def fetch_detail(self, external_id: str) -> RawNotice:
            self.client.get(f"https://example.test/{external_id}")
            return RawNotice(
                external_id=external_id,
                title="장학",
                posted_at=datetime(2026, 11, 5, 9, 0, tzinfo=SEOUL),
                url=f"https://example.test/{external_id}",
                body_text="본문",
                attachments=(Attachment(name="a.pdf", url="https://example.test/a.pdf"),),
            )

    clock = FakeClock()
    with DummyCrawler(make_client(clock)) as crawler:
        summaries = crawler.fetch_list(1)
        notice = crawler.fetch_detail(summaries[0].external_id)
    assert notice.attachments[0].name == "a.pdf"
    assert gaps(clock.request_times) == pytest.approx([1.0])

    with pytest.raises(TypeError):
        Crawler()  # type: ignore[abstract]
