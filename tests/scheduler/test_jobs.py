"""수집 파이프라인 테스트 (T17).

사이트는 저장 HTML fixture나 메모리 속 가짜 크롤러로, 추출은 목으로 대신합니다.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.crawlers.aibc import AibcCrawler
from app.crawlers.base import Attachment, Crawler, NoticeSummary, PoliteClient, RawNotice
from app.extraction import (
    ExtractedCriteria,
    ExtractionError,
    ExtractionResult,
    ExtractRequest,
)
from app.extraction.extractor import _mock_result
from app.models import CrawlRun, Notice, Scholarship
from app.parsers.dates import kst
from app.scheduler import jobs
from app.scheduler.jobs import content_hash, run_crawl, start_runs

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = kst(2026, 11, 5, 10, 0)
POSTED = kst(2026, 11, 1, 9, 0)
PDF_BYTES = (FIXTURES / "sw_dept" / "pdf" / "1154905_0.pdf").read_bytes()


# --- 가짜 사이트·크롤러 -------------------------------------------------------


@dataclass
class FakeSite:
    notices: dict[str, RawNotice] = field(default_factory=dict)
    pages: dict[int, list[str]] = field(default_factory=dict)  # 목록 페이지 → 글 ID
    files: dict[str, bytes] = field(default_factory=dict)  # 첨부 URL → 내용
    fetched: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    fail: bool = False

    def post(
        self,
        external_id: str,
        title: str = "2026 장학생 모집",
        body: str = "본문",
        *,
        posted_at: datetime = POSTED,
        attachments: tuple[Attachment, ...] = (),
        page: int | None = 1,
    ) -> None:
        self.notices[external_id] = RawNotice(
            external_id=external_id,
            title=title,
            posted_at=posted_at,
            url=f"https://example.test/{external_id}",
            body_text=body,
            attachments=attachments,
        )
        if page is not None and external_id not in self.pages.setdefault(page, []):
            self.pages[page].append(external_id)


class FakeCrawler(Crawler):
    source_code = "SW_DEPT"

    def __init__(self, site: FakeSite) -> None:  # 네트워크 클라이언트를 만들지 않음
        self.site = site

    def fetch_list(self, page: int) -> list[NoticeSummary]:
        if self.site.fail:
            raise RuntimeError("목록 구조가 바뀜")
        return [
            NoticeSummary(
                external_id=i,
                title=self.site.notices[i].title,
                url=self.site.notices[i].url,
                posted_at=self.site.notices[i].posted_at,
            )
            for i in self.site.pages.get(page, [])
        ]

    def fetch_detail(self, external_id: str) -> RawNotice:
        self.site.fetched.append(external_id)
        return self.site.notices[external_id]

    def fetch_attachment(self, attachment: Attachment, *, referer: str) -> bytes | None:
        self.site.downloads.append(attachment.name)
        return self.site.files.get(attachment.url)

    def close(self) -> None:
        pass


def result(kind: str = "모집", *items: tuple[str, datetime | None]) -> ExtractionResult:
    """목 응답의 장학금 1개를 바탕으로 이름·마감만 바꾼 추출 결과."""
    base = _mock_result().criteria.scholarships[0].model_dump()
    scholarships = [
        {**base, "scholarship_name": name, "apply_start": None, "apply_end": end}
        for name, end in items
    ]
    criteria = ExtractedCriteria.model_validate({"notice_kind": kind, "scholarships": scholarships})
    return ExtractionResult(criteria=criteria, model_version="fake+extract_v1")


class FakeExtractor:
    """공지 제목별로 돌려줄 결과(또는 예외)를 정해 둡니다. 기본은 마감 11/12인 장학금 1개."""

    def __init__(self) -> None:
        self.answers: dict[str, list[ExtractionResult | Exception]] = {}
        self.requests: list[ExtractRequest] = []

    def set(self, title: str, *answers: ExtractionResult | Exception) -> None:
        self.answers[title] = list(answers)

    def __call__(self, request: ExtractRequest) -> ExtractionResult:
        self.requests.append(request)
        queue = self.answers.get(request.title)
        answer = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else None
        if answer is None:
            return result("모집", ("기본 장학금", kst(2026, 11, 12, 18, 0)))
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def crawl(session_factory, site, extractor):
    """SW_DEPT 하나만 가짜 크롤러로 수집합니다. at으로 실행 시각을 바꿀 수 있습니다."""

    def _crawl(at: datetime = NOW, **kwargs):
        return run_crawl(
            sources=["SW_DEPT"],
            session_factory=session_factory,
            crawler_factory=lambda code: FakeCrawler(site),
            extract_fn=extractor,
            clock=lambda: at,
            **kwargs,
        )

    return _crawl


def notices(session_factory) -> list[Notice]:
    with session_factory() as session:
        rows = list(session.scalars(select(Notice).order_by(Notice.id)))
        for row in rows:
            _ = row.scholarships, row.files
        return rows


def scholarships(session_factory) -> dict[str, Scholarship]:
    with session_factory() as session:
        return {s.name: s for s in session.scalars(select(Scholarship).order_by(Scholarship.id))}


# --- 저장 HTML fixture + AI_MOCK=1 -----------------------------------------------


class InstantClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_fixture_site_with_mock_ai(session_factory):
    """AIBC 저장 HTML로 목록·상세를 받고 실제 extract()를 AI_MOCK=1로 호출합니다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/download/"):
            return httpx.Response(200, content=PDF_BYTES)
        slug = request.url.path.rsplit("/", 1)[-1]
        name = "list.html" if slug == "1108" else f"{slug}.html"
        return httpx.Response(200, text=(FIXTURES / "aibc" / name).read_text(encoding="utf-8"))

    def factory(code: str) -> Crawler:
        client = PoliteClient(clock=InstantClock(), transport=httpx.MockTransport(handler))
        return AibcCrawler(client)

    outcome = run_crawl(
        sources=["AIBC"],
        session_factory=session_factory,
        crawler_factory=factory,
        clock=lambda: NOW,
    )

    assert not outcome.skipped
    [run] = outcome.runs
    assert (run.source_code, run.status, run.new_count) == ("AIBC", "DONE", 2)
    assert run.finished_at is not None
    rows = notices(session_factory)
    assert {r.external_id for r in rows} == {"pVAfFxOycuAihHhbGS0LAc", "aq74eFJIirVMESsaQS7qtM"}
    for row in rows:
        assert row.extract_status == "DONE"
        assert row.model_version == "mock+extract_v4"
        assert row.notice_kind == "모집"
        assert len(row.content_hash) == 64
        [scholarship] = row.scholarships
        assert scholarship.name == "두산연강재단 장학금"
        assert scholarship.status == "OPEN"  # 마감 11/12 18:00 > 실행 시각 11/5
        assert scholarship.deadline_source == "AI"
        assert scholarship.deadline_conflict is False


