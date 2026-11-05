"""한국장학재단 장학금 공지 크롤러 테스트 (T13). 저장 HTML fixture만 씁니다."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.crawlers.base import ParseError, PoliteClient
from app.crawlers.kosaf import KosafCrawler, detail_url, parse_detail, parse_list

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kosaf"
SEOUL = ZoneInfo("Asia/Seoul")
RECRUIT = "21427"  # 모집 공지, ZIP 첨부 2개
RESULT = "21470"  # 결과 공지, 첨부 없음
MINUTES = "21475"  # 회의록(장학금 아님), PDF 첨부 1개


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail(seq_no: str):
    return parse_detail(read(f"{seq_no}.html"), external_id=seq_no, url=detail_url(seq_no))


@pytest.fixture(scope="module")
def summaries():
    return parse_list(read("list_p1.html"))


# --- 목록 ---


def test_list_has_ten_notices_without_filtering(summaries):
    # 결과·회의록도 거르지 않고 AI 추출의 notice_kind로 나눕니다.
    ids = [s.external_id for s in summaries]
    assert len(ids) == 10
    assert ids[0] == MINUTES and RESULT in ids
    assert len(set(ids)) == 10


def test_list_fields(summaries):
    result = next(s for s in summaries if s.external_id == RESULT)
    assert result.title.startswith("2026년 복권기금 꿈사다리 우수 장학사업(SOS장학금)")
    assert result.posted_at == datetime(2026, 11, 2, 0, 0, tzinfo=SEOUL)
    assert result.url == (
        "https://www.kosaf.go.kr/ko/notice.do?mode=view&ctgrId1=0000000002&seqNo=21470"
    )
    assert not result.is_pinned


def test_list_without_table_raises():
    with pytest.raises(ParseError):
        parse_list("<html><body>점검 중</body></html>")


# --- 상세 ---


def test_detail_fields():
    notice = detail(RECRUIT)
    assert notice.title == "2026년 2학기 푸른등대 기부장학금(2차) 신규장학생 선발 안내"
    assert notice.posted_at == datetime(2026, 10, 21, 0, 0, tzinfo=SEOUL)
    assert notice.url == detail_url(RECRUIT)


def test_detail_body_text_only():
    body = detail(RECRUIT).body_text
    lines = body.splitlines()
    assert lines[0] == "안녕하세요 한국장학재단입니다."
    assert "ㅇ 장학금 신청 및 서류제출: 2026. 10. 22.(목) 09:00 ~ 11. 12.(목) 18:00" in lines
    # 본문 밖의 공공누리 표시·이전글/다음글·첨부 이름은 본문에 들어가지 않음
    assert "공공누리" not in body
    assert "이전글" not in body
    assert ".zip" not in body


def test_detail_attachments_are_names_with_original_link():
    attachments = detail(RECRUIT).attachments
    assert [a.name for a in attachments] == [
        "붙임1. '26-2학기 푸른등대 기부장학금(2차) 사업계획서 및 제출서류 샘플 등.zip",
        "붙임2. 2026년 2학기 푸른등대 기부장학금(2차) 학생신청 메뉴얼(PC).zip",
    ]
    # fileDown()은 받지 않으므로 URL은 원문 페이지
    assert all(a.url == detail_url(RECRUIT) for a in attachments)


def test_detail_without_attachments():
    assert detail(RESULT).attachments == ()


def test_detail_non_scholarship_notice_parses():
    notice = detail(MINUTES)
    assert notice.title == "2026년 제1차 국가과학기술 장학사업 운영위원회 회의록"
    assert [a.name for a in notice.attachments] == [
        "2026년 제1차 국가과학기술 장학사업 운영위원회 회의록.pdf"
    ]


def test_detail_with_unexpected_structure_raises():
    with pytest.raises(ParseError):
        parse_detail("<html><body></body></html>", external_id=RECRUIT, url="x")


def test_detail_url_rejects_non_numeric_id():
    with pytest.raises(ValueError):
        detail_url("21427&mode=delete")


# --- 요청 (MockTransport, 네트워크 없음) ---


class InstantClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_crawler_requests():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        seq_no = request.url.params.get("seqNo")
        return httpx.Response(200, text=read(f"{seq_no}.html" if seq_no else "list_p1.html"))

    client = PoliteClient(clock=InstantClock(), transport=httpx.MockTransport(handler))
    with KosafCrawler(client) as crawler:
        assert len(crawler.fetch_list(1)) == 10
        crawler.fetch_list(3)
        notice = crawler.fetch_detail(RESULT)

    assert notice.external_id == RESULT
    page1, page3, detail_request = (dict(r.url.params) for r in requests)
    assert page1 == {"ctgrId1": "0000000002"}
    assert page3 == {"ctgrId1": "0000000002", "page": "3"}
    assert detail_request == {"mode": "view", "ctgrId1": "0000000002", "seqNo": RESULT}
    assert all(r.url.host == "www.kosaf.go.kr" for r in requests)
