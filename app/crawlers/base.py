"""크롤러 기반: 요청 간격을 지키는 HTTP 클라이언트와 크롤러 인터페이스 (계획서 2장 수집 전략, 8.2).

- 사이트마다 클라이언트를 하나씩 두고, 같은 사이트에 보내는 요청 사이에 **1초 이상** 간격을 둡니다.
  간격은 앞 요청의 응답을 받은 시점부터 잽니다(응답이 느린 사이트에 몰아서 보내지 않기 위함).
- `User-Agent`에 서비스명과 연락처를 넣습니다. 타임아웃은 10초입니다.
- 5xx 응답은 간격을 지켜 **1회만** 다시 요청합니다.
  그래도 실패하면 `httpx.HTTPStatusError`를 던집니다.
- 크롤러는 `crawlers/` 안의 이 클라이언트로만 요청합니다. 테스트에서는 `httpx.MockTransport`와
  가짜 시계를 넣어 실제 사이트에 요청하지 않습니다.
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, ClassVar, Protocol, Self
from zoneinfo import ZoneInfo

import httpx

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag

USER_AGENT = "CBNU-ScholarshipMatcher/0.1 (WOOWANGTHON team36; contact: pgmo0329@naver.com)"
MIN_INTERVAL_SECONDS = 1.0  # 공통 규칙: 같은 사이트 요청 간격 1초 이상
TIMEOUT_SECONDS = 10.0
MAX_5XX_RETRIES = 1

logger = logging.getLogger(__name__)


class Clock(Protocol):
    """간격 계산용 시계. 테스트에서는 가짜 시계로 바꿉니다."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class PoliteClient:
    """같은 사이트에 대한 요청 간격을 보장하는 동기 httpx 클라이언트."""

    def __init__(
        self,
        *,
        min_interval: float = MIN_INTERVAL_SECONDS,
        timeout: float = TIMEOUT_SECONDS,
        clock: Clock | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if min_interval < MIN_INTERVAL_SECONDS:
            raise ValueError(f"요청 간격은 {MIN_INTERVAL_SECONDS}초 이상이어야 합니다")
        self._min_interval = min_interval
        self._clock = clock or SystemClock()
        self._last_finished: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def get(self, url: str, *, params: dict[str, str | int] | None = None) -> httpx.Response:
        """GET 요청. 5xx면 1회 재시도하고, 최종 응답이 4xx·5xx면 `HTTPStatusError`를 던집니다."""
        for attempt in range(MAX_5XX_RETRIES + 1):
            response = self._send(url, params)
            if response.status_code < 500 or attempt == MAX_5XX_RETRIES:
                break
            logger.warning("5xx 응답, 재시도", extra={"url": url, "status": response.status_code})
        response.raise_for_status()
        return response

    def _send(self, url: str, params: dict[str, str | int] | None) -> httpx.Response:
        self._wait_turn()
        try:
            return self._http.get(url, params=params)
        finally:
            # 실패(타임아웃 등)한 요청도 사이트에 닿았을 수 있으므로 간격 계산에 넣습니다.
            self._last_finished = self._clock.monotonic()

    def _wait_turn(self) -> None:
        if self._last_finished is None:
            return
        remaining = self._min_interval - (self._clock.monotonic() - self._last_finished)
        if remaining > 0:
            self._clock.sleep(remaining)

    def close(self) -> None:
        self._http.close()


@dataclass(frozen=True)
class Attachment:
    name: str
    url: str


@dataclass(frozen=True)
class NoticeSummary:
    """목록 페이지의 글 1건. 상세를 받을지 정하는 데만 씁니다."""

    external_id: str  # document_srl / slug / seqNo (2장 표)
    title: str
    url: str
    posted_at: datetime | None = None  # 목록에 게시일이 없으면 None
    is_pinned: bool = False  # 상단 고정 공지(매 페이지에 다시 나옴)

    def __post_init__(self) -> None:
        _require_aware(self.posted_at)


@dataclass(frozen=True)
class RawNotice:
    """상세 페이지에서 받은 공지 원문. 해시는 제목 + 본문 + 첨부 목록으로 만듭니다(2장)."""

    external_id: str
    title: str
    posted_at: datetime  # Asia/Seoul로 해석한 시각 (timezone 필수)
    url: str
    body_text: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_aware(self.posted_at)


def _require_aware(value: datetime | None) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError("posted_at에는 시간대가 있어야 합니다(Asia/Seoul)")


class Crawler(ABC):
    """사이트별 크롤러 인터페이스. 사이트마다 `PoliteClient`를 하나씩 씁니다."""

    source_code: ClassVar[str]  # SW_DEPT / AIBC / KOSAF (SOURCES.code)

    def __init__(self, client: PoliteClient | None = None) -> None:
        self.client = client or PoliteClient()

    @abstractmethod
    def fetch_list(self, page: int) -> list[NoticeSummary]:
        """목록 `page`(1부터)의 글을 돌려줍니다. 장학 글만 걸러서 돌려줍니다."""

    @abstractmethod
    def fetch_detail(self, external_id: str) -> RawNotice:
        """글 1건의 상세를 받아 돌려줍니다."""

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# --- 사이트 크롤러가 함께 쓰는 HTML 도우미 ---
# BeautifulSoup/lxml은 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).

SEOUL = ZoneInfo("Asia/Seoul")
_POSTED_AT = re.compile(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")
_BLOCK_TAGS = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol")
_CELL_TAGS = ("td", "th")


class ParseError(ValueError):
    """페이지 구조가 예상과 달라 필요한 값을 찾지 못했습니다(사이트 개편 등)."""


def soup(html: str) -> "BeautifulSoup":
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "lxml")


def parse_posted_at(text: str) -> datetime | None:
    """게시판의 게시일 `2026.10.22 15:33`, `2026-10-29`를 한국 시간으로 읽습니다.

    시각이 없으면 00:00입니다. 형식이 정해진 게시일만 읽고, 공지 본문·제목의 날짜 해석은
    `parsers/dates.py`(T14)가 맡습니다.
    """
    match = _POSTED_AT.search(text)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=SEOUL)


def block_text(element: "Tag") -> str:
    """문단·줄바꿈·표 행 단위로 줄을 나눈 본문 텍스트. `element`를 바꾸므로 한 번만 부릅니다.

    공문을 한글 편집기에서 붙여 넣은 글은 `<span>`이 글자 단위로 잘게 나뉘어 있어서,
    `get_text("\\n")`처럼 태그마다 나누면 "2027 / 년도"처럼 끊깁니다. 그래서 인라인 태그는
    이어 붙이고, 블록 태그와 `<br>`에서만 줄을 나눕니다.
    표는 한 행을 한 줄로 만들고 칸은 ` | `로 구분합니다(칸 안의 문단·줄바꿈은 공백으로 합침).
    """
    for row in element.find_all("tr"):
        # 소스 HTML의 칸 사이 줄바꿈이 행을 여러 줄로 나누지 않게 공백 문자열을 지웁니다.
        for child in list(row.children):
            if isinstance(child, str) and not child.strip():
                child.extract()
    for cell in element.find_all(_CELL_TAGS):
        for br in cell.find_all("br"):
            br.replace_with(" ")
        for block in cell.find_all(_BLOCK_TAGS):
            block.insert_before(" ")
            block.insert_after(" ")
        cell.string = " ".join(cell.get_text().split())
        cell.insert_after(" | ")
    for br in element.find_all("br"):
        br.replace_with("\n")
    for block in element.find_all(_BLOCK_TAGS):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (" ".join(line.split()).strip(" |") for line in element.get_text().splitlines())
    return "\n".join(line for line in lines if line)