# --- 신규·중복·수정 ---------------------------------------------------------------


def test_new_notice_is_saved_and_extracted(crawl, site, extractor, session_factory):
    site.post("100", body="  평점 3.5 이상  ")

    outcome = crawl()

    assert outcome.runs[0].new_count == 1
    [notice] = notices(session_factory)
    assert notice.extract_status == "DONE"
    assert notice.last_checked_at == NOW
    assert notice.extracted_at == NOW
    assert [s.status for s in notice.scholarships] == ["OPEN"]
    [request] = extractor.requests
    assert request == ExtractRequest(
        source_code="SW_DEPT", title="2026 장학생 모집", posted_at=POSTED, text="평점 3.5 이상"
    )


def test_duplicate_notice_is_not_extracted_again(crawl, site, extractor, session_factory):
    pdf = Attachment(name="공고문.pdf", url="https://example.test/f/1")
    site.files[pdf.url] = PDF_BYTES
    site.post("100", attachments=(pdf,))
    crawl()
    later = NOW + timedelta(hours=1)

    outcome = crawl(at=later)

    assert outcome.runs[0].new_count == 0
    assert len(extractor.requests) == 1
    assert site.downloads == ["공고문.pdf"]  # 해시가 같으면 PDF를 다시 받지 않음
    [notice] = notices(session_factory)
    assert notice.last_checked_at == later
    assert notice.extracted_at == NOW


