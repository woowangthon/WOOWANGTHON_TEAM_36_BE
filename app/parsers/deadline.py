"""제목의 마감일 정규식 (계획서 2장 표 "마감일", 5.2 추출 규칙).

소프트웨어학부 게시판 제목에는 마감이 거의 항상 `( ~11.12 18:00)` 형태로 붙습니다.
이 값은 AI의 `apply_end`와 비교하는 1차 값입니다(5.2: 장학금이 1개면 다르면 정규식 값 우선).

지원하는 형식(실제 제목에서 확인):
- `( ~11.12 18:00)`, `( ~ 11.12)`, `( ~10. 8. 12:00)`, `( ~9.30까지 신청-마감)`
- `(~11/6(금))`, `(~11/23(월) 오후4시)`, `(~12/31)`
- `( ~9.17 16:00, 기한 변경)`, `( ~10.26 12:00) 기간 연장 ~10.27 18:00` → **마지막 마감**(연장)을 씀
- `( ~2027.1.5 18:00)`처럼 연도가 있으면 그 연도를 씀

`접수: 10/26~11/4`처럼 `~` 앞에도 날짜가 있는 기간 표기도 `~` 뒤 날짜를 마감으로 읽습니다.
연도·시각 해석은 `parsers/dates.py`가 합니다(연도 없으면 게시일 기준, 시각 없으면 23:59).
"""

import re
from datetime import datetime

from app.parsers.dates import deadline_at

_WEEKDAY = r"(?:\(\s*[월화수목금토일]\s*\))?"
_DEADLINE = re.compile(
    r"~\s*"
    r"(?:(?P<year>\d{4})\s*[./]\s*)?"
    r"(?P<month>\d{1,2})\s*[./]\s*(?P<day>\d{1,2})(?!\d)\.?\s*" + _WEEKDAY + r"\s*"
    r"(?:(?P<hour>\d{1,2}):(?P<minute>\d{2})|(?P<ampm>오전|오후)\s*(?P<hour12>\d{1,2})\s*시)?"
)


def title_deadline(title: str, posted_at: datetime) -> datetime | None:
    """제목에서 마감 시각(한국 시간)을 뽑습니다. 없거나 없는 날짜면 None."""
    matches = list(_DEADLINE.finditer(title))
    if not matches:
        return None
    match = matches[-1]  # 연장 표기가 있으면 마지막 마감이 실제 마감
    hour, minute = _clock(match)
    return deadline_at(
        int(match["month"]),
        int(match["day"]),
        posted_at=posted_at,
        year=int(match["year"]) if match["year"] else None,
        hour=hour,
        minute=minute,
    )


def _clock(match: re.Match[str]) -> tuple[int | None, int | None]:
    if match["hour"] is not None:
        return int(match["hour"]), int(match["minute"])
    if match["ampm"] is not None:
        hour = int(match["hour12"]) % 12
        return (hour + 12 if match["ampm"] == "오후" else hour), 0
    return None, None
