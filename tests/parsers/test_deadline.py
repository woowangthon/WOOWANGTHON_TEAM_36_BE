"""제목 마감일 정규식 테스트 (T14). 형식은 실제 소프트웨어학부 게시판 제목에서 모았습니다."""

from datetime import datetime
from pathlib import Path

import pytest

from app.crawlers.sw_dept import parse_list
from app.parsers.dates import SEOUL
from app.parsers.deadline import title_deadline

POSTED = datetime(2026, 10, 20, 15, 0, tzinfo=SEOUL)
POSTED_DEC = datetime(2026, 12, 10, 10, 0, tzinfo=SEOUL)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "sw_dept"


def at(month: int, day: int, hour: int = 23, minute: int = 59, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SEOUL)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # 기본형: 날짜 + 시각
        (
            "2026년도 2학기 푸른등대 기부장학사업 신규 장학생(2차) 선발 안내 ( ~11.12 18:00)",
            at(11, 12, 18, 0),
        ),
        # 시각 없음 → 23:59, ~ 뒤 공백
        ("(재)관정재단 2027년도 장학생 추천 안내 ( ~ 11.12)", at(11, 12)),
        # 점 뒤 공백·끝 점
        ("두산연강재단 장학생 추천 안내 ( ~10. 8. 12:00)", at(10, 8, 12, 0)),
        # 슬래시 + 요일
        ("모의토익(11월) 응시 희망자 명단 제출 안내(~11/6(금))", at(11, 6)),
        # 슬래시 + 요일 + '오후N시'
        (
            "「2027학년도 1학기 개신프론티어 교과」이수학생 선발 안내(~11/23(월) 오후4시)",
            at(11, 23, 16, 0),
        ),
        # 오전 12시는 0시
        ("자정 마감 안내(~11/23 오전12시)", at(11, 23, 0, 0)),
        # 날짜 뒤 덧붙인 말
        ("천사장학금 선발 계획 ( ~9.17 16:00, 기한 변경)", at(9, 17, 16, 0, year=2027)),
        ("국가근로 안내 ( ~10.30까지 신청-마감)", at(10, 30)),
        # 연장 표기: 마지막 마감이 실제 마감
        ("장학생 추천 안내 ( ~10.26 12:00) 기간 연장 ~10.27 18:00", at(10, 27, 18, 0)),
        # 기간 표기(시작~끝)
        ("학점 교류 신청 안내 접수: 10/26~11/4", at(11, 4)),
        # 연도 있음
        ("특별 장학금 안내 ( ~2027.1.5 18:00)", at(1, 5, 18, 0, year=2027)),
    ],
)
def test_title_deadline_formats(title, expected):
    assert title_deadline(title, POSTED) == expected


def test_title_deadline_crosses_new_year():
    # 12월 게시, 1월 마감 → 다음 해
    assert title_deadline("동계 장학금 안내 ( ~1.8 17:00)", POSTED_DEC) == at(
        1, 8, 17, 0, year=2027
    )


def test_title_deadline_same_month_stays_in_year():
    assert title_deadline("장학 안내 ( ~12.20)", POSTED_DEC) == at(12, 20)


@pytest.mark.parametrize(
    "title",
    [
        "2027학년도 1학기 소프트웨어학부 성적우수 장학생 선발 기준 안내",  # 마감 없음
        "GPU 서버 대여 안내 (2025년 6월 10일부터 시행)",  # ~ 없는 날짜
        "장학 안내 ( ~2.30)",  # 없는 날짜
        "장학 안내 (~상시)",
    ],
)
def test_title_without_valid_deadline(title):
    assert title_deadline(title, POSTED) is None


def test_fixture_list_titles_with_tilde_all_parse():
    # 저장한 목록 1페이지의 장학 글 중 제목에 ~가 있으면 모두 마감을 읽을 수 있어야 함
    for summary in parse_list((FIXTURES / "list_p1.html").read_text(encoding="utf-8")):
        if "~" in summary.title:
            deadline = title_deadline(summary.title, summary.posted_at)
            assert deadline is not None, summary.title
            assert deadline >= summary.posted_at, summary.title