def test_whitespace_only_change_keeps_hash():
    a = RawNotice("1", "제목", POSTED, "u", "가  나\n\n다", ())
    b = RawNotice("1", "제목", POSTED, "u", " 가 나 다 ", ())
    assert content_hash(a) == content_hash(b)


def test_modified_notice_is_re_extracted(crawl, site, extractor, session_factory):
    site.post("100", body="평점 3.0 이상")
    crawl()
    [before] = notices(session_factory)

    site.post("100", body="평점 3.5 이상")
    outcome = crawl(at=NOW + timedelta(hours=1))

    assert outcome.runs[0].new_count == 0  # 수정은 새 공지로 세지 않음
    [after] = notices(session_factory)
    assert after.id == before.id
    assert after.content_hash != before.content_hash
    assert after.body_text == "평점 3.5 이상"
    assert len(extractor.requests) == 2


def test_attachment_only_change_is_re_extracted(crawl, site, extractor, session_factory):
    old = Attachment(name="공고문.pdf", url="https://example.test/f/1")
    new = Attachment(name="공고문(수정).pdf", url="https://example.test/f/2")
    site.files = {old.url: PDF_BYTES, new.url: PDF_BYTES}
    site.post("100", attachments=(old,))
    crawl()

    site.post("100", attachments=(new,))
    crawl(at=NOW + timedelta(hours=1))

    assert len(extractor.requests) == 2
    assert site.downloads == ["공고문.pdf", "공고문(수정).pdf"]
    [notice] = notices(session_factory)
    assert notice.attachments == [{"name": new.name, "url": new.url}]
    assert "[첨부 PDF: 공고문(수정).pdf]" in extractor.requests[-1].text


def test_attachments_are_read_and_saved_per_file(crawl, site, extractor, session_factory):
    pdf = Attachment(name="공고문.PDF", url="https://example.test/f/1")
    hwp = Attachment(name="신청서.hwp", url="https://example.test/f/2")
    site.files[pdf.url] = PDF_BYTES
    site.post("100", body="본문입니다", attachments=(pdf, hwp))

    crawl()

    assert site.downloads == ["공고문.PDF", "신청서.hwp"]
    text = extractor.requests[0].text
    assert text.startswith("본문입니다\n\n[첨부 PDF: 공고문.PDF]\n")
    assert "신청서.hwp" not in text  # 받지 못한 첨부는 입력에 넣지 않음
    [notice] = notices(session_factory)
    assert notice.body_text == "본문입니다"  # 첨부 텍스트는 첨부 표에만 저장
    assert [(f.name, f.file_kind, f.status) for f in notice.files] == [
        ("공고문.PDF", "PDF", "OK"),
        ("신청서.hwp", "OTHER", "FAILED"),
    ]


def test_result_notice_attachments_are_not_downloaded(crawl, site, extractor, session_factory):
    roster = Attachment(name="선발자 명단.pdf", url="https://example.test/f/1")
    site.files[roster.url] = PDF_BYTES
    site.post("100", title="2026 SOS장학금 최종 선발결과 안내", attachments=(roster,))

    crawl()

    assert site.downloads == []
    [notice] = notices(session_factory)
    assert [(f.name, f.status) for f in notice.files] == [("선발자 명단.pdf", "SKIPPED")]


