"""장학금 목록·상세·상태 API (T23, 계획서 5.3·7.1·7.2·9.8). 시드 데이터로 확인합니다."""

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.matching.engine import condition_key
from app.models import Notice, NoticeAttachment, Scholarship, Source, UserConditionAnswer
from app.parsers.dates import kst, now_kst
from tests.api.test_auth import api, factory, signup  # noqa: F401
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

PROFILE = {
    "name": "길동",
    "gender": "여",
    "birth_year": 2004,
    "gpa": 3.6,
    "score_100": 88.0,
    "credits": 15,
    "gpa_total": 3.5,
    "score_100_total": 87.0,
    "completed_semesters": 5,
    "enrollment_status": "재학",
    "aibc_member": False,
    "income_bracket": None,
    "self_region": None,
    "parent_region": None,
    "special_flags": None,
    "full_tuition_waiver": None,
}
NO_LIMIT = {"target_department": "제한없음"}
ELIGIBLE = {**NO_LIMIT, "min_gpa": 3.0}
CONDITIONAL = {**NO_LIMIT, "max_income_bracket": 8}  # 프로필 소득분위 모름
INELIGIBLE = {**NO_LIMIT, "min_gpa": 4.0}

_counter = iter(range(1, 1_000_000))

# 목록은 조회 시각에 마감이 지난 장학금을 뺍니다(T45). 시드의 마감일(2026-11 중순)이
# 실제 날짜에 따라 지나지 않도록 API의 '지금'을 고정합니다.
NOW = kst(2026, 11, 5, 10)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    import app.api.scholarships as module

    monkeypatch.setattr(module, "now_kst", lambda: NOW)


def add_notice(factory, *, source="SW_DEPT", kind="모집", posted_at=None, title="공지") -> int:  # noqa: F811
    n = next(_counter)
    with factory() as session:
        notice = Notice(
            source_code=source,
            external_id=f"ext-{n}",
            title=title,
            posted_at=posted_at or kst(2026, 11, 1),
            url=f"https://example.com/notice/{n}",
            body_text="본문",
            content_hash=f"{n:064d}",
            notice_kind=kind,
            extract_status="DONE",
        )
        session.add(notice)
        session.commit()
        return notice.id


def add_scholarship(
    factory,  # noqa: F811
    notice_id: int,
    name: str = "장학금",
    *,
    criteria: dict | None = None,
    status: str = "OPEN",
    apply_end: datetime | None = None,
    benefit_type: str | None = "등록금성",
    deadline_conflict: bool = False,
    display_name: str | None = None,
) -> int:
    with factory() as session:
        scholarship = Scholarship(
            notice_id=notice_id,
            name=name,
            display_name=display_name,
            benefit_type=benefit_type,
            benefit_amount_text="100만원",
            apply_start=kst(2026, 11, 1),
            apply_end=apply_end,
            deadline_source="AI" if apply_end else None,
            deadline_conflict=deadline_conflict,
            status=status,
            criteria={"scholarship_name": name, **(criteria if criteria is not None else ELIGIBLE)},
        )
        session.add(scholarship)
        session.commit()
        return scholarship.id


def seed(factory, name: str, **kwargs) -> int:  # noqa: F811
    notice_kwargs = {k: kwargs.pop(k) for k in ("source", "kind", "posted_at") if k in kwargs}
    return add_scholarship(factory, add_notice(factory, **notice_kwargs), name, **kwargs)


@pytest.fixture
def student(api):  # noqa: F811
    """가입 → 동의 → 프로필 입력까지 마친 학생 (쿠키가 api 클라이언트에 남음)."""
    assert signup(api).status_code == 201
    assert api.post("/auth/consent", json={"privacy": True, "sensitive": False}).status_code == 200
    assert api.put("/profile", json=PROFILE).status_code == 200
    return api


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


# --- 접근 제어 ----------------------------------------------------------------------


def test_requires_login_consent_and_profile(api, factory):  # noqa: F811
    assert api.get("/scholarships").status_code == 401
    signup(api)
    assert api.get("/scholarships").status_code == 403  # 동의 전
    api.post("/auth/consent", json={"privacy": True, "sensitive": False})
    response = api.get("/scholarships")
    assert response.status_code == 404  # 프로필 미입력
    assert response.json()["detail"] == "프로필을 입력하세요"


# --- 목록: 노출 대상 ------------------------------------------------------------------


def test_list_shows_open_and_always_only(student, factory):  # noqa: F811
    seed(factory, "모집중", status="OPEN", apply_end=kst(2026, 12, 1))
    seed(factory, "상시", kind="기준안내", status="ALWAYS")
    seed(factory, "마감", status="CLOSED", apply_end=kst(2026, 10, 1))
    assert sorted(names(student.get("/scholarships"))) == ["모집중", "상시"]


