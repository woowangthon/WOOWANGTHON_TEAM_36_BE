"""소프트웨어학부 장학 게시판 크롤러 (계획서 2장 표 "소프트웨어학부" 열).

- XE/Rhymix 게시판입니다. 목록은 장학 카테고리(`category=8409`)로 받지만, 다른 분류(학부 등)의
  **상단 고정 공지가 섞여 나오므로** 분류 열이 `장학`인 글만 고릅니다.
- 글 ID는 `document_srl`입니다.
- 상세에서 제목·게시일·본문(`.xe_content`)·첨부 목록(파일명·다운로드 URL)을 읽습니다.
  첨부 내려받기(같은 세션 쿠키 + Referer 필요)와 PDF 텍스트 추출은 수집 파이프라인(T17)에서 합니다.
- BeautifulSoup/lxml은 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from app.crawlers.base import Attachment, Crawler, NoticeSummary, RawNotice

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag

BASE_URL = "https://software.cbnu.ac.kr/"
LIST_URL = "https://software.cbnu.ac.kr/index.php"
LIST_PARAMS = {"mid": "sub0401", "category": "8409"}
SCHOLARSHIP_CATEGORY = "장학"

SEOUL = ZoneInfo("Asia/Seoul")
_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")


class ParseError(ValueError):
    """페이지 구조가 예상과 달라 필요한 값을 찾지 못했습니다(사이트 개편 등)."""


def detail_url(external_id: str) -> str:
    return f"{LIST_URL}?mid=sub0401&document_srl={external_id}"


class SwDeptCrawler(Crawler):
    source_code = "SW_DEPT"

    def fetch_list(self, page: int) -> list[NoticeSummary]:
        params: dict[str, str | int] = {**LIST_PARAMS}
        if page > 1:
            params["page"] = page
        response = self.client.get(LIST_URL, params=params)
        return parse_list(response.text)

    def fetch_detail(self, external_id: str) -> RawNotice:
        url = detail_url(external_id)
        response = self.client.get(url)
        return parse_detail(response.text, external_id=external_id, url=url)


def parse_list(html: str) -> list[NoticeSummary]:
    """목록 HTML에서 분류가 `장학`인 글만 돌려줍니다. 같은 글이 두 번 나오면 한 번만 넣습니다."""
    soup = _soup(html)
    table = soup.select_one("table.bd_lst")
    if table is None:
        raise ParseError("목록 표(table.bd_lst)를 찾지 못했습니다")

    summaries: list[NoticeSummary] = []
    seen: set[str] = set()
    for row in table.select("tbody > tr"):
        category = row.select_one("td.cate")
        link = row.select_one("td.title a[href*='document_srl=']")
        if category is None or link is None:
            continue
        if category.get_text(strip=True) != SCHOLARSHIP_CATEGORY:
            continue  # 다른 분류의 상단 고정 공지
        external_id = _document_srl(str(link["href"]))
        if external_id is None or external_id in seen:
            continue
        seen.add(external_id)

        time_cell = row.select_one("td.time")
        posted_at = None
        if time_cell is not None:
            # 일반 글은 시각이 title 속성("15:33")에 있고,
            # 고정 공지는 날짜만 있습니다(00:00으로 봄).
            clock = time_cell.get("title")
            text = time_cell.get_text(strip=True)
            posted_at = _parse_kst(f"{text} {clock}" if clock else text)

        summaries.append(
            NoticeSummary(
                external_id=external_id,
                title=link.get_text(" ", strip=True),
                url=detail_url(external_id),
                posted_at=posted_at,
                is_pinned="notice" in (row.get("class") or []),
            )
        )
    return summaries


def parse_detail(html: str, *, external_id: str, url: str) -> RawNotice:
    soup = _soup(html)
    title = soup.select_one(".top_area h1")
    date = soup.select_one(".top_area .date")
    content = soup.select_one(".xe_content")
    if title is None or date is None or content is None:
        raise ParseError(f"상세 페이지 구조가 다릅니다(document_srl={external_id})")

    posted_at = _parse_kst(date.get_text(strip=True))
    if posted_at is None:
        raise ParseError(f"게시일을 읽지 못했습니다(document_srl={external_id})")

    attachments = tuple(
        Attachment(name=a.get_text(strip=True), url=urljoin(BASE_URL, str(a["href"])))
        for a in soup.select(".rd_file a[href*='procFileDownload']")
    )
    return RawNotice(
        external_id=external_id,
        title=title.get_text(" ", strip=True),
        posted_at=posted_at,
        url=url,
        body_text=_block_text(content),
        attachments=attachments,
    )


def _soup(html: str) -> "BeautifulSoup":
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "lxml")


def _document_srl(href: str) -> str | None:
    values = parse_qs(urlsplit(href).query).get("document_srl")
    return values[0] if values else None


def _parse_kst(text: str) -> datetime | None:
    """`2026.10.22 15:33` 또는 `2026.10.22`를 한국 시간으로 읽습니다. 시각이 없으면 00:00."""
    match = _DATE.search(text)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=SEOUL)


_BLOCK_TAGS = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol")
_CELL_TAGS = ("td", "th")


def _block_text(element: "Tag") -> str:
    """문단·줄바꿈·표 행 단위로 줄을 나눈 본문 텍스트.

    공문을 한글 편집기에서 붙여 넣은 글은 `<span>`이 글자 단위로 잘게 나뉘어 있어서,
    `get_text("\\n")`처럼 태그마다 나누면 "2027 / 년도"처럼 끊깁니다. 그래서 인라인 태그는
    이어 붙이고, 블록 태그와 `<br>`에서만 줄을 나눕니다. 표 칸은 ` | `로 구분합니다.
    """
    for br in element.find_all("br"):
        br.replace_with("\n")
    for cell in element.find_all(_CELL_TAGS):
        cell.insert_after(" | ")
    for block in element.find_all(_BLOCK_TAGS):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (" ".join(line.split()).strip(" |") for line in element.get_text().splitlines())
    return "\n".join(line for line in lines if line)
