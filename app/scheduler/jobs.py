"""수집 작업 (계획서 2장 수집 전략, 5.2 재추출·장학금 상태, 9.3 정기 수집).

    uv run python -m app.scheduler.jobs                     # 정기 수집과 같은 1회 실행
    uv run python -m app.scheduler.jobs --backfill          # 게시일 기준 최근 1년 백필
    uv run python -m app.scheduler.jobs --source SW_DEPT    # 사이트 하나만

실행 흐름(`run_crawl`)
1. 시작 잠금: 한 트랜잭션에서 `pg_advisory_xact_lock` → 1시간 안에 시작된 `RUNNING`이 있으면 건너뜀
   → 없으면 사이트별 `CRAWL_RUNS`를 `RUNNING`으로 넣고 커밋(9.3 중복 실행 방지)
2. 사이트마다 목록 1페이지(백필은 1년치)의 상세를 받아 해시를 비교하고, 1페이지 밖의 `OPEN` 공지는
   24시간마다 다시 확인합니다. 새 글·해시가 바뀐 글만 PDF를 받고 `PENDING`으로 둡니다(2장).
3. 이번에 새로 받았거나 바뀐 공지를 먼저 추출하고, 그다음 대기 중인 `PENDING`·`FAILED`를
   최대 20건 추출합니다. 추출 실패는 `FAILED`로 두고 다음 공지를 계속 처리합니다(9.3).
4. 장학금 upsert: 같은 공지 안에서 이름이 같으면 id를 유지하고,
   결과에서 사라진 장학금은 `CLOSED`(5.2).
5. 상태 일괄 갱신: `OPEN`·`ALWAYS` → `CLOSED` 방향만(마감 경과, 상시 만료 60일·1년).
6. `CRAWL_RUNS`를 `DONE`/`FAILED`로 마감합니다.

요청 안에서 끝까지 동기로 실행합니다(`BackgroundTasks` 쓰지 않음, 9.3).
추출 함수에는 공지 텍스트(`ExtractRequest`)만 넘깁니다(8.1).
"""

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_sessionmaker
from app.core.logging import configure_logging, log_event
from app.crawlers.aibc import AibcCrawler
from app.crawlers.base import Crawler, RawNotice
from app.crawlers.kosaf import KosafCrawler
from app.crawlers.sw_dept import SwDeptCrawler
from app.extraction import ExtractionError, ExtractionResult, ExtractRequest, extract
from app.models import CrawlRun, Notice, Scholarship
from app.parsers.dates import now_kst
from app.parsers.deadline import title_deadline
from app.parsers.pdf import extract_text
from app.schemas.api.admin import CrawlRunOut

logger = logging.getLogger(__name__)

SITE_CRAWLERS: dict[str, Callable[[], Crawler]] = {
    "SW_DEPT": SwDeptCrawler,
    "AIBC": AibcCrawler,
    "KOSAF": KosafCrawler,
}
SOURCE_CODES = tuple(SITE_CRAWLERS)

CRAWL_LOCK_KEY = 36_2026_11  # pg_advisory_xact_lock 고정 키 (수집 작업 전용)
RUNNING_STALE_AFTER = timedelta(hours=1)  # 1시간 지난 RUNNING은 비정상 종료로 보고 무시
RECHECK_AFTER = timedelta(hours=24)  # 1페이지 밖 OPEN 공지 재확인 주기
PENDING_BATCH = 20  # 대기 중인 PENDING·FAILED를 한 번에 처리하는 최대 건수
BACKFILL_PERIOD = timedelta(days=365)
MAX_BACKFILL_PAGES = 60  # 목록 구조가 바뀌어 끝을 못 찾을 때의 안전장치
ALWAYS_RECRUIT_PERIOD = timedelta(days=60)  # 마감 없는 모집
ALWAYS_CRITERIA_PERIOD = timedelta(days=365)  # 기준안내
ERROR_TEXT_LIMIT = 500

ExtractFn = Callable[[ExtractRequest], ExtractionResult]
CrawlerFactory = Callable[[str], Crawler]


@dataclass
class CrawlOutcome:
    skipped: bool
    runs: list[CrawlRunOut] = field(default_factory=list)


@dataclass
class _SiteResult:
    changed: list[int] = field(default_factory=list)  # 새로 받았거나 해시가 바뀐 공지 id
    new_count: int = 0


# --- 실행 진입점 ------------------------------------------------------------