def test_list_excludes_result_and_other_notices(student, factory):  # noqa: F811
    seed(factory, "모집")
    seed(factory, "결과 공지 장학금", kind="결과")
    seed(factory, "기타 공지 장학금", kind="기타")
    assert names(student.get("/scholarships")) == ["모집"]


def test_list_item_shape(student, factory):  # noqa: F811
    sid = seed(
        factory,
        "두산연강",
        source="KOSAF",
        apply_end=kst(2026, 11, 12, 18),
        deadline_conflict=True,
        posted_at=kst(2026, 10, 30, 9),
    )
    item = student.get("/scholarships").json()["items"][0]
    assert item == {
        "id": sid,
        "name": "두산연강",
        "display_name": "두산연강",  # 쉬운 이름이 없으면 원래 이름
        "source_code": "KOSAF",
        "source_name": item["source_name"],
        "is_internal": False,
        "benefit_amount_text": "100만원",
        "benefit_type": "등록금성",
        "apply_end": "2026-11-12T18:00:00+09:00",
        "status": "OPEN",
        "deadline_conflict": True,
        "posted_at": "2026-10-30T09:00:00+09:00",
        "result": "ELIGIBLE",
        "bookmarked": False,
        "applied": False,
        "open_conditions": [],
        "other_unknown": False,
        "also_posted_in": [],
    }


# --- 탭(판정 결과) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tab", "expected"),
    [
        ("eligible", ["가능"]),
        ("conditional", ["확인"]),
        ("all", ["가능", "확인", "불가"]),
    ],
)
def test_result_tabs(student, factory, tab, expected):  # noqa: F811
    seed(factory, "가능", criteria=ELIGIBLE, apply_end=kst(2026, 11, 20))
    seed(factory, "확인", criteria=CONDITIONAL, apply_end=kst(2026, 11, 21))
    seed(factory, "불가", criteria=INELIGIBLE, apply_end=kst(2026, 11, 22))
    assert names(student.get("/scholarships", params={"result": tab})) == expected


def test_result_reflects_profile_change(student, factory):  # noqa: F811
    seed(factory, "소득", criteria=CONDITIONAL)
    assert student.get("/scholarships").json()["items"][0]["result"] == "CONDITIONAL"
    student.put("/profile", json={**PROFILE, "income_bracket": 3})
    assert student.get("/scholarships").json()["items"][0]["result"] == "ELIGIBLE"


# --- 정렬 ---------------------------------------------------------------------------


def test_sort_by_deadline_puts_always_last(student, factory):  # noqa: F811
    seed(factory, "상시", kind="기준안내", status="ALWAYS", posted_at=kst(2026, 11, 5))
    seed(factory, "늦은 마감", apply_end=kst(2026, 12, 20), posted_at=kst(2026, 11, 3))
    seed(factory, "빠른 마감", apply_end=kst(2026, 11, 10), posted_at=kst(2026, 10, 1))
    assert names(student.get("/scholarships", params={"sort": "deadline"})) == [
        "빠른 마감",
        "늦은 마감",
        "상시",
    ]


def test_sort_by_latest(student, factory):  # noqa: F811
    seed(factory, "오래된", apply_end=kst(2026, 11, 10), posted_at=kst(2026, 10, 1))
    seed(factory, "최신", apply_end=kst(2026, 12, 20), posted_at=kst(2026, 11, 5))
    seed(factory, "중간", kind="기준안내", status="ALWAYS", posted_at=kst(2026, 10, 20))
    assert names(student.get("/scholarships", params={"sort": "latest"})) == [
        "최신",
        "중간",
        "오래된",
    ]


# --- 필터 ---------------------------------------------------------------------------


def test_source_filter(student, factory):  # noqa: F811
    seed(factory, "학부", source="SW_DEPT")
    seed(factory, "사업단", source="AIBC", criteria={**ELIGIBLE, "target_program": "제한없음"})
    seed(factory, "재단", source="KOSAF")
    assert sorted(names(student.get("/scholarships", params={"source": "internal"}))) == [
        "사업단",
        "학부",
    ]
    assert names(student.get("/scholarships", params={"source": "external"})) == ["재단"]


