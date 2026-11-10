"""장학금 목록·상세·상태 API (T23, 계획서 5.3·7.1·7.2·9.8). 시드 데이터로 확인합니다."""

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models import Notice, Scholarship
from app.parsers.dates import kst, now_kst
from tests.api.test_auth import api, factory, signup  # noqa: F401
from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401

PROFILE = {
    "name": "길동",
    "gender": "여",
    "birth_year": 2004,
    "gpa": 3.6,
    "credits": 15,
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
) -> int:
    with factory() as session:
        scholarship = Scholarship(
            notice_id=notice_id,
            name=name,
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
        "my_state": None,
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


# --- 상태(관심·신청완료·숨기기) ----------------------------------------------------------


def test_put_and_delete_state(student, factory):  # noqa: F811
    sid = seed(factory, "관심")
    response = student.put(f"/scholarships/{sid}/state", json={"state": "BOOKMARKED"})
    assert response.status_code == 200
    assert response.json() == {"scholarship_id": sid, "state": "BOOKMARKED"}
    assert student.get("/scholarships").json()["items"][0]["my_state"] == "BOOKMARKED"

    student.put(f"/scholarships/{sid}/state", json={"state": "APPLIED"})  # 덮어쓰기
    assert student.get("/scholarships").json()["items"][0]["my_state"] == "APPLIED"

    assert student.delete(f"/scholarships/{sid}/state").status_code == 204
    assert student.get("/scholarships").json()["items"][0]["my_state"] is None
    assert student.delete(f"/scholarships/{sid}/state").status_code == 204  # 없어도 204


def test_state_on_missing_scholarship_is_404(student):
    assert student.put("/scholarships/999/state", json={"state": "HIDDEN"}).status_code == 404


def test_invalid_state_is_422(student, factory):  # noqa: F811
    sid = seed(factory, "x")
    assert student.put(f"/scholarships/{sid}/state", json={"state": "LIKED"}).status_code == 422


def test_hidden_is_excluded_from_default_list(student, factory):  # noqa: F811
    keep = seed(factory, "보임", apply_end=kst(2026, 11, 10))
    hide = seed(factory, "숨김", apply_end=kst(2026, 11, 11))
    student.put(f"/scholarships/{hide}/state", json={"state": "HIDDEN"})
    student.put(f"/scholarships/{keep}/state", json={"state": "BOOKMARKED"})
    assert names(student.get("/scholarships")) == ["보임"]


def test_my_list_includes_closed(student, factory):  # noqa: F811
    open_id = seed(factory, "열림", apply_end=kst(2026, 11, 20))
    closed_id = seed(factory, "마감됨", status="CLOSED", apply_end=kst(2026, 10, 1))
    applied_id = seed(factory, "신청함", status="CLOSED", apply_end=kst(2026, 10, 2))
    seed(factory, "상태 없음")
    for sid, state in ((open_id, "BOOKMARKED"), (closed_id, "BOOKMARKED"), (applied_id, "APPLIED")):
        student.put(f"/scholarships/{sid}/state", json={"state": state})
    assert names(student.get("/scholarships", params={"state": "bookmarked"})) == [
        "마감됨",
        "열림",
    ]
    assert names(student.get("/scholarships", params={"state": "applied"})) == ["신청함"]


def test_state_is_per_user(student, factory):  # noqa: F811
    sid = seed(factory, "공용")
    student.put(f"/scholarships/{sid}/state", json={"state": "HIDDEN"})
    student.cookies.clear()
    signup(student, email="other@example.com")
    student.post("/auth/consent", json={"privacy": True, "sensitive": False})
    student.put("/profile", json=PROFILE)
    items = student.get("/scholarships").json()["items"]
    assert [(i["name"], i["my_state"]) for i in items] == [("공용", None)]


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
            "evidence": {"min_gpa": "직전학기 평점 3.5 이상"},
        },
        apply_end=kst(2026, 11, 12, 18),
    )
    sibling_open = add_scholarship(factory, notice_id, "B재단")
    sibling_closed = add_scholarship(factory, notice_id, "C재단", status="CLOSED")
    student.put(f"/scholarships/{sid}/state", json={"state": "BOOKMARKED"})

    body = student.get(f"/scholarships/{sid}").json()
    assert body["name"] == "A재단"
    assert body["result"] == "CONDITIONAL"
    assert body["my_state"] == "BOOKMARKED"
    assert body["conditions"] == [
        {"condition": "min_gpa", "outcome": "MET", "evidence": "직전학기 평점 3.5 이상"},
        {"condition": "max_income_bracket", "outcome": "UNKNOWN", "evidence": None},
    ]
    assert body["extra_conditions"] == ["봉사활동 20시간 이상"]
    assert body["required_documents"] == ["재학증명서", "성적증명서"]
    assert body["apply_start"] == "2026-11-01T00:00:00+09:00"
    assert body["notice"]["title"] == "교외장학금 3종 모집"
    assert body["notice"]["url"].startswith("https://example.com/notice/")
    assert body["siblings"] == [
        {"id": sibling_open, "name": "B재단", "status": "OPEN"},
        {"id": sibling_closed, "name": "C재단", "status": "CLOSED"},
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
