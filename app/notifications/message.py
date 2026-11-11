"""알림 메일 내용 (계획서 7.4).

보낼 항목 목록으로 제목과 본문(일반 텍스트 + HTML)을 만듭니다.

- 넣는 것: 장학금 이름, 마감, 판정 표시(지원 가능/확인 필요) 또는 남은 날, 상세 화면 링크,
  수신 거부 링크
- 넣지 않는 것: 판정 근거, 프로필 값, 조건 내용. 인증번호 도입 전에 가입한 계정은 주소 소유가
  확인되지 않아, 받는 사람이 계정 주인이 아닐 수 있습니다.
- 제목에는 장학금 이름을 넣지 않습니다(공지에서 온 문자열이 메일 헤더에 들어가지 않게).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from app.notifications.select import NEW_MATCH, Item
from app.parsers.dates import to_kst

SERVICE_NAME = "장학금 매칭"
DEADLINE_SECTION = "마감이 다가온 관심 장학금"
NEW_SECTION = "새로 올라온 장학금"
LABELS = {
    "ELIGIBLE": "지원 가능",
    "CONDITIONAL": "확인 필요",
    "DEADLINE_D3": "마감 3일 전",
    "DEADLINE_D1": "마감 1일 전",
}
NO_DEADLINE = "상시"
REASON = "장학금 매칭에 가입한 이메일이라 이 메일을 받았습니다."


@dataclass(frozen=True)
class MailContent:
    subject: str
    text: str
    html: str


def unsubscribe_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/unsubscribe?token={token}"


def _detail_url(base_url: str, scholarship_id: int) -> str:
    return f"{base_url.rstrip('/')}/scholarships/{scholarship_id}"


def _label(item: Item) -> str:
    return LABELS[item.result if item.kind == NEW_MATCH else item.kind]  # type: ignore[index]


def _deadline(item: Item) -> str:
    if item.apply_end is None:
        return NO_DEADLINE
    return to_kst(item.apply_end).strftime("%Y-%m-%d %H:%M")


def _one_line(name: str) -> str:
    return " ".join(name.split())


def _subject(new_count: int, deadline_count: int) -> str:
    parts = []
    if new_count:
        parts.append(f"새 장학금 {new_count}건")
    if deadline_count:
        parts.append(f"마감 임박 {deadline_count}건")
    return f"[{SERVICE_NAME}] {', '.join(parts)}"


def build_message(items: Sequence[Item], *, base_url: str, unsubscribe_token: str) -> MailContent:
    """`items`는 `select_items`의 결과(비어 있지 않음)입니다."""
    deadlines = [i for i in items if i.kind != NEW_MATCH]
    new = [i for i in items if i.kind == NEW_MATCH]
    unsubscribe = unsubscribe_url(base_url, unsubscribe_token)

    text_parts: list[str] = []
    html_parts: list[str] = []
    for title, section in ((DEADLINE_SECTION, deadlines), (NEW_SECTION, new)):
        if not section:
            continue
        lines = [f"■ {title}"]
        rows = []
        for item in section:
            name, label, due = _one_line(item.name), _label(item), _deadline(item)
            url = _detail_url(base_url, item.scholarship_id)
            lines.append(f"- [{label}] {name} (마감: {due})\n  {url}")
            rows.append(
                f'<li><strong>[{escape(label)}]</strong> <a href="{escape(url)}">{escape(name)}</a>'
                f" (마감: {escape(due)})<br>{escape(url)}</li>"
            )
        text_parts.append("\n".join(lines))
        html_parts.append(f"<h3>{escape(title)}</h3>\n<ul>\n" + "\n".join(rows) + "\n</ul>")

    text = "\n\n".join(text_parts) + f"\n\n--\n{REASON}\n수신 거부: {unsubscribe}\n"
    html = (
        "<div>\n"
        + "\n".join(html_parts)
        + f'\n<hr>\n<p style="color:#666;font-size:12px">{escape(REASON)}<br>'
        + f'<a href="{escape(unsubscribe)}">수신 거부</a>: {escape(unsubscribe)}</p>\n</div>\n'
    )
    return MailContent(subject=_subject(len(new), len(deadlines)), text=text, html=html)