def test_attachment_count_is_capped(crawl, site, extractor, session_factory, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_ATTACHMENTS", 1)
    files = tuple(Attachment(name=f"{i}.pdf", url=f"https://example.test/f/{i}") for i in (1, 2))
    site.files = {f.url: PDF_BYTES for f in files}
    site.post("100", attachments=files)

    crawl()

    assert site.downloads == ["1.pdf"]
    [notice] = notices(session_factory)
    assert [f.status for f in notice.files] == ["OK", "SKIPPED"]


def test_re_crawl_replaces_attachment_rows(crawl, site, extractor, session_factory):
    first = Attachment(name="공고문.pdf", url="https://example.test/f/1")
    site.files[first.url] = PDF_BYTES
    site.post("100", attachments=(first,))
    crawl()

    site.post("100", body="수정된 본문", attachments=())
    crawl(at=NOW + timedelta(hours=1))

    [notice] = notices(session_factory)
    assert notice.files == []


def test_form_files_are_saved_but_not_sent_to_extraction(crawl, site, extractor, session_factory):
    notice_pdf = Attachment(name="선발 공고문.pdf", url="https://example.test/f/1")
    form = Attachment(name="[붙임2] 장학금 신청서 서식.pdf", url="https://example.test/f/2")
    site.files = {notice_pdf.url: PDF_BYTES, form.url: PDF_BYTES}
    site.post("100", attachments=(notice_pdf, form))

    crawl()

    text = extractor.requests[0].text
    assert "[첨부 PDF: 선발 공고문.pdf]" in text
    assert "신청서 서식" not in text
    [notice] = notices(session_factory)
    assert [f.status for f in notice.files] == ["OK", "OK"]  # 저장은 함(양식도 읽은 첨부)


@pytest.mark.parametrize(
    ("name", "is_form"),
    [
        ("2026 천사장학생 신청서 서식.hwp", True),
        ("붙임1.zip / (일상회복) 프로그램 확인서.hwp", True),
        ("[붙임 1] 학생활동지원신청서.hwpx", True),
        ("개인정보 수집·이용 동의서.pdf", True),
        ("학생신청 메뉴얼(PC).pdf", True),
        ("붙임1. 사업계획서 및 제출서류 샘플 등.zip / 사업계획서 및 업무처리기준.pdf", False),
        ("2026학년도 2학기 천사장학생 선발 계획.hwp", False),
        ("장학생 선발 공고문.pdf", False),
    ],
)
def test_is_form_file(name, is_form):
    assert jobs.is_form_file(name) is is_form


def test_notice_saved_before_attachment_table_uses_body_text(session_factory, extractor):
    """첨부 표 이전에 수집한 공지는 body_text에 PDF 텍스트가 붙어 있고 첨부 행이 없습니다."""
    with session_factory() as session:
        notice = Notice(
            source_code="SW_DEPT",
            external_id="old",
            title="예전 공지",
            posted_at=POSTED,
            url="https://example.test/old",
            body_text="본문\n\n[첨부 PDF: 예전.pdf]\n평점 3.0",
            content_hash="x",
        )
        session.add(notice)
        session.commit()
        jobs.extract_notices(session, [notice.id], extractor, NOW)

    assert extractor.requests[-1].text == "본문\n\n[첨부 PDF: 예전.pdf]\n평점 3.0"


# --- 마감일 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ai_end", "expected_end", "source", "conflict"),
    [
        (kst(2026, 11, 12, 18, 0), kst(2026, 11, 20, 23, 59), "REGEX", True),  # 둘 다 있고 다름
        (kst(2026, 11, 20, 23, 59), kst(2026, 11, 20, 23, 59), "REGEX", False),  # 같음
        (None, kst(2026, 11, 20, 23, 59), "REGEX", False),  # 제목에만 있음
    ],
)
def test_title_deadline_is_compared(
    crawl, site, extractor, session_factory, ai_end, expected_end, source, conflict
):
    title = "2026 장학생 모집 (~11.20.)"
    site.post("100", title=title)
    extractor.set(title, result("모집", ("A 장학금", ai_end)))

    crawl()

    a = scholarships(session_factory)["A 장학금"]
    assert (a.apply_end, a.deadline_source, a.deadline_conflict) == (expected_end, source, conflict)
    assert a.status == "OPEN"


def test_title_without_deadline_uses_ai_value(crawl, site, extractor, session_factory):
    site.post("100", title="2026 장학생 모집")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", kst(2026, 11, 12, 18, 0))))

    crawl()

    a = scholarships(session_factory)["A 장학금"]
    assert (a.apply_end, a.deadline_source, a.deadline_conflict) == (
        kst(2026, 11, 12, 18, 0),
        "AI",
        False,
    )


