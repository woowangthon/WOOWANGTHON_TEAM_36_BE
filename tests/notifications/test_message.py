"""알림 메일 내용 (계획서 7.4). 판정 근거·프로필 값은 메일에 넣지 않습니다."""

from app.notifications.message import build_message, unsubscribe_url
from app.notifications.select import Item
from app.parsers.dates import kst

BASE = "https://app.example"
TOKEN = "TOKEN"


def new(scholarship_id=1, name="새장학금", result="ELIGIBLE", apply_end=None) -> Item:
    return Item(scholarship_id, name, "NEW_MATCH", apply_end, result)


def deadline(scholarship_id=9, name="관심장학금", kind="DEADLINE_D3") -> Item:
    return Item(scholarship_id, name, kind, kst(2026, 11, 14, 23, 59), None)


def build(items):
    return build_message(items, base_url=BASE, unsubscribe_token=TOKEN)


def test_subject_counts_both_sections():
    content = build([deadline(), new(1), new(2)])

    assert content.subject == "[장학금 매칭] 새 장학금 2건, 마감 임박 1건"


def test_subject_omits_empty_side():
    assert build([new(1), new(2)]).subject == "[장학금 매칭] 새 장학금 2건"
    assert build([deadline()]).subject == "[장학금 매칭] 마감 임박 1건"


def test_empty_section_is_omitted():
    only_new = build([new()])
    only_deadline = build([deadline()])

    for body in (only_new.text, only_new.html):
        assert "새로 올라온 장학금" in body and "마감이 다가온 관심 장학금" not in body
    for body in (only_deadline.text, only_deadline.html):
        assert "마감이 다가온 관심 장학금" in body and "새로 올라온 장학금" not in body


def test_deadline_section_comes_first():
    text = build([deadline(), new()]).text

    assert text.index("마감이 다가온 관심 장학금") < text.index("새로 올라온 장학금")


def test_item_lines_show_label_deadline_and_link():
    content = build(
        [
            deadline(9, "관심A", "DEADLINE_D3"),
            deadline(8, "관심B", "DEADLINE_D1"),
            new(1, "가능", "ELIGIBLE", kst(2026, 11, 30, 18, 0)),
            new(2, "확인", "CONDITIONAL"),
        ]
    )

    for body in (content.text, content.html):
        for expected in (
            "관심A",
            "마감 3일 전",
            "마감 1일 전",
            "2026-11-14 23:59",
            "지원 가능",
            "확인 필요",
            "2026-11-30 18:00",
            "상시",
            "https://app.example/scholarships/9",
            "https://app.example/scholarships/1",
        ):
            assert expected in body, expected


def test_deadline_is_shown_in_seoul_time():
    from datetime import UTC, datetime

    item = new(1, apply_end=datetime(2026, 11, 14, 14, 59, tzinfo=UTC))

    assert "2026-11-14 23:59" in build([item]).text


def test_footer_has_reason_and_unsubscribe_link():
    content = build([new()])

    assert unsubscribe_url(BASE, TOKEN) == "https://app.example/unsubscribe?token=TOKEN"
    for body in (content.text, content.html):
        assert "장학금 매칭에 가입한 이메일이라 이 메일을 받았습니다." in body
        assert "https://app.example/unsubscribe?token=TOKEN" in body


def test_base_url_trailing_slash_is_ignored():
    content = build_message([new()], base_url=BASE + "/", unsubscribe_token=TOKEN)

    assert "https://app.example/scholarships/1" in content.text
    assert "example//" not in content.text


def test_html_escapes_name_and_subject_has_no_name():
    content = build([new(name='<script>alert("x")</script>\n주입')])

    assert "<script>" not in content.html
    assert "&lt;script&gt;" in content.html
    assert "\n" not in content.subject and "script" not in content.subject


def test_text_keeps_name_on_one_line():
    content = build([new(name="첫줄\r\n둘째줄")])

    assert "첫줄 둘째줄" in content.text
