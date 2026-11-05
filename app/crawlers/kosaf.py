"""한국장학재단 장학금 공지 크롤러 (계획서 2장 표 "한국장학재단" 열).

- 목록은 장학금 공지 분류(`ctgrId1=0000000002`)이고 한 페이지에 10건입니다.
  상단 고정 공지는 없습니다.
  선발 결과·회의록처럼 신청과 무관한 글도 섞여 있지만, 여기서는 거르지 않고 AI 추출의
  `notice_kind`(모집/결과/기준안내/기타)로 나눕니다(5.2).
- 글 ID는 `seqNo`입니다.
- 상세에서는 본문 텍스트(`td#VIEW_MCONTENT`)만 읽습니다. 첨부는 자바스크립트 `fileDown()`으로
  ZIP을 내려받는 방식이라 **받지 않고**, 파일 이름만 원문 페이지 링크와 함께 첨부 목록에 넣습니다
  (첨부만 바뀐 수정을 해시로 잡기 위함, 2장).
- 게시일은 날짜만 있어 00:00(KST)으로 봅니다.
- 게시물은 공공누리 제4유형(출처표시·상업적 이용금지·변경금지)입니다.
  화면에는 출처와 원문 링크를 표시합니다.
"""

import re
from urllib.parse import parse_qs, urlsplit

from app.crawlers.base import (
    Attachment,
    Crawler,
    NoticeSummary,
    ParseError,
    RawNotice,
    block_text,
    soup,
)
from app.parsers.dates import parse_posted_at

LIST_URL = "https://www.kosaf.go.kr/ko/notice.do"
CATEGORY = "0000000002"  # 장학금 공지

_SEQ_NO = re.compile(r"^\d+$")
_FILE_SIZE = re.compile(r"\s*\(\s*[\d.,]+\s*[KMG]?B\s*\)\s*$", re.IGNORECASE)
_POSTED = re.compile(r"작성일\s*:\s*([\d.]+)")


def detail_url(external_id: str) -> str:
    if not _SEQ_NO.match(external_id):
        raise ValueError(f"한국장학재단 글 ID(seqNo) 형식이 아닙니다: {external_id!r}")
    return f"{LIST_URL}?mode=view&ctgrId1={CATEGORY}&seqNo={external_id}"


class KosafCrawler(Crawler):
    source_code = "KOSAF"

    def fetch_list(self, page: int) -> list[NoticeSummary]:
        params: dict[str, str | int] = {"ctgrId1": CATEGORY}
        if page > 1:
            params["page"] = page
        return parse_list(self.client.get(LIST_URL, params=params).text)

    def fetch_detail(self, external_id: str) -> RawNotice:
        url = detail_url(external_id)
        return parse_detail(self.client.get(url).text, external_id=external_id, url=url)

    def fetch_attachment(self, attachment: Attachment, *, referer: str) -> bytes | None:
        return None  # fileDown() ZIP은 받지 않음(2장)


def parse_list(html: str) -> list[NoticeSummary]:
    page = soup(html)
    table = page.select_one("table.t-viewz")
    if table is None:
        raise ParseError("목록 표(table.t-viewz)를 찾지 못했습니다")

    summaries: list[NoticeSummary] = []
    seen: set[str] = set()
    for row in table.select("tr"):
        link = row.select_one("td.subject a[href*='seqNo=']")
        if link is None:
            continue
        external_id = _seq_no(str(link["href"]))
        if external_id is None or external_id in seen:
            continue
        seen.add(external_id)
        day = row.select_one("td.day")
        summaries.append(
            NoticeSummary(
                external_id=external_id,
                title=link.get_text(" ", strip=True),
                url=detail_url(external_id),
                posted_at=parse_posted_at(day.get_text(strip=True)) if day else None,
            )
        )
    return summaries


def parse_detail(html: str, *, external_id: str, url: str) -> RawNotice:
    page = soup(html)
    title = page.select_one("#VIEW_TITLE")
    info = page.select_one("td.infor")
    content = page.select_one("#VIEW_MCONTENT")
    if title is None or info is None or content is None:
        raise ParseError(f"상세 페이지 구조가 다릅니다(seqNo={external_id})")

    posted = _POSTED.search(info.get_text(" ", strip=True))
    posted_at = parse_posted_at(posted.group(1)) if posted else None
    if posted_at is None:
        raise ParseError(f"게시일을 읽지 못했습니다(seqNo={external_id})")

    # fileDown()은 받지 않으므로 첨부 URL은 원문 페이지 링크로 둡니다.
    attachments = tuple(
        Attachment(name=_FILE_SIZE.sub("", " ".join(a.get_text().split())), url=url)
        for a in page.select("#VIEW_FILE a[onclick*='fileDown']")
    )
    return RawNotice(
        external_id=external_id,
        title=title.get_text(" ", strip=True),
        posted_at=posted_at,
        url=url,
        body_text=block_text(content),
        attachments=attachments,
    )


def _seq_no(href: str) -> str | None:
    values = parse_qs(urlsplit(href).query).get("seqNo")
    return values[0] if values and _SEQ_NO.match(values[0]) else None