def test_benefit_type_filter_includes_mixed(student, factory):  # noqa: F811
    seed(factory, "등록금", benefit_type="등록금성", apply_end=kst(2026, 11, 10))
    seed(factory, "생활비", benefit_type="생활비성", apply_end=kst(2026, 11, 11))
    seed(factory, "혼합", benefit_type="혼합", apply_end=kst(2026, 11, 12))
    seed(factory, "미상", benefit_type=None, apply_end=kst(2026, 11, 13))
    assert names(student.get("/scholarships", params={"benefit_type": "등록금성"})) == [
        "등록금",
        "혼합",
    ]
    assert names(student.get("/scholarships", params={"benefit_type": "생활비성"})) == [
        "생활비",
        "혼합",
    ]


# --- 상태(관심·신청완료) ----------------------------------------------------------------


def test_bookmark_and_applied_are_independent(student, factory):  # noqa: F811
    """관심과 신청완료는 따로 켜고 끕니다. 보낸 값만 바뀝니다(7.2, 2026-11-14)."""
    sid = seed(factory, "관심")

    def put(body: dict) -> dict:
        response = student.put(f"/scholarships/{sid}/state", json=body)
        assert response.status_code == 200
        return response.json()

    assert put({"bookmarked": True}) == {
        "scholarship_id": sid,
        "bookmarked": True,
        "applied": False,
    }
    assert put({"applied": True}) == {"scholarship_id": sid, "bookmarked": True, "applied": True}
    assert put({"bookmarked": False}) == {
        "scholarship_id": sid,
        "bookmarked": False,
        "applied": True,
    }
    item = student.get("/scholarships").json()["items"][0]
    assert (item["bookmarked"], item["applied"]) == (False, True)


def test_scholarship_marked_both_is_in_both_state_lists(student, factory):  # noqa: F811
    sid = seed(factory, "둘다")
    seed(factory, "상태 없음")
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True, "applied": True})

    for state in ("bookmarked", "applied"):
        assert names(student.get("/scholarships", params={"state": state})) == ["둘다"]


def test_unmarking_everything_looks_unmarked(student, factory):  # noqa: F811
    sid = seed(factory, "껐음")
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": False})

    item = student.get("/scholarships").json()["items"][0]
    assert (item["bookmarked"], item["applied"]) == (False, False)
    assert student.get("/scholarships", params={"state": "bookmarked"}).json()["items"] == []


def test_state_on_missing_scholarship_is_404(student):
    assert student.put("/scholarships/999/state", json={"bookmarked": True}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"bookmarked": None},
        {"state": "BOOKMARKED"},  # 바뀌기 전 화면이 보내던 모양
        {"bookmarked": True, "hidden": True},
    ],
)
def test_invalid_state_body_is_422(student, factory, body):  # noqa: F811
    sid = seed(factory, "x")
    assert student.put(f"/scholarships/{sid}/state", json=body).status_code == 422
    item = student.get("/scholarships").json()["items"][0]
    assert (item["bookmarked"], item["applied"]) == (False, False)


def test_delete_state_is_removed(student, factory):  # noqa: F811
    """지우기는 PUT에 false를 보냅니다. DELETE는 없앴습니다."""
    sid = seed(factory, "x")
    assert student.delete(f"/scholarships/{sid}/state").status_code == 405


def test_default_list_keeps_scholarships_with_state(student, factory):  # noqa: F811
    bookmarked = seed(factory, "관심", apply_end=kst(2026, 11, 10))
    applied = seed(factory, "신청함", apply_end=kst(2026, 11, 11))
    seed(factory, "상태 없음", apply_end=kst(2026, 11, 12))
    student.put(f"/scholarships/{bookmarked}/state", json={"bookmarked": True})
    student.put(f"/scholarships/{applied}/state", json={"applied": True})
    assert names(student.get("/scholarships")) == ["관심", "신청함", "상태 없음"]


def test_my_list_includes_closed(student, factory):  # noqa: F811
    open_id = seed(factory, "열림", apply_end=kst(2026, 11, 20))
    closed_id = seed(factory, "마감됨", status="CLOSED", apply_end=kst(2026, 10, 1))
    applied_id = seed(factory, "신청함", status="CLOSED", apply_end=kst(2026, 10, 2))
    seed(factory, "상태 없음")
    for sid, flag in ((open_id, "bookmarked"), (closed_id, "bookmarked"), (applied_id, "applied")):
        student.put(f"/scholarships/{sid}/state", json={flag: True})
    assert names(student.get("/scholarships", params={"state": "bookmarked"})) == [
        "마감됨",
        "열림",
    ]
    assert names(student.get("/scholarships", params={"state": "applied"})) == ["신청함"]