def test_multiple_scholarships_are_not_compared_with_title(crawl, site, extractor, session_factory):
    title = "2026 장학생 모집 (~11.20.)"
    site.post("100", title=title)
    extractor.set(
        title,
        result("모집", ("A 장학금", kst(2026, 11, 12, 18, 0)), ("B 장학금", None)),
    )

    crawl()

    rows = scholarships(session_factory)
    assert (rows["A 장학금"].apply_end, rows["A 장학금"].deadline_source) == (
        kst(2026, 11, 12, 18, 0),
        "AI",
    )
    assert rows["B 장학금"].deadline_source is None
    assert rows["B 장학금"].status == "ALWAYS"  # 마감 없는 모집, 게시 60일 이내
    assert not any(r.deadline_conflict for r in rows.values())


def test_other_sources_are_not_compared_with_title(session_factory, extractor):
    site = FakeSite()
    title = "장학생 모집 (~11.20.)"
    site.post("K1", title=title)
    extractor.set(title, result("모집", ("A 장학금", kst(2026, 11, 12, 18, 0))))

    run_crawl(
        sources=["KOSAF"],
        session_factory=session_factory,
        crawler_factory=lambda code: FakeCrawler(site),
        extract_fn=extractor,
        clock=lambda: NOW,
    )

    a = scholarships(session_factory)["A 장학금"]
    assert (a.apply_end, a.deadline_source) == (kst(2026, 11, 12, 18, 0), "AI")


def test_past_deadline_is_closed(crawl, site, extractor, session_factory):
    site.post("100")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", kst(2026, 11, 12, 18, 0))))
    crawl()
    assert scholarships(session_factory)["A 장학금"].status == "OPEN"

    crawl(at=kst(2026, 11, 12, 18, 0))  # 마감 시각이 되면 일괄 갱신에서 CLOSED

    assert scholarships(session_factory)["A 장학금"].status == "CLOSED"
    assert len(extractor.requests) == 1


# --- 재추출 ---------------------------------------------------------------------


def test_re_extraction_keeps_scholarship_id(crawl, site, extractor, session_factory):
    end = kst(2026, 11, 12, 18, 0)
    site.post("100", body="v1")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", end), ("B 장학금", end)))
    crawl()
    before = scholarships(session_factory)

    site.post("100", body="v2")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", end), ("C 장학금", end)))
    crawl(at=NOW + timedelta(hours=1))

    after = scholarships(session_factory)
    assert after["A 장학금"].id == before["A 장학금"].id
    assert after["B 장학금"].id == before["B 장학금"].id
    assert after["B 장학금"].status == "CLOSED"  # 재추출 결과에서 사라짐
    assert after["C 장학금"].id not in {s.id for s in before.values()}
    assert after["C 장학금"].status == "OPEN"


def test_deadline_extension_reopens_and_vanished_stays_closed(
    crawl, site, extractor, session_factory
):
    site.post("100", body="v1")
    extractor.set(
        "2026 장학생 모집",
        result("모집", ("A 장학금", kst(2026, 11, 4, 18, 0)), ("B 장학금", kst(2026, 11, 30))),
    )
    crawl()
    first = scholarships(session_factory)
    assert first["A 장학금"].status == "CLOSED"  # 이미 마감
    assert first["B 장학금"].status == "OPEN"

    # 마감 연장 공지: A는 11/20로 연장, B는 결과에서 사라짐(마감은 아직 남았지만 CLOSED)
    site.post("100", body="v2 마감 연장")
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", kst(2026, 11, 20, 18, 0))))
    crawl(at=NOW + timedelta(hours=1))

    after = scholarships(session_factory)
    assert after["A 장학금"].id == first["A 장학금"].id
    assert after["A 장학금"].status == "OPEN"
    assert after["B 장학금"].status == "CLOSED"

    crawl(at=NOW + timedelta(days=1))  # 일괄 갱신이 CLOSED를 되살리지 않음
    assert scholarships(session_factory)["B 장학금"].status == "CLOSED"


def test_result_notice_has_no_scholarships(crawl, site, extractor, session_factory):
    site.post("100", title="장학생 선발 결과")
    extractor.set("장학생 선발 결과", result("결과"))

    crawl()

    [notice] = notices(session_factory)
    assert (notice.notice_kind, notice.extract_status, notice.scholarships) == ("결과", "DONE", [])


