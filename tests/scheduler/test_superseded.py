"""같은 출처·같은 이름의 옛 상시 공지 고르기 (QA 문제-2). DB 없이 도는 단위 테스트입니다."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.scheduler.jobs import AlwaysRow, superseded_always_ids

KST = ZoneInfo("Asia/Seoul")


def at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 9, 0, tzinfo=KST)


def test_older_semester_notice_is_superseded():
    # 2026-2학기 기준(4/1 게시)과 2027-1학기 기준(9/8 게시) 성적우수장학생
    rows = [
        AlwaysRow(72, "SW_DEPT", "성적우수장학생", at(4, 1)),
        AlwaysRow(14, "SW_DEPT", "성적우수장학생", at(9, 8)),
        AlwaysRow(71, "SW_DEPT", "학과수석장학생", at(4, 1)),
        AlwaysRow(15, "SW_DEPT", "학과수석장학생", at(9, 8)),
    ]
    assert superseded_always_ids(rows) == {72, 71}


def test_only_one_notice_is_kept():
    assert superseded_always_ids([AlwaysRow(1, "KOSAF", "주거안정장학금", at(9, 17))]) == set()


def test_different_sources_are_not_merged():
    # 학부·재단에 같은 이름이 올라와도 조건이 다를 수 있어 합치지 않음 (문제-3 결정)
    rows = [
        AlwaysRow(1, "SW_DEPT", "국가근로장학금", at(3, 1)),
        AlwaysRow(2, "KOSAF", "국가근로장학금", at(9, 1)),
    ]
    assert superseded_always_ids(rows) == set()


def test_different_names_are_not_merged():
    rows = [
        AlwaysRow(201, "KOSAF", "국가우수장학금(이공계) 성적우수 유형", at(4, 7)),
        AlwaysRow(202, "KOSAF", "국가우수장학금(이공계) 재학중우수자(2년지원) 유형", at(4, 7)),
        AlwaysRow(171, "KOSAF", "인문100년 장학금", at(1, 13)),
        AlwaysRow(205, "KOSAF", "인문100년장학금 (전공탐색유형)", at(3, 4)),
    ]
    assert superseded_always_ids(rows) == set()


def test_spacing_difference_is_same_name():
    rows = [
        AlwaysRow(170, "KOSAF", "예술체육비전 장학금", at(1, 13)),
        AlwaysRow(160, "KOSAF", "예술체육비전  장학금 ", at(3, 4)),
    ]
    assert superseded_always_ids(rows) == {170}


def test_same_posted_time_keeps_all():
    rows = [
        AlwaysRow(1, "SW_DEPT", "A 장학금", at(9, 8)),
        AlwaysRow(2, "SW_DEPT", "A 장학금", at(9, 8)),
    ]
    assert superseded_always_ids(rows) == set()