def test_state_is_per_user(student, factory):  # noqa: F811
    sid = seed(factory, "공용")
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})
    student.cookies.clear()
    signup(student, email="other@example.com")
    student.post("/auth/consent", json={"privacy": True, "sensitive": False})
    student.put("/profile", json=PROFILE)
    items = student.get("/scholarships").json()["items"]
    assert [(i["name"], i["bookmarked"], i["applied"]) for i in items] == [("공용", False, False)]


def test_list_and_detail_use_display_name(student, factory):  # noqa: F811
    """쉬운 이름(extract_v9)이 있으면 display_name으로 내려 주고, 원래 이름도 유지합니다."""
    notice_id = add_notice(factory)
    sid = add_scholarship(
        factory,
        notice_id,
        "2026학년도 2학기 (재)두산연강재단 장학생 선발",
        display_name="두산연강재단 장학금",
    )
    add_scholarship(factory, notice_id, "B재단 장학생 모집", display_name="B재단 장학금")
    [item, _] = sorted(student.get("/scholarships").json()["items"], key=lambda i: i["id"])
    assert (item["name"], item["display_name"]) == (
        "2026학년도 2학기 (재)두산연강재단 장학생 선발",
        "두산연강재단 장학금",
    )
    detail = student.get(f"/scholarships/{sid}").json()
    assert detail["display_name"] == "두산연강재단 장학금"
    assert [(s["name"], s["display_name"]) for s in detail["siblings"]] == [
        ("B재단 장학생 모집", "B재단 장학금")
    ]


# --- 상세 ---------------------------------------------------------------------------


def test_detail(student, factory):  # noqa: F811
    notice_id = add_notice(factory, source="KOSAF", title="교외장학금 3종 모집")
    sid = add_scholarship(
        factory,
        notice_id,
        "A재단",
        criteria={
            **NO_LIMIT,
            "min_gpa": 3.5,
            "max_income_bracket": 8,
            "required_documents": ["재학증명서", "성적증명서"],
            "extra_conditions": ["봉사활동 20시간 이상"],
            "basic_conditions": ["대한민국 국적"],
            "notes": ["석차순 선발"],
            "evidence": {"min_gpa": "직전학기 평점 3.5 이상"},
            "evidence_source": {"min_gpa": "붙임1.zip / 선발 공고문.pdf"},
        },
        apply_end=kst(2026, 11, 12, 18),
    )
    sibling_open = add_scholarship(factory, notice_id, "B재단")
    sibling_closed = add_scholarship(factory, notice_id, "C재단", status="CLOSED")
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})

    body = student.get(f"/scholarships/{sid}").json()
    assert body["name"] == "A재단"
    assert body["result"] == "CONDITIONAL"
    assert (body["bookmarked"], body["applied"]) == (True, False)
    assert body["conditions"] == [
        {
            "condition": "min_gpa",
            "outcome": "MET",
            "evidence": "직전학기 평점 3.5 이상",
            "evidence_source": "붙임1.zip / 선발 공고문.pdf",
            "requirement": "3.5 이상",
            "ambiguous": False,
        },
        {
            "condition": "max_income_bracket",
            "outcome": "UNKNOWN",
            "evidence": None,
            "evidence_source": None,
            "requirement": "8구간 이하",
            "ambiguous": False,
        },
    ]
    assert body["extra_conditions"] == ["봉사활동 20시간 이상"]
    assert body["basic_conditions"] == ["대한민국 국적"]
    assert body["notes"] == ["석차순 선발"]
    assert body["required_documents"] == ["재학증명서", "성적증명서"]
    assert body["apply_start"] == "2026-11-01T00:00:00+09:00"
    assert body["notice"]["title"] == "교외장학금 3종 모집"
    assert body["notice"]["url"].startswith("https://example.com/notice/")
    assert body["notice"]["body_text"] == "본문"
    assert body["notice"]["attachments"] == []
    assert body["siblings"] == [
        {"id": sibling_open, "name": "B재단", "display_name": "B재단", "status": "OPEN"},
        {"id": sibling_closed, "name": "C재단", "display_name": "C재단", "status": "CLOSED"},
    ]
    assert body["unread_attachments"] == []
    assert body["input_truncated"] is False


# --- 자격을 좁히는 조건에 답하기 (5.3, 2026-11-12) -------------------------------------

VOLUNTEER = "봉사활동 20시간 이상"
TOEIC = "TOEIC 500점 이상"
TWO_EXTRAS = {**ELIGIBLE, "extra_conditions": [VOLUNTEER, TOEIC]}


def answer(api, sid: int, text: str, value: str | None):  # noqa: F811
    path = f"/scholarships/{sid}/conditions/{condition_key(text)}"
    return api.delete(path) if value is None else api.put(path, json={"answer": value})