# --- 상시 만료 ------------------------------------------------------------------


def test_recruit_without_deadline_closes_after_60_days(crawl, site, extractor, session_factory):
    site.post("100", posted_at=kst(2026, 10, 1, 9, 0))
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", None)))
    crawl()
    assert scholarships(session_factory)["A 장학금"].status == "ALWAYS"

    crawl(at=kst(2026, 11, 30, 8, 59))  # 게시 후 60일 직전
    assert scholarships(session_factory)["A 장학금"].status == "ALWAYS"

    crawl(at=kst(2026, 11, 30, 9, 0))  # 게시 후 60일
    assert scholarships(session_factory)["A 장학금"].status == "CLOSED"
    assert len(extractor.requests) == 1


def test_criteria_notice_closes_after_one_year(crawl, site, extractor, session_factory):
    title = "2026학년도 성적우수장학금 선발 기준"
    site.post("100", title=title, posted_at=kst(2026, 3, 2, 9, 0))
    extractor.set(title, result("기준안내", ("성적우수장학금", None)))
    crawl()
    assert scholarships(session_factory)["성적우수장학금"].status == "ALWAYS"

    crawl(at=kst(2027, 3, 2, 8, 59))
    assert scholarships(session_factory)["성적우수장학금"].status == "ALWAYS"

    crawl(at=kst(2027, 3, 2, 9, 0))
    assert scholarships(session_factory)["성적우수장학금"].status == "CLOSED"


def test_old_notice_is_closed_on_save(crawl, site, extractor, session_factory):
    site.post("100", posted_at=kst(2026, 8, 1, 9, 0))
    extractor.set("2026 장학생 모집", result("모집", ("A 장학금", None)))

    crawl()

    assert scholarships(session_factory)["A 장학금"].status == "CLOSED"


# --- 추출 실패·대기 ------------------------------------------------------------


def test_failed_extraction_is_retried_next_run(crawl, site, extractor, session_factory):
    site.post("100", title="실패할 공지")
    site.post("101", title="정상 공지")
    extractor.set("실패할 공지", ExtractionError("시간 초과"), result("모집", ("A 장학금", None)))

    crawl()

    first = {n.external_id: n.extract_status for n in notices(session_factory)}
    assert first == {"100": "FAILED", "101": "DONE"}  # 한 공지가 실패해도 나머지는 처리

    crawl(at=NOW + timedelta(hours=1))  # 공지 내용은 그대로지만 FAILED라 다시 추출

    second = {n.external_id: n.extract_status for n in notices(session_factory)}
    assert second == {"100": "DONE", "101": "DONE"}
    assert [r.title for r in extractor.requests] == ["실패할 공지", "정상 공지", "실패할 공지"]
    assert "A 장학금" in scholarships(session_factory)


def test_failed_notice_stops_after_three_attempts(crawl, site, extractor, session_factory):
    """같은 공지가 매시간 실패하며 Gemini 비용만 쓰지 않게 3번에서 멈춥니다(9.3)."""
    site.post("100", title="계속 실패할 공지")
    extractor.set("계속 실패할 공지", *[ExtractionError("응답 검증 실패")] * 5)

    for hour in range(5):
        crawl(at=NOW + timedelta(hours=hour))

    assert len(extractor.requests) == 3
    (notice,) = notices(session_factory)
    assert (notice.extract_status, notice.extract_attempts) == ("FAILED", 3)


def test_credit_exhausted_is_not_counted(crawl, site, extractor, session_factory):
    """402(선불 크레딧 소진)는 공지 탓이 아니므로 횟수에 세지 않고, 충전 뒤 다시 추출합니다."""
    site.post("100", title="공지")
    no_credit = ExtractionError("Gemini 호출 실패(HTTP 402)", counts_as_attempt=False)
    extractor.set("공지", *[no_credit] * 4, result("모집", ("A 장학금", None)))

    for hour in range(5):
        crawl(at=NOW + timedelta(hours=hour))

    (notice,) = notices(session_factory)
    assert (notice.extract_status, notice.extract_attempts) == ("DONE", 0)
    assert len(extractor.requests) == 5