def run_crawl(
    *,
    sources: Iterable[str] | None = None,
    backfill: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    crawler_factory: CrawlerFactory | None = None,
    extract_fn: ExtractFn | None = None,
    clock: Callable[[], datetime] = now_kst,
) -> CrawlOutcome:
    codes = list(sources or SOURCE_CODES)
    unknown = set(codes) - set(SOURCE_CODES)
    if unknown:
        raise ValueError(f"알 수 없는 출처: {sorted(unknown)}")
    factory = session_factory or get_sessionmaker()
    make_crawler = crawler_factory or (lambda code: SITE_CRAWLERS[code]())
    extractor = extract_fn or extract
    now = clock()

    runs = start_runs(factory, codes, now)
    if runs is None:
        log_event(logger, "다른 수집이 진행 중이라 건너뜀", sources=codes)
        return CrawlOutcome(skipped=True)

    with factory() as session:
        runs = [session.get_one(CrawlRun, run_id) for run_id in runs]
        changed: list[int] = []
        for run in runs:
            try:
                with make_crawler(run.source_code) as crawler:
                    result = crawl_source(session, crawler, run.source_code, now, backfill)
                changed.extend(result.changed)
                run.new_count = result.new_count
                run.status = "DONE"
            except Exception as error:  # 사이트 하나가 실패해도 다른 사이트는 계속
                session.rollback()
                run.status = "FAILED"
                run.error = f"{type(error).__name__}: {error}"[:ERROR_TEXT_LIMIT]
                log_event(
                    logger,
                    "사이트 수집 실패",
                    logging.ERROR,
                    source=run.source_code,
                    error=type(error).__name__,
                )
            session.commit()

        extract_notices(session, changed, extractor, now)
        extract_notices(session, pending_notice_ids(session, exclude=changed), extractor, now)
        close_expired(session, now)

        for run in runs:
            run.finished_at = clock()
        session.commit()
        outs = [CrawlRunOut.model_validate(run, from_attributes=True) for run in runs]
        log_event(
            logger,
            "수집 완료",
            runs={r.source_code: r.status for r in outs},
            changed=len(changed),
        )
        return CrawlOutcome(skipped=False, runs=outs)


def start_runs(factory: sessionmaker[Session], codes: list[str], now: datetime) -> list[int] | None:
    """중복 실행 방지(9.3). 이미 진행 중이면 None.

    Neon pooler는 트랜잭션마다 연결을 바꿔 쓰므로 세션 단위 잠금 대신 트랜잭션 단위 잠금을 잡고,
    같은 트랜잭션 안에서 RUNNING 확인과 INSERT를 한 뒤 커밋합니다. 잠금은 커밋하면 풀립니다.
    """
    with factory() as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CRAWL_LOCK_KEY})
        running = session.scalar(
            select(func.count())
            .select_from(CrawlRun)
            .where(CrawlRun.status == "RUNNING", CrawlRun.started_at > now - RUNNING_STALE_AFTER)
        )
        if running:
            return None
        runs = [CrawlRun(source_code=code, status="RUNNING", started_at=now) for code in codes]
        session.add_all(runs)
        session.flush()
        return [run.id for run in runs]


# --- 수집 ------------------------------------------------------------------


def crawl_source(
    session: Session, crawler: Crawler, source_code: str, now: datetime, backfill: bool
) -> _SiteResult:
    result = _SiteResult()
    cutoff = now - BACKFILL_PERIOD if backfill else None
    seen: set[str] = set()

    for external_id in _list_targets(crawler, cutoff):
        if external_id in seen:
            continue
        seen.add(external_id)
        _process_notice(session, crawler, source_code, external_id, now, cutoff, result)

    if not backfill:
        # 1페이지 밖으로 밀려난 모집 중 공지는 24시간마다 다시 확인합니다(2장).
        for external_id in _recheck_targets(session, source_code, seen, now):
            _process_notice(session, crawler, source_code, external_id, now, None, result)
    return result


def _list_targets(crawler: Crawler, cutoff: datetime | None) -> list[str]:
    """상세를 받을 글 ID 목록. 정기 수집은 1페이지, 백필은 게시일이 cutoff 이후인 페이지까지."""
    if cutoff is None:
        return [s.external_id for s in crawler.fetch_list(1)]
    ids: list[str] = []
    for page in range(1, MAX_BACKFILL_PAGES + 1):
        items = crawler.fetch_list(page)
        ids.extend(s.external_id for s in items if s.posted_at is None or s.posted_at >= cutoff)
        regular = [s for s in items if not s.is_pinned]  # 고정 공지는 매 페이지에 다시 나옴
        if not regular or all(s.posted_at is not None and s.posted_at < cutoff for s in regular):
            break
    return ids


