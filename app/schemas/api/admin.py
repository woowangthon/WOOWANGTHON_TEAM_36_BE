"""관리자·정기 수집 API DTO (계획서 7.1 관리자 화면, 7.2, 9.3)."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel

from app.extraction.schemas import NoticeKind, SourceCode

CrawlRunStatus = Literal["RUNNING", "DONE", "FAILED"]
ExtractStatus = Literal["PENDING", "DONE", "FAILED"]


class CrawlRunOut(BaseModel):
    id: int
    source_code: SourceCode
    status: CrawlRunStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    new_count: int
    error: str | None


class AdminNotice(BaseModel):
    """관리자 화면의 공지 1건. 재추출 버튼을 붙일 대상입니다(7.1)."""

    id: int
    source_code: SourceCode
    title: str
    url: str
    posted_at: AwareDatetime
    notice_kind: NoticeKind | None
    extract_status: ExtractStatus
    extracted_at: AwareDatetime | None
    scholarship_count: int


class CrawlRunsResponse(BaseModel):
    """관리자 화면 한 번에 필요한 값 (화면 하나에 API 한 번, 9.8).

    - items: 사이트별 마지막 수집 시각·결과·오류를 보여 줄 최근 수집 기록 (최신순)
    - notices: 추출 실패·대기 공지 전부(먼저) + 최근 공지 (재추출 대상)
    """

    items: list[CrawlRunOut]
    notices: list[AdminNotice]


class ReextractResponse(BaseModel):
    """재추출은 요청 안에서 하지 않고 PENDING으로만 바꿉니다(9.3)."""

    notice_id: int
    extract_status: Literal["PENDING"]


class CrawlResponse(BaseModel):
    """POST /internal/crawl 결과. 다른 실행이 진행 중이면 skipped=true로 바로 끝납니다(9.3)."""

    skipped: bool
    runs: list[CrawlRunOut]
