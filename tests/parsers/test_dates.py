"""날짜 해석 테스트 (T14, 계획서 2장 "날짜 해석")."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.parsers.dates import (
    SEOUL,
    academic_year,
    deadline_at,
    infer_year,
    kst,
    now_kst,
    parse_posted_at,
    to_kst,
)

POSTED_OCT = datetime(2026, 10, 20, 9, 0, tzinfo=SEOUL)
POSTED_DEC = datetime(2026, 12, 15, 9, 0, tzinfo=SEOUL)


def test_now_is_seoul_time():
    now = now_kst()
    assert now.utcoffset() == timedelta(hours=9)


def test_to_kst_converts_utc():
    # Cloud Run 서버 시간(UTC) 2026-11-04 15:30 → 한국 2026-11-05 00:30
    converted = to_kst(datetime(2026, 11, 4, 15, 30, tzinfo=UTC))
    assert converted == datetime(2026, 11, 5, 0, 30, tzinfo=SEOUL)
    assert converted.day == 5


def test_to_kst_rejects_naive_datetime():
    with pytest.raises(ValueError):
        to_kst(datetime(2026, 11, 5))  # noqa: DTZ001 (일부러 시간대 없이)


def test_kst_invalid_date_is_none():
    assert kst(2026, 2, 30) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026.10.22 15:33", datetime(2026, 10, 22, 15, 33, tzinfo=SEOUL)),
        ("2026.11.03", datetime(2026, 11, 3, 0, 0, tzinfo=SEOUL)),
        ("2026-10-29", datetime(2026, 10, 29, 0, 0, tzinfo=SEOUL)),
        ("등록일 2026-10-14", datetime(2026, 10, 14, 0, 0, tzinfo=SEOUL)),
        ("2026.9.1 0:03", datetime(2026, 9, 1, 0, 3, tzinfo=SEOUL)),
    ],
)
def test_parse_posted_at(text, expected):
    assert parse_posted_at(text) == expected


def test_parse_posted_at_without_date():
    assert parse_posted_at("조회수 503") is None


def test_infer_year_same_year():
    assert infer_year(11, POSTED_OCT) == 2026


def test_infer_year_crosses_new_year():
    # 12월 게시, 1월 마감 → 다음 해
    assert infer_year(1, POSTED_DEC) == 2027


def test_infer_year_uses_seoul_date_of_utc_posted_at():
    # UTC 12-31 16:00 = 한국 2027-01-01 01:00 → 1월 마감은 같은 해(2027)
    posted = datetime(2026, 12, 31, 16, 0, tzinfo=UTC)
    assert infer_year(1, posted) == 2027


def test_deadline_without_time_is_end_of_day():
    assert deadline_at(11, 12, posted_at=POSTED_OCT) == datetime(2026, 11, 12, 23, 59, tzinfo=SEOUL)


def test_deadline_with_time():
    assert deadline_at(11, 12, posted_at=POSTED_OCT, hour=18, minute=0) == datetime(
        2026, 11, 12, 18, 0, tzinfo=SEOUL
    )


def test_deadline_crosses_new_year():
    assert deadline_at(1, 5, posted_at=POSTED_DEC) == datetime(2027, 1, 5, 23, 59, tzinfo=SEOUL)


def test_deadline_explicit_year_wins():
    assert deadline_at(1, 5, posted_at=POSTED_OCT, year=2026) == datetime(
        2026, 1, 5, 23, 59, tzinfo=SEOUL
    )


def test_deadline_invalid_date_is_none():
    assert deadline_at(2, 30, posted_at=POSTED_OCT) is None


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2027, 2, 28), 2026),  # 2월까지는 전 학년도
        (date(2027, 3, 1), 2027),  # 3월 1일에 바뀜
        (date(2026, 11, 13), 2026),
        (date(2027, 1, 5), 2026),
    ],
)
def test_academic_year(day, expected):
    assert academic_year(day) == expected
