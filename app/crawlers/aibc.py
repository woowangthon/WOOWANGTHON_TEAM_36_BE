"""AI부트캠프사업단 공지 크롤러 (계획서 2장 표 "AI부트캠프사업단" 열).

- 자체 CMS 게시판이고 카테고리가 없습니다. 제목이 `[장학금]`으로 시작하는 글만 장학 글로 봅니다.
- 글 ID는 상세 URL 끝의 slug(`/menu/1108/<slug>`)입니다.
- 게시판 전체가 1페이지(약 10건)라서 2페이지 이후는 요청하지 않고 빈 목록을 돌려줍니다.
- `robots.txt`가 `/cms`를 금지하므로 `/cms` 경로는 요청하지 않습니다.
- 게시일은 날짜만 있어 00:00(KST)으로 봅니다.
"""

import re
from urllib.parse import urljoin, urlsplit

from app.crawlers.base import (
    Attachment,
    Crawler,
    NoticeSummary,
    ParseError,
    RawNotice,
    block_text,
    parse_posted_at,
    soup,
)

BASE_URL = "https://aibc.cbnu.ac.kr/"
LIST_URL = "https://aibc.cbnu.ac.kr/menu/1108"
SCHOLARSHIP_PREFIX = "[장학금]"
FORBIDDEN_PATH_PREFIX = "/cms"  # robots.txt Disallow

_SLUG = re.compile(r"^[A-Za-z0-9]+$")
_FILE_SIZE = re.compile(r"\s*\(\s*[\d.,]+\s*[KMG]?B\s*\)\s*$", re.IGNORECASE)


def detail_url(external_id: str) -> str:
    if not _SLUG.match(external_id):
        raise ValueError(f"사업단 글 ID 형식이 아닙니다: {external_id!r}")
    return f"{LIST_URL}/{external_id}"


def is_allowed(url: str) -> bool:
    return not urlsplit(url).path.startswith(FORBIDDEN_PATH_PREFIX)


class AibcCrawler(Crawler):
    source_code = "AIBC"

    def fetch_list(self, page: int) -> list[NoticeSummary]:
        if page > 1:
            return []  # 게시판 전체가 1페이지 (2장 표)
        return parse_list(self._get(LIST_URL).text)

    def fetch_detail(self, external_id: str) -> RawNotice:
        url = detail_url(external_id)
        return parse_detail(self._get(url).text, external_id=external_id, url=url)

    def _get(self, url: str):
        if not is_allowed(url):
            raise ValueError(f"robots.txt가 금지한 경로입니다: {url}")
        return self.client.get(url)


def parse_list(html: str) -> list[NoticeSummary]:
    """제목이 `[장학금]`으로 시작하는 글만 돌려줍니다. 같은 글이 두 번 나오면 한 번만 넣습니다."""
    page = soup(html)
    table = page.select_one("table[data-board='list']")
    if table is None:
        raise ParseError("목록 표(table[data-board=list])를 찾지 못했습니다")

    summaries: list[NoticeSummary] = []
    seen: set[str] = set()
    for row in table.select("tbody > tr"):
        link = row.select_one("td[data-label='제목'] a[href]")
        if link is None:
            continue
        title = link.get_text(" ", strip=True)
        if not title.startswith(SCHOLARSHIP_PREFIX):
            continue
        external_id = _slug(str(link["href"]))
        if external_id is None or external_id in seen:
            continue
        seen.add(external_id)

        date_cell = row.select_one("td[data-label='등록일']")
        summaries.append(
            NoticeSummary(
                external_id=external_id,
                title=title,
                url=detail_url(external_id),
                posted_at=parse_posted_at(date_cell.get_text(strip=True)) if date_cell else None,
                is_pinned="noti" in (row.get("class") or []),
            )
        )
    return summaries


def parse_detail(html: str, *, external_id: str, url: str) -> RawNotice:
    page = soup(html)
    view = page.select_one("[data-board='view']")
    title = view.select_one(".view_h > strong") if view else None
    content = view.select_one(".board_txt") if view else None
    if view is None or title is None or content is None:
        raise ParseError(f"상세 페이지 구조가 다릅니다(slug={external_id})")

    posted_at = None
    for item in view.select(".view_h li"):
        label = item.select_one("strong")
        if label is not None and label.get_text(strip=True) == "등록일":
            posted_at = parse_posted_at(item.get_text(" ", strip=True))
    if posted_at is None:
        raise ParseError(f"게시일을 읽지 못했습니다(slug={external_id})")

    attachments = []
    for item in view.select(".file li"):
        name = item.select_one("strong")
        link = item.select_one("a[href]")
        if name is None or link is None:
            continue
        attachments.append(
            Attachment(
                name=_FILE_SIZE.sub("", name.get_text(strip=True)),
                url=urljoin(BASE_URL, str(link["href"])),
            )
        )

    return RawNotice(
        external_id=external_id,
        title=title.get_text(" ", strip=True),
        posted_at=posted_at,
        url=url,
        body_text=block_text(content),
        attachments=tuple(attachments),
    )


def _slug(href: str) -> str | None:
    path = urlsplit(href).path.rstrip("/")
    prefix = urlsplit(LIST_URL).path + "/"
    if not path.startswith(prefix):
        return None
    slug = path.removeprefix(prefix)
    return slug if _SLUG.match(slug) else None
