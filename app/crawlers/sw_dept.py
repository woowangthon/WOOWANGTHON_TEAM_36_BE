"""소프트웨어학부 장학 게시판 크롤러 (계획서 2장 표 "소프트웨어학부" 열).

- XE/Rhymix 게시판입니다. 목록은 장학 카테고리(`category=8409`)로 받지만, 다른 분류(학부 등)의
  **상단 고정 공지가 섞여 나오므로** 분류 열이 `장학`인 글만 고릅니다.
- 글 ID는 `document_srl`입니다.
- 상세에서 제목·게시일·본문(`.xe_content`)·첨부 목록(파일명·다운로드 URL)을 읽습니다.
  첨부 내려받기(같은 세션 쿠키 + Referer 필요)와 PDF 텍스트 추출은 수집 파이프라인(T17)에서 합니다.
"""

from urllib.parse import parse_qs, urljoin, urlsplit

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

BASE_URL = "https://software.cbnu.ac.kr/"
LIST_URL = "https://software.cbnu.ac.kr/index.php"
LIST_PARAMS = {"mid": "sub0401", "category": "8409"}
SCHOLARSHIP_CATEGORY = "장학"


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
    page = soup(html)
    table = page.select_one("table.bd_lst")
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
            posted_at = parse_posted_at(f"{text} {clock}" if clock else text)

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
    page = soup(html)
    title = page.select_one(".top_area h1")
    date = page.select_one(".top_area .date")
    content = page.select_one(".xe_content")
    if title is None or date is None or content is None:
        raise ParseError(f"상세 페이지 구조가 다릅니다(document_srl={external_id})")

    posted_at = parse_posted_at(date.get_text(strip=True))
    if posted_at is None:
        raise ParseError(f"게시일을 읽지 못했습니다(document_srl={external_id})")

    attachments = tuple(
        Attachment(name=a.get_text(strip=True), url=urljoin(BASE_URL, str(a["href"])))
        for a in page.select(".rd_file a[href*='procFileDownload']")
    )
    return RawNotice(
        external_id=external_id,
        title=title.get_text(" ", strip=True),
        posted_at=posted_at,
        url=url,
        body_text=block_text(content),
        attachments=attachments,
    )


def _document_srl(href: str) -> str | None:
    values = parse_qs(urlsplit(href).query).get("document_srl")
    return values[0] if values else None
