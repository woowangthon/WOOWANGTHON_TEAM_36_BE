"""소프트웨어학부 장학 게시판 크롤러 테스트 (T11). 저장 HTML fixture만 씁니다."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.crawlers.base import PoliteClient
from app.crawlers.sw_dept import (
    ParseError,
    SwDeptCrawler,
    detail_url,
    parse_detail,
    parse_list,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sw_dept"
SEOUL = ZoneInfo("Asia/Seoul")

# list_p1.html에 있는 다른 분류(학부)의 상단 고정 공지 일부
OTHER_CATEGORY_PINNED = {"1154870", "1154864", "1154841", "1154609", "17471"}


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail(external_id: str):
    return parse_detail(
        read(f"{external_id}.html"), external_id=external_id, url=detail_url(external_id)
    )


@pytest.fixture(scope="module")
def summaries():
    return parse_list(read("list_p1.html"))


# --- 목록 ---


def test_list_keeps_only_scholarship_category(summaries):
    ids = {s.external_id for s in summaries}
    assert len(summaries) == 20  # 장학 고정 공지 2 + 일반 글 18
    assert not ids & OTHER_CATEGORY_PINNED


def test_list_keeps_scholarship_pinned_notices(summaries):
    pinned = [s.external_id for s in summaries if s.is_pinned]
    assert pinned == ["1154905", "1154830"]


def test_list_has_no_duplicate_ids(summaries):
    ids = [s.external_id for s in summaries]
    assert len(ids) == len(set(ids))


def test_list_uses_document_srl_and_detail_url(summaries):
    first = summaries[2]
    assert first.external_id == "1154730"
    assert first.url == "https://software.cbnu.ac.kr/index.php?mid=sub0401&document_srl=1154730"
    assert first.title.startswith("2026년도 2학기 푸른등대 기부장학사업")
    assert first.title.endswith("( ~11.12 18:00)")


def test_list_posted_at(summaries):
    by_id = {s.external_id: s for s in summaries}
    # 일반 글: 날짜 + title 속성의 시각
    assert by_id["1154730"].posted_at == datetime(2026, 10, 22, 15, 33, tzinfo=SEOUL)
    # 고정 공지: 날짜만 있어 00:00
    assert by_id["1154905"].posted_at == datetime(2026, 11, 3, 0, 0, tzinfo=SEOUL)


def test_list_dedupes_notice_shown_twice():
    html = read("list_p1.html")
    # 같은 글이 고정 공지와 일반 글로 두 번 나오는 경우
    row_start = html.index('<tr class="notice">')
    row_end = html.index("</tr>", row_start) + len("</tr>")
    doubled = html[:row_end] + html[row_start:row_end] + html[row_end:]
    ids = [s.external_id for s in parse_list(doubled)]
    assert ids.count("1154905") == 1


def test_list_without_table_raises():
    with pytest.raises(ParseError):
        parse_list("<html><body>점검 중</body></html>")


# --- 상세 ---


def test_detail_fields():
    notice = detail("1154730")
    assert notice.external_id == "1154730"
    assert notice.title.startswith("2026년도 2학기 푸른등대 기부장학사업 신규 장학생(2차)")
    assert notice.posted_at == datetime(2026, 10, 22, 15, 33, tzinfo=SEOUL)
    assert notice.url == detail_url("1154730")
    assert "푸른등대" in notice.body_text


def test_detail_body_joins_inline_spans():
    # 한글 편집기에서 붙여 넣은 글은 글자 단위 span으로 나뉘어 있음
    body = detail("1154905").body_text
    assert "2027년도 (재)관정재단 장학생 선발 계획" in body
    assert "- 학부 1~3학기 총 평점평균이 4.0 이상인 자" in body.splitlines()


def test_detail_body_has_no_blank_lines():
    for external_id in ("1154905", "1154830", "1154016"):
        assert all(line.strip() for line in detail(external_id).body_text.splitlines())


def test_detail_pdf_attachment():
    attachments = detail("1154016").attachments
    assert len(attachments) == 1
    pdf = attachments[0]
    assert pdf.name == "2027-1학기_성적우수선발_활동점수_지급기준(소프트웨어학부).pdf"
    assert pdf.url.startswith(
        "https://software.cbnu.ac.kr/?module=file&act=procFileDownload&file_srl=1154132"
    )


def test_detail_keeps_all_attachment_types():
    # 해시에는 첨부 목록 전체가 들어가므로(2장) PDF가 아닌 첨부도 목록에 남깁니다.
    names = [a.name for a in detail("1154590").attachments]
    assert len(names) == 2
    assert all(name.endswith(".hwp") for name in names)


def test_detail_without_attachments():
    assert detail("1154172").attachments == ()


def test_detail_other_category_still_parses():
    # 목록에서 걸러지지만 상세 자체는 읽을 수 있어야 합니다.
    assert detail("1154870").title.startswith("영어분야 레벨반 편성")


def test_all_detail_fixtures_parse():
    for path in FIXTURES.glob("1*.html"):
        notice = detail(path.stem)
        assert notice.title
        assert notice.body_text
        assert notice.posted_at.tzinfo is not None


def test_detail_with_unexpected_structure_raises():
    with pytest.raises(ParseError):
        parse_detail("<html><body></body></html>", external_id="1", url=detail_url("1"))


# --- 요청 (MockTransport, 네트워크 없음) ---


class InstantClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_crawler_requests_list_and_detail():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        srl = request.url.params.get("document_srl")
        return httpx.Response(200, text=read(f"{srl}.html" if srl else "list_p1.html"))

    client = PoliteClient(clock=InstantClock(), transport=httpx.MockTransport(handler))
    with SwDeptCrawler(client) as crawler:
        assert len(crawler.fetch_list(1)) == 20
        crawler.fetch_list(2)
        notice = crawler.fetch_detail("1154730")

    assert notice.external_id == "1154730"
    page1, page2, detail_request = (r.url.params for r in requests)
    assert dict(page1) == {"mid": "sub0401", "category": "8409"}
    assert page2["page"] == "2" and page2["category"] == "8409"
    assert dict(detail_request) == {"mid": "sub0401", "document_srl": "1154730"}