def test_answers_change_result(student, factory):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    detail = student.get(f"/scholarships/{sid}").json()
    assert detail["result"] == "CONDITIONAL"
    assert detail["extra_checks"] == [
        {"key": condition_key(VOLUNTEER), "text": VOLUNTEER, "answer": None, "summary": None},
        {"key": condition_key(TOEIC), "text": TOEIC, "answer": None, "summary": None},
    ]

    first = answer(student, sid, VOLUNTEER, "YES")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "scholarship_id": sid,
        "key": condition_key(VOLUNTEER),
        "answer": "YES",
        "result": "CONDITIONAL",  # 하나 남음
    }
    assert answer(student, sid, TOEIC, "YES").json()["result"] == "ELIGIBLE"
    assert names(student.get("/scholarships?result=eligible")) == ["추가조건"]

    assert answer(student, sid, TOEIC, "NO").json()["result"] == "INELIGIBLE"  # 덮어쓰기
    detail = student.get(f"/scholarships/{sid}").json()
    assert [c["answer"] for c in detail["extra_checks"]] == ["YES", "NO"]

    cleared = answer(student, sid, TOEIC, None)
    assert cleared.status_code == 200
    assert cleared.json()["answer"] is None
    assert cleared.json()["result"] == "CONDITIONAL"
    assert answer(student, sid, TOEIC, None).status_code == 200  # 답이 없어도 같은 응답


def test_list_has_open_conditions(student, factory):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    seed(factory, "모름", criteria={**CONDITIONAL, "extra_conditions": [VOLUNTEER]})
    answer(student, sid, VOLUNTEER, "YES")
    items = {i["name"]: i for i in student.get("/scholarships").json()["items"]}
    assert items["추가조건"]["open_conditions"] == [
        {"key": condition_key(TOEIC), "text": TOEIC, "summary": None}
    ]
    assert items["추가조건"]["other_unknown"] is False
    assert items["모름"]["open_conditions"] == [
        {"key": condition_key(VOLUNTEER), "text": VOLUNTEER, "summary": None}
    ]  # 같은 문장이라도 답은 장학금마다 따로
    assert items["모름"]["other_unknown"] is True


def test_answer_unknown_condition_is_404(student, factory):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    assert answer(student, sid, "없는 조건", "YES").status_code == 404
    assert answer(student, 999_999, VOLUNTEER, "YES").status_code == 404
    other = seed(factory, "다른 장학금", criteria={**ELIGIBLE, "extra_conditions": ["다른 조건"]})
    assert answer(student, other, VOLUNTEER, "YES").status_code == 404  # 남의 장학금 문장


@pytest.mark.parametrize("body", [{"answer": "MAYBE"}, {"answer": "YES", "extra": 1}, {}])
def test_invalid_answer_is_422(student, factory, body):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    path = f"/scholarships/{sid}/conditions/{condition_key(VOLUNTEER)}"
    assert student.put(path, json=body).status_code == 422


def test_answers_are_per_user_and_encrypted(student, factory):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    answer(student, sid, VOLUNTEER, "YES")
    with factory() as session:
        [row] = session.query(UserConditionAnswer).all()
        assert b"YES" not in row.enc_answer
        assert row.answered_at.utcoffset() is not None

    student.cookies.clear()
    signup(student, email="other@example.com")
    student.post("/auth/consent", json={"privacy": True, "sensitive": False})
    student.put("/profile", json=PROFILE)
    detail = student.get(f"/scholarships/{sid}").json()
    assert [c["answer"] for c in detail["extra_checks"]] == [None, None]


def test_answers_deleted_with_account(student, factory):  # noqa: F811
    sid = seed(factory, "추가조건", criteria=TWO_EXTRAS)
    answer(student, sid, VOLUNTEER, "YES")
    assert student.delete("/auth/me").status_code in (200, 204)
    with factory() as session:
        assert session.query(UserConditionAnswer).count() == 0


def test_answer_ignored_after_sentence_changes(student, factory):  # noqa: F811
    """재추출로 문장이 바뀌면 옛 답은 맞지 않아 다시 '?'(확인 필요)가 됩니다."""
    sid = seed(factory, "추가조건", criteria={**ELIGIBLE, "extra_conditions": [VOLUNTEER]})
    assert answer(student, sid, VOLUNTEER, "YES").json()["result"] == "ELIGIBLE"
    with factory() as session:
        s = session.get(Scholarship, sid)
        s.criteria = {**s.criteria, "extra_conditions": ["봉사활동 30시간 이상"]}
        session.commit()
    detail = student.get(f"/scholarships/{sid}").json()
    assert detail["result"] == "CONDITIONAL"
    assert [c["answer"] for c in detail["extra_checks"]] == [None]
    assert answer(student, sid, VOLUNTEER, "NO").status_code == 404