def test_changed_notice_resets_attempts(crawl, site, extractor, session_factory):
    site.post("100", title="공지")
    extractor.set("공지", *[ExtractionError("시간 초과")] * 3, result("모집", ("A 장학금", None)))
    for hour in range(3):
        crawl(at=NOW + timedelta(hours=hour))
    assert notices(session_factory)[0].extract_attempts == 3

    site.post("100", title="공지", body="마감 연장")  # 해시가 바뀜
    crawl(at=NOW + timedelta(hours=3))

    (notice,) = notices(session_factory)
    assert (notice.extract_status, notice.extract_attempts) == ("DONE", 0)


def test_pending_backlog_is_processed_20_per_run(crawl, extractor, session_factory):
    with session_factory() as session:
        for i in range(25):
            session.add(
                Notice(
                    source_code="KOSAF",
                    external_id=str(i),
                    title=f"대기 공지 {i:02d}",
                    posted_at=POSTED,
                    url=f"https://example.test/{i}",
                    body_text="본문",
                    attachments=[],
                    content_hash="0" * 64,
                    extract_status="PENDING",
                )
            )
        session.commit()

    crawl()
    statuses = [n.extract_status for n in notices(session_factory)]
    assert statuses.count("DONE") == 20
    assert statuses.count("PENDING") == 5
    assert [r.title for r in extractor.requests] == [f"대기 공지 {i:02d}" for i in range(20)]

    crawl(at=NOW + timedelta(hours=1))
    assert all(n.extract_status == "DONE" for n in notices(session_factory))
    assert len(extractor.requests) == 25


def test_new_notices_are_extracted_before_backlog(crawl, site, extractor, session_factory):
    with session_factory() as session:
        for i in range(20):
            session.add(
                Notice(
                    source_code="KOSAF",
                    external_id=str(i),
                    title=f"대기 공지 {i:02d}",
                    posted_at=POSTED,
                    url=f"https://example.test/{i}",
                    body_text="본문",
                    attachments=[],
                    content_hash="0" * 64,
                    extract_status="FAILED",
                )
            )
        session.commit()
    site.post("100", title="새 공지")

    crawl()

    assert extractor.requests[0].title == "새 공지"
    assert len(extractor.requests) == 21  # 새 공지 1건 + 대기 20건


# --- 재확인·백필 -----------------------------------------------------------------


def test_open_notice_off_first_page_is_rechecked_daily(crawl, site, extractor, session_factory):
    site.post("100")
    crawl()
    site.pages[1] = []  # 1페이지에서 밀려남
    site.post("200", title="다른 공지", page=1)

    crawl(at=NOW + timedelta(hours=23))
    assert site.fetched.count("100") == 1  # 24시간이 안 지나 재확인하지 않음

    site.post("100", body="마감 연장", page=None)
    crawl(at=NOW + timedelta(hours=24, minutes=1))
    assert site.fetched.count("100") == 2
    assert len([r for r in extractor.requests if r.title == "2026 장학생 모집"]) == 2


def test_backfill_walks_pages_back_one_year(session_factory, extractor):
    site = FakeSite()
    site.post("p1", posted_at=kst(2026, 10, 1), page=1)
    site.post("p2", posted_at=kst(2026, 3, 1), page=2)
    site.post("p3a", posted_at=kst(2025, 11, 6), page=3)
    site.post("p3b", posted_at=kst(2025, 11, 4), page=3)  # 1년보다 오래됨
    site.post("p4", posted_at=kst(2025, 6, 1), page=4)

    outcome = run_crawl(
        sources=["SW_DEPT"],
        backfill=True,
        session_factory=session_factory,
        crawler_factory=lambda code: FakeCrawler(site),
        extract_fn=extractor,
        clock=lambda: NOW,
    )

    assert outcome.runs[0].new_count == 3
    assert {n.external_id for n in notices(session_factory)} == {"p1", "p2", "p3a"}
    assert "p4" not in site.fetched  # 3페이지에서 기간 밖 글을 만나 멈춤


# --- 실행 기록·잠금 ---------------------------------------------------------------


