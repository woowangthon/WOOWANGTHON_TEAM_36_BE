"""날짜 해석 (계획서 2장 "날짜 해석"). 날짜·시각 해석은 이 모듈 한 곳에서만 합니다.

- 모든 날짜·시각은 한국 시간(`Asia/Seoul`)으로 해석합니다. Cloud Run 서버 시간은 UTC이므로
  시간대가 없는 `datetime`은 만들지 않습니다.
- 연도가 없는 날짜(`11.12`)는 게시일의 연도로 보고, 월이 게시 월보다 작으면 다음 해로 봅니다
  (12월 게시, 1월 마감).
- 마감 시각이 없으면 그날 23:59로 봅니다.
- 학년도는 3월 1일에 시작합니다(2027학년도 = 2027-03-01 ~ 2028-02-말일).
"""

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
END_OF_DAY = time(23, 59)
_POSTED_AT = re.compile(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")


def now_kst() -> datetime:
    """현재 시각(한국 시간)."""
    return datetime.now(SEOUL)


def to_kst(value: datetime) -> datetime:
    """시간대가 있는 시각을 한국 시간으로 바꿉니다. 시간대가 없으면 오류입니다."""
    if value.utcoffset() is None:
        raise ValueError("시간대가 없는 datetime은 쓰지 않습니다(Asia/Seoul로 만들 것)")
    return value.astimezone(SEOUL)


def kst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime | None:
    """한국 시간 시각을 만듭니다. 없는 날짜(2월 30일 등)면 None."""
    try:
        return datetime(year, month, day, hour, minute, tzinfo=SEOUL)
    except ValueError:
        return None


def parse_posted_at(text: str) -> datetime | None:
    """게시판의 게시일 `2026.10.22 15:33`, `2026-10-29`를 한국 시간으로 읽습니다.

    게시일은 시각이 없으면 00:00입니다(마감일의 23:59 규칙과 다름).
    """
    match = _POSTED_AT.search(text)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    return kst(int(year), int(month), int(day), int(hour or 0), int(minute or 0))


def academic_year(day: date) -> int:
    """한국 날짜가 속한 학년도. 3월 1일에 바뀝니다(2027-02-28 → 2026, 2027-03-01 → 2027)."""
    return day.year if day.month >= 3 else day.year - 1


def infer_year(month: int, posted_at: datetime) -> int:
    """연도가 없는 날짜의 연도. 게시 월보다 이른 달이면 다음 해(12월 게시 → 1월 마감)."""
    posted = to_kst(posted_at)
    return posted.year + 1 if month < posted.month else posted.year


def deadline_at(
    month: int,
    day: int,
    *,
    posted_at: datetime,
    year: int | None = None,
    hour: int | None = None,
    minute: int | None = None,
) -> datetime | None:
    """마감 시각. 연도가 없으면 게시일 기준으로 추정하고, 시각이 없으면 23:59로 둡니다."""
    resolved_year = year if year is not None else infer_year(month, posted_at)
    if hour is None:
        return kst(resolved_year, month, day, END_OF_DAY.hour, END_OF_DAY.minute)
    return kst(resolved_year, month, day, hour, minute or 0)