# --- 읽지 못한 첨부 (D8) -------------------------------------------------------------


def add_attachment(
    factory,  # noqa: F811
    notice_id: int,
    position: int,
    status: str,
    name: str = "첨부.hwp",
    text: str = "",
):
    with factory() as session:
        session.add(
            NoticeAttachment(
                notice_id=notice_id,
                position=position,
                name=name,
                url=f"https://example.com/file/{notice_id}/{position}",
                file_kind="HWP",
                status=status,
                text=text,
                extracted_at=kst(2026, 11, 1),
            )
        )
        session.commit()


def test_unread_attachment_lowers_eligible_to_conditional(student, factory):  # noqa: F811
    unread_notice = add_notice(factory)
    add_attachment(factory, unread_notice, 0, "OK", name="공고문.pdf")
    add_attachment(factory, unread_notice, 1, "NO_TEXT", name="스캔 공고문.pdf")
    add_scholarship(
        factory, unread_notice, "첨부 못 읽음", criteria=ELIGIBLE, apply_end=kst(2026, 11, 10)
    )
    read_notice = add_notice(factory)
    add_attachment(factory, read_notice, 0, "OK", name="공고문.pdf")
    add_attachment(factory, read_notice, 1, "SKIPPED", name="11번째 첨부.pdf")  # 일부러 안 읽음
    add_scholarship(
        factory, read_notice, "첨부 다 읽음", criteria=ELIGIBLE, apply_end=kst(2026, 11, 11)
    )
    blocked = add_notice(factory)
    add_attachment(factory, blocked, 0, "FAILED")
    add_scholarship(factory, blocked, "불충족", criteria=INELIGIBLE, apply_end=kst(2026, 11, 12))

    items = student.get("/scholarships").json()["items"]
    assert [(i["name"], i["result"]) for i in items] == [
        ("첨부 못 읽음", "CONDITIONAL"),
        ("첨부 다 읽음", "ELIGIBLE"),
        ("불충족", "INELIGIBLE"),  # 불충족은 그대로
    ]


READ_TEXT = "선발 기준\n평점 3.0 이상 | 이수학점 12학점"


def test_detail_lists_unread_attachments(student, factory):  # noqa: F811
    notice_id = add_notice(factory)
    add_attachment(factory, notice_id, 0, "UNSUPPORTED", name="붙임.zip / 사진.png")
    add_attachment(factory, notice_id, 1, "OK", name="공고문.pdf", text=READ_TEXT)
    add_attachment(factory, notice_id, 2, "TOO_LARGE", name="대용량.pdf")
    add_attachment(factory, notice_id, 3, "SKIPPED", name="11번째 첨부.pdf")
    with factory() as session:
        session.get(Notice, notice_id).input_truncated = True
        session.commit()
    sid = add_scholarship(factory, notice_id, "장학금", criteria=ELIGIBLE)

    body = student.get(f"/scholarships/{sid}").json()
    assert body["result"] == "CONDITIONAL"
    assert body["unread_attachments"] == [
        {
            "name": "붙임.zip / 사진.png",
            "url": f"https://example.com/file/{notice_id}/0",
            "reason": "UNSUPPORTED",
        },
        {
            "name": "대용량.pdf",
            "url": f"https://example.com/file/{notice_id}/2",
            "reason": "TOO_LARGE",
        },
    ]
    assert body["input_truncated"] is True
    # 원문 보기: 읽은 첨부만 텍스트와 함께 (SKIPPED·못 읽은 첨부 제외)
    assert body["notice"]["attachments"] == [
        {
            "name": "공고문.pdf",
            "url": f"https://example.com/file/{notice_id}/1",
            "text": READ_TEXT,
        }
    ]


def test_detail_of_closed_scholarship(student, factory):  # noqa: F811
    sid = seed(factory, "마감", status="CLOSED", apply_end=kst(2026, 10, 1))
    assert student.get(f"/scholarships/{sid}").json()["status"] == "CLOSED"


def test_detail_missing_is_404(student):
    assert student.get("/scholarships/999").status_code == 404


# --- 성능·쿼리 수 (9.8) ----------------------------------------------------------------