def _recheck_targets(
    session: Session, source_code: str, seen: set[str], now: datetime
) -> list[str]:
    stale = now - RECHECK_AFTER
    query = (
        select(Notice.external_id)
        .where(
            Notice.source_code == source_code,
            Notice.last_checked_at.is_(None) | (Notice.last_checked_at < stale),
            Notice.scholarships.any(Scholarship.status == "OPEN"),
        )
        .order_by(Notice.id)
    )
    if seen:
        query = query.where(Notice.external_id.not_in(seen))
    return list(session.scalars(query))


def _process_notice(
    session: Session,
    crawler: Crawler,
    source_code: str,
    external_id: str,
    now: datetime,
    cutoff: datetime | None,
    result: _SiteResult,
) -> None:
    raw = crawler.fetch_detail(external_id)
    if cutoff is not None and raw.posted_at < cutoff:
        return  # 백필 기간 밖의 고정 공지
    digest = content_hash(raw)
    notice = session.scalar(
        select(Notice).where(Notice.source_code == source_code, Notice.external_id == external_id)
    )
    if notice is not None and notice.content_hash == digest:
        notice.last_checked_at = now
        session.commit()
        return

    is_new = notice is None
    if notice is None:
        notice = Notice(source_code=source_code, external_id=external_id)
        session.add(notice)
    notice.title = raw.title
    notice.posted_at = raw.posted_at
    notice.url = raw.url
    notice.body_text = notice_text(crawler, raw)  # PDF는 새 글·바뀐 글만 받음
    notice.attachments = [{"name": a.name, "url": a.url} for a in raw.attachments]
    notice.content_hash = digest
    notice.extract_status = "PENDING"
    notice.last_checked_at = now
    session.commit()
    result.changed.append(notice.id)
    result.new_count += int(is_new)


