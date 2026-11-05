"""AI부트캠프사업단 공지 크롤러 테스트 (T12). 저장 HTML fixture만 씁니다."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.crawlers.aibc import (
    AibcCrawler,
    detail_url,
    is_allowed,
    parse_detail,
    parse_list,
)
from app.crawlers.base import ParseError, PoliteClient

FIXTURES = Path(__file__).parents[1] / "fixtures" / "aibc"
SEOUL = ZoneInfo("Asia/Seoul")
CRITERIA = "aq74eFJIirVMESsaQS7qtM"  # [장학금] 지급 기준 공지
CERTIFICATE = "pVAfFxOycuAihHhbGS0LAc"  # [장학금] 가점 사항(자격증)
RESULT = "kiF1EPTNkL5vj4HVH5idK5"  # 선발 결과 공지(장학금 접두어 없음)
ACTIVITY = "ypFXkiw6xSDhBxkPBz3aht"  # 경비 지원 안내, 첨부 4개


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def detail(slug: str):
    return parse_detail(read(f"{slug}.html"), external_id=slug, url=detail_url(slug))


# --- 목록 ---


def test_list_keeps_only_scholarship_prefix():
    summaries = parse_list(read("list.html"))
    assert [s.external_id for s in summaries] == [CERTIFICATE, CRITERIA]
    assert all(s.title.startswith("[장학금]") for s in summaries)


def test_list_excludes_non_scholarship_notices():
    ids = {s.external_id for s in parse_list(read("list.html"))}
    assert RESULT not in ids
    assert ACTIVITY not in ids


def test_list_fields():
    criteria = parse_list(read("list.html"))[1]
    assert criteria.url == f"https://aibc.cbnu.ac.kr/menu/1108/{CRITERIA}"
    assert criteria.title == "[장학금] AI부트캠프사업단 장학금 지급 기준 공지"
    assert criteria.posted_at == datetime(2026, 10, 14, 0, 0, tzinfo=SEOUL)
    assert criteria.is_pinned


def test_list_without_table_raises():
    with pytest.raises(ParseError):
        parse_list("<html><body>점검 중</body></html>")


# --- 상세 ---


def test_detail_fields():
    notice = detail(CRITERIA)
    assert notice.external_id == CRITERIA
    assert notice.title == "[장학금] AI부트캠프사업단 장학금 지급 기준 공지"
    assert notice.posted_at == datetime(2026, 10, 14, 0, 0, tzinfo=SEOUL)  # 날짜만 → 00:00
    assert notice.url == f"https://aibc.cbnu.ac.kr/menu/1108/{CRITERIA}"
    assert notice.attachments == ()


def test_detail_table_rows_are_single_lines():
    lines = detail(CRITERIA).body_text.splitlines()
    assert "평가항목 | 평가내용 | 세부평가 항목 | 평가기준" in lines
    assert any(line.startswith("기본평가 | 몰입형 교육과정 이수성적 |") for line in lines)


def test_detail_attachments_without_file_size():
    attachments = detail(ACTIVITY).attachments
    assert [a.name for a in attachments] == [
        "[붙임 1] 학생활동지원신청서.hwpx",
        "[붙임 2] 학생활동지원서 결과보고 및 증빙서류(경진대회).hwpx",
        "[붙임 3] 학생활동지원서 결과보고 및 증빙서류(학술대회).hwpx",
        "AI부트캠프사업단 학생활동 지원에 대한 지침_2026.10.12..pdf",
    ]
    assert attachments[3].url == "https://aibc.cbnu.ac.kr/download/E8iNoROlBkLLpVhbYLorUe"


def test_detail_body_uses_br_line_breaks():
    lines = detail(ACTIVITY).body_text.splitlines()
    assert "2. 지원대상 및 조건" in lines
    assert "5. 신청기간: ~ 12/31(상시접수)" in lines


def test_detail_masked_result_notice_parses():
    # 결과 공지(학번 마스킹)도 상세 자체는 읽을 수 있어야 합니다.
    notice = detail(RESULT)
    assert notice.posted_at == datetime(2026, 10, 21, 0, 0, tzinfo=SEOUL)
    assert "2024******" in notice.body_text  # 학번 뒤 6자리 마스킹(fixture README)


def test_detail_with_unexpected_structure_raises():
    with pytest.raises(ParseError):
        parse_detail("<html><body></body></html>", external_id=CRITERIA, url="x")


# --- 글 ID·robots.txt ---


def test_detail_url_rejects_non_slug():
    with pytest.raises(ValueError):
        detail_url("../../cms/admin")


def test_cms_path_is_not_allowed():
    assert not is_allowed("https://aibc.cbnu.ac.kr/cms/login")
    assert is_allowed(f"https://aibc.cbnu.ac.kr/menu/1108/{CRITERIA}")


# --- 요청 (MockTransport, 네트워크 없음) ---


class InstantClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_crawler_requests():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        slug = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=read("list.html" if slug == "1108" else f"{slug}.html"))

    client = PoliteClient(clock=InstantClock(), transport=httpx.MockTransport(handler))
    with AibcCrawler(client) as crawler:
        summaries = crawler.fetch_list(1)
        assert crawler.fetch_list(2) == []  # 게시판 전체가 1페이지라 요청하지 않음
        notice = crawler.fetch_detail(summaries[0].external_id)

    assert notice.external_id == CERTIFICATE
    assert requested == [
        "https://aibc.cbnu.ac.kr/menu/1108",
        f"https://aibc.cbnu.ac.kr/menu/1108/{CERTIFICATE}",
    ]
    assert not any("/cms" in url for url in requested)