def _seed_many(factory, count: int) -> None:  # noqa: F811
    base = now_kst()
    with factory() as session:
        notices = [
            Notice(
                source_code=("SW_DEPT", "AIBC", "KOSAF")[i % 3],
                external_id=f"bulk-{count}-{i}",
                title=f"공지 {i}",
                posted_at=base - timedelta(days=i % 200),
                url=f"https://example.com/bulk/{i}",
                body_text="본문",
                content_hash=f"{i:064d}",
                notice_kind="모집",
                extract_status="DONE",
            )
            for i in range(count)
        ]
        session.add_all(notices)
        session.flush()
        session.add_all(
            Scholarship(
                notice_id=n.id,
                name=f"장학금 {i}",
                benefit_type="등록금성",
                apply_end=base + timedelta(days=1 + i % 60),
                status="OPEN",
                criteria={
                    "scholarship_name": f"장학금 {i}",
                    **(ELIGIBLE, CONDITIONAL, INELIGIBLE)[i % 3],
                    "target_grades": [2, 3],
                    "regions": ["충청남도"],
                },
            )
            for i, n in enumerate(notices)
        )
        session.commit()


def _count_queries(engine, call) -> int:
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # noqa: ARG001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_query_count_does_not_grow_with_notices(student, factory, migrated_engine):  # noqa: F811
    _seed_many(factory, 10)
    small = _count_queries(migrated_engine, lambda: student.get("/scholarships"))
    _seed_many(factory, 290)
    assert len(student.get("/scholarships").json()["items"]) == 300
    large = _count_queries(migrated_engine, lambda: student.get("/scholarships"))
    assert small == large


def test_list_300_notices_under_one_second(student, factory):  # noqa: F811
    _seed_many(factory, 300)
    student.get("/scholarships")  # 워밍업
    started = time.perf_counter()
    response = student.get("/scholarships")
    elapsed = time.perf_counter() - started
    assert len(response.json()["items"]) == 300
    assert elapsed < 1.0, f"{elapsed:.3f}s"


# --- 조건 요약 (T43) ----------------------------------------------------------------

SUMMARIZED = {
    **NO_LIMIT,
    "min_gpa": 3.5,
    "grade_basis": "직전학기",
    "max_income_bracket": 8,
    "ambiguous_fields": ["max_income_bracket"],
    "extra_conditions": [{"text": VOLUNTEER, "summary": "봉사활동 20시간 이상 한 학생"}],
    "basic_conditions": [{"text": "품행이 단정한 자", "summary": "품행 단정"}],
}


def test_detail_has_summaries(student, factory):  # noqa: F811
    sid = seed(factory, "요약", criteria=SUMMARIZED)
    body = student.get(f"/scholarships/{sid}").json()
    checks = {c["condition"]: c for c in body["conditions"]}
    assert checks["min_gpa"]["requirement"] == "직전학기 3.5 이상"
    assert checks["min_gpa"]["ambiguous"] is False
    assert checks["max_income_bracket"]["requirement"] is None
    assert checks["max_income_bracket"]["ambiguous"] is True
    assert body["extra_checks"][0]["summary"] == "봉사활동 20시간 이상 한 학생"
    assert body["extra_conditions"] == [VOLUNTEER]
    assert body["basic_items"] == [{"text": "품행이 단정한 자", "summary": "품행 단정"}]
    assert body["basic_conditions"] == ["품행이 단정한 자"]


def test_list_open_conditions_have_summary(student, factory):  # noqa: F811
    seed(factory, "요약", criteria=SUMMARIZED)
    items = student.get("/scholarships").json()["items"]
    assert items[0]["open_conditions"][0]["summary"] == "봉사활동 20시간 이상 한 학생"


def test_detail_reads_legacy_string_conditions(student, factory):  # noqa: F811
    legacy = {**NO_LIMIT, "extra_conditions": [VOLUNTEER], "basic_conditions": ["대한민국 국적"]}
    sid = seed(factory, "옛형식", criteria=legacy)
    resp = student.get(f"/scholarships/{sid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extra_checks"][0]["summary"] is None
    assert body["basic_items"][0] == {"text": "대한민국 국적", "summary": None}


def test_answer_finds_key_of_object_item(student, factory):  # noqa: F811
    sid = seed(factory, "객체", criteria=SUMMARIZED)
    assert answer(student, sid, VOLUNTEER, "YES").status_code == 200


# --- 조회 시각 기준 마감 (T45) ------------------------------------------------------


def test_list_hides_deadline_passed_before_next_crawl(student, factory):  # noqa: F811
    """수집 사이에 마감이 지나 DB 상태가 아직 OPEN이어도 목록(모든 탭)에서 빠짐."""
    seed(factory, "지남", criteria=CONDITIONAL, apply_end=NOW - timedelta(minutes=1))
    seed(factory, "딱 지금", criteria=CONDITIONAL, apply_end=NOW)
    seed(factory, "남음", criteria=CONDITIONAL, apply_end=NOW + timedelta(minutes=1))
    seed(factory, "상시", criteria=CONDITIONAL, status="ALWAYS", apply_end=None)

    for result in ("conditional", "all"):
        response = student.get("/scholarships", params={"result": result})
        assert names(response) == ["남음", "상시"]