def content_hash(raw: RawNotice) -> str:
    """제목 + 공백을 정리한 본문 + 첨부 목록(파일명·URL). PDF 내용은 넣지 않습니다(2장)."""
    payload = [
        raw.title.strip(),
        " ".join(raw.body_text.split()),
        [[a.name, a.url] for a in raw.attachments],
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def notice_text(crawler: Crawler, raw: RawNotice) -> str:
    """추출에 넘길 텍스트: 본문 + `[첨부 PDF: 파일명]` + PDF 텍스트(골든셋 입력과 같은 형식).

    대기 중인 공지를 나중에 다시 추출할 때 PDF를 또 받지 않도록 이 텍스트를 저장합니다.
    """
    parts = [raw.body_text]
    for attachment in raw.attachments:
        if not attachment.name.lower().endswith(".pdf"):
            continue  # HWP·ZIP 등은 텍스트를 추출하지 않음(2장)
        try:
            data = crawler.fetch_attachment(attachment, referer=raw.url)
        except Exception as error:
            log_event(
                logger,
                "첨부 다운로드 실패",
                logging.WARNING,
                file_name=attachment.name,
                error=type(error).__name__,
            )
            continue
        if data is None:
            continue
        parts.append(f"[첨부 PDF: {attachment.name}]\n{extract_text(data, name=attachment.name)}")
    return "\n\n".join(part.strip() for part in parts)


# --- 추출 ------------------------------------------------------------------


def pending_notice_ids(session: Session, *, exclude: Iterable[int]) -> list[int]:
    """이전부터 대기 중인 PENDING(관리자 재추출 포함)·FAILED 공지, 오래된 것부터 최대 20건."""
    excluded = list(exclude)
    query = (
        select(Notice.id)
        .where(Notice.extract_status.in_(("PENDING", "FAILED")))
        .order_by(Notice.id)
        .limit(PENDING_BATCH)
    )
    if excluded:
        query = query.where(Notice.id.not_in(excluded))
    return list(session.scalars(query))


def extract_notices(
    session: Session, notice_ids: Iterable[int], extractor: ExtractFn, now: datetime
) -> None:
    for notice_id in notice_ids:
        notice = session.get(Notice, notice_id)
        if notice is None:
            continue
        request = ExtractRequest(
            source_code=notice.source_code,
            title=notice.title,
            posted_at=notice.posted_at,
            text=notice.body_text,
        )
        try:
            result = extractor(request)
        except ExtractionError as error:
            notice.extract_status = "FAILED"
            session.commit()
            log_event(
                logger,
                "추출 실패",
                logging.WARNING,
                notice_id=notice.id,
                reason=str(error)[:ERROR_TEXT_LIMIT],
            )
            continue
        apply_extraction(session, notice, result, now)
        session.commit()


def apply_extraction(
    session: Session, notice: Notice, result: ExtractionResult, now: datetime
) -> None:
    """추출 결과 저장. 같은 이름의 장학금은 id를 유지하고, 사라진 장학금은 CLOSED(5.2)."""
    criteria = result.criteria
    notice.notice_kind = criteria.notice_kind
    notice.model_version = result.model_version
    notice.extracted_at = now
    notice.extract_status = "DONE"

    existing: dict[str, Scholarship] = {}
    for row in notice.scholarships:
        existing.setdefault(row.name, row)

    # 학부 게시판에서 장학금이 1개면 제목 마감일과 비교합니다(5.2).
    compare_title = notice.source_code == "SW_DEPT" and len(criteria.scholarships) == 1
    title_end = title_deadline(notice.title, notice.posted_at) if compare_title else None

    kept: set[int] = set()
    for item in criteria.scholarships:
        row = existing.get(item.scholarship_name)
        if row is None or row.id in kept:
            row = Scholarship(name=item.scholarship_name)
            notice.scholarships.append(row)
        apply_end, source, conflict = resolve_deadline(item.apply_end, title_end, compare_title)
        row.benefit_type = item.benefit_type
        row.benefit_amount_text = item.benefit_amount_text
        row.apply_start = item.apply_start
        row.apply_end = apply_end
        row.deadline_source = source
        row.deadline_conflict = conflict
        row.criteria = item.model_dump(mode="json")
        row.status = scholarship_status(criteria.notice_kind, apply_end, notice.posted_at, now)
        session.flush()
        kept.add(row.id)

    for row in notice.scholarships:
        if row.id not in kept:
            row.status = "CLOSED"  # 재추출 결과에서 사라짐


def resolve_deadline(
    ai_end: datetime | None, title_end: datetime | None, compare_title: bool
) -> tuple[datetime | None, str | None, bool]:
    """(쓸 마감, 출처, 충돌 여부). 둘 다 있고 다를 때만 충돌이며 정규식 값을 씁니다(5.2)."""
    if not compare_title or title_end is None:
        return ai_end, ("AI" if ai_end else None), False
    if ai_end is None:
        return title_end, "REGEX", False
    return title_end, "REGEX", ai_end != title_end


def scholarship_status(
    notice_kind: str | None, apply_end: datetime | None, posted_at: datetime, now: datetime
) -> str:
    """5.2 장학금 상태 표."""
    if apply_end is not None and apply_end <= now:
        return "CLOSED"
    if notice_kind == "모집":
        if apply_end is not None:
            return "OPEN"
        return "ALWAYS" if now < posted_at + ALWAYS_RECRUIT_PERIOD else "CLOSED"
    if notice_kind == "기준안내":
        return "ALWAYS" if now < posted_at + ALWAYS_CRITERIA_PERIOD else "CLOSED"
    return "CLOSED"


# --- 상태 일괄 갱신 ----------------------------------------------------------


def close_expired(session: Session, now: datetime) -> int:
    """OPEN·ALWAYS → CLOSED 방향만 갱신합니다. CLOSED는 되살리지 않습니다(5.2)."""
    rows = session.execute(
        select(Scholarship, Notice.notice_kind, Notice.posted_at)
        .join(Notice)
        .where(Scholarship.status.in_(("OPEN", "ALWAYS")))
    ).all()
    closed = 0
    for row, kind, posted_at in rows:
        if scholarship_status(kind, row.apply_end, posted_at, now) == "CLOSED":
            row.status = "CLOSED"
            closed += 1
    session.commit()
    return closed


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="장학금 공지 수집 (계획서 9.3)")
    parser.add_argument("--backfill", action="store_true", help="게시일 기준 최근 1년 수집")
    parser.add_argument("--source", action="append", choices=SOURCE_CODES, help="이 출처만 수집")
    args = parser.parse_args(argv)

    configure_logging()
    outcome = run_crawl(sources=args.source, backfill=args.backfill)
    if outcome.skipped:
        print("다른 수집이 진행 중이라 건너뛰었습니다.")
        return 0
    for run in outcome.runs:
        print(f"{run.source_code}: {run.status}, 새 공지 {run.new_count}건 {run.error or ''}")
    return 0 if all(run.status == "DONE" for run in outcome.runs) else 1


if __name__ == "__main__":
    sys.exit(main())