def test_site_failure_is_recorded_and_others_continue(session_factory, extractor):
    good, bad = FakeSite(), FakeSite(fail=True)
    good.post("1")
    sites = {"SW_DEPT": bad, "AIBC": good, "KOSAF": FakeSite()}

    outcome = run_crawl(
        session_factory=session_factory,
        crawler_factory=lambda code: FakeCrawler(sites[code]),
        extract_fn=extractor,
        clock=lambda: NOW,
    )

    runs = {r.source_code: r for r in outcome.runs}
    assert runs["SW_DEPT"].status == "FAILED"
    assert runs["SW_DEPT"].error == "RuntimeError: 목록 구조가 바뀜"
    assert (runs["AIBC"].status, runs["AIBC"].new_count) == ("DONE", 1)
    assert runs["KOSAF"].status == "DONE"
    with session_factory() as session:
        assert session.scalar(select(CrawlRun).where(CrawlRun.status == "RUNNING")) is None


def test_concurrent_runs_only_one_proceeds(session_factory, site, extractor):
    """두 실행을 실제로 동시에 시작해 하나만 진행되는지 봅니다(9.3)."""
    site.post("100")
    entered, release = threading.Event(), threading.Event()
    barrier = threading.Barrier(2)
    outcomes: list[jobs.CrawlOutcome] = []

    class SlowCrawler(FakeCrawler):
        def fetch_list(self, page: int) -> list[NoticeSummary]:
            entered.set()
            release.wait(timeout=10)
            return super().fetch_list(page)

    def worker() -> None:
        barrier.wait()
        outcomes.append(
            run_crawl(
                sources=["SW_DEPT"],
                session_factory=session_factory,
                crawler_factory=lambda code: SlowCrawler(site),
                extract_fn=extractor,
                clock=lambda: NOW,
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=10)
    for _ in range(100):  # 진행 중인 실행이 끝나기 전에 다른 실행이 건너뛰고 끝나야 함
        if outcomes:
            break
        threading.Event().wait(0.05)
    assert [o.skipped for o in outcomes] == [True]
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(o.skipped for o in outcomes) == [False, True]
    with session_factory() as session:
        runs = list(session.scalars(select(CrawlRun)))
    assert [(r.status, r.new_count) for r in runs] == [("DONE", 1)]
    assert len(extractor.requests) == 1


def test_start_runs_lock_under_simultaneous_start(session_factory):
    barrier = threading.Barrier(2)
    results: list[list[int] | None] = []

    def worker() -> None:
        barrier.wait()
        results.append(start_runs(session_factory, ["SW_DEPT", "AIBC"], NOW))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results, key=lambda r: r is None) == [[1, 2], None]


def test_stale_running_does_not_block(crawl, site, session_factory):
    with session_factory() as session:
        session.add(
            CrawlRun(source_code="SW_DEPT", status="RUNNING", started_at=NOW - timedelta(hours=2))
        )
        session.commit()

    assert not crawl().skipped


def test_recent_running_blocks(crawl, session_factory):
    with session_factory() as session:
        session.add(
            CrawlRun(
                source_code="SW_DEPT", status="RUNNING", started_at=NOW - timedelta(minutes=59)
            )
        )
        session.commit()

    assert crawl().skipped


def test_unknown_source_is_rejected(session_factory):
    with pytest.raises(ValueError):
        run_crawl(sources=["NOPE"], session_factory=session_factory)


# --- CLI ----------------------------------------------------------------------


def test_cli_passes_options(monkeypatch, capsys):
    calls = []

    def fake_run_crawl(**kwargs):
        calls.append(kwargs)
        return jobs.CrawlOutcome(skipped=True)

    monkeypatch.setattr(jobs, "run_crawl", fake_run_crawl)
    monkeypatch.setattr(jobs, "configure_logging", lambda: None)

    assert jobs.main(["--backfill", "--source", "SW_DEPT", "--source", "KOSAF"]) == 0
    assert calls == [{"sources": ["SW_DEPT", "KOSAF"], "backfill": True}]
    assert "건너뛰었습니다" in capsys.readouterr().out