def test_deadline_passed_shows_closed_in_my_list_and_detail(student, factory):  # noqa: F811
    sid = seed(factory, "지남", apply_end=NOW - timedelta(hours=1))
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})

    [item] = student.get("/scholarships", params={"state": "bookmarked"}).json()["items"]
    assert item["status"] == "CLOSED"
    assert student.get(f"/scholarships/{sid}").json()["status"] == "CLOSED"


# --- 같은 장학금 묶기 (T45) ---------------------------------------------------------

END = kst(2026, 11, 20, 18)


def _source_name(factory, code: str) -> str:  # noqa: F811
    with factory() as session:
        return session.get(Source, code).name


def test_same_scholarship_on_two_boards_is_listed_once(student, factory):  # noqa: F811
    """이름 표기(공백·법인 표기·괄호)가 달라도 마감 시각이 같으면 하나로 묶고, 교내 글을 보여 줌."""
    kosaf = seed(factory, "(재)두산연강재단 장학생", source="KOSAF", apply_end=END)
    internal = seed(factory, "재단법인 두산연강재단  장학생", source="SW_DEPT", apply_end=END)

    [item] = student.get("/scholarships").json()["items"]
    assert item["id"] == internal != kosaf
    assert item["also_posted_in"] == [_source_name(factory, "KOSAF")]


def test_bookmarked_one_is_shown_even_if_external(student, factory):  # noqa: F811
    kosaf = seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END)
    seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END)
    student.put(f"/scholarships/{kosaf}/state", json={"bookmarked": True})

    [item] = student.get("/scholarships").json()["items"]
    assert item["id"] == kosaf
    assert item["also_posted_in"] == [_source_name(factory, "SW_DEPT")]


def test_applied_one_is_shown_even_if_external(student, factory):  # noqa: F811
    """신청완료만 켠 글도 내가 표시한 글이므로 먼저 보여 줍니다."""
    kosaf = seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END)
    seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END)
    student.put(f"/scholarships/{kosaf}/state", json={"applied": True})

    [item] = student.get("/scholarships").json()["items"]
    assert (item["id"], item["applied"]) == (kosaf, True)


def test_unmarked_again_is_not_preferred_over_internal(student, factory):  # noqa: F811
    """둘 다 끈 글은 표시하지 않은 것과 같아 교내 글이 대표가 됩니다."""
    kosaf = seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END)
    internal = seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END)
    student.put(f"/scholarships/{kosaf}/state", json={"bookmarked": True})
    student.put(f"/scholarships/{kosaf}/state", json={"bookmarked": False})

    [item] = student.get("/scholarships").json()["items"]
    assert item["id"] == internal


def test_different_deadline_or_always_is_not_grouped(student, factory):  # noqa: F811
    seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END)
    seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END + timedelta(days=1))
    seed(factory, "기준안내", source="KOSAF", status="ALWAYS", kind="기준안내")
    seed(factory, "기준안내", source="SW_DEPT", status="ALWAYS", kind="기준안내")

    items = student.get("/scholarships").json()["items"]
    assert len(items) == 4
    assert all(item["also_posted_in"] == [] for item in items)


def test_grouped_scholarship_appears_in_one_tab_only(student, factory):  # noqa: F811
    """두 글의 추출 결과가 달라도 대표 글의 판정 탭에만 나옴."""
    internal = seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END, criteria=ELIGIBLE)
    seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END, criteria=CONDITIONAL)

    eligible = student.get("/scholarships", params={"result": "eligible"}).json()["items"]
    assert [item["id"] for item in eligible] == [internal]
    assert names(student.get("/scholarships", params={"result": "conditional"})) == []


def test_source_filter_groups_within_that_board_only(student, factory):  # noqa: F811
    kosaf = seed(factory, "두산연강 장학생", source="KOSAF", apply_end=END)
    seed(factory, "두산연강 장학생", source="SW_DEPT", apply_end=END)

    [item] = student.get("/scholarships", params={"source": "external"}).json()["items"]
    assert (item["id"], item["also_posted_in"]) == (kosaf, [])


def test_my_list_is_not_grouped(student, factory):  # noqa: F811
    for source in ("KOSAF", "SW_DEPT"):
        sid = seed(factory, "두산연강 장학생", source=source, apply_end=END)
        student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})

    assert len(student.get("/scholarships", params={"state": "bookmarked"}).json()["items"]) == 2
