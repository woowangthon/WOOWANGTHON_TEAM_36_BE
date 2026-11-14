# 관심·신청완료 따로 표시하기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관심(하트)과 신청완료를 서로 영향을 주지 않는 두 표시로 저장하고, 화면에서 각각 켜고 끌 수 있게 합니다.

**Architecture:** `user_scholarship_states`의 `state` 한 칸을 `bookmarked`·`applied` 불리언 두 칸으로 바꿉니다. `PUT /scholarships/{id}/state`는 보낸 칸만 upsert하고, 응답의 `my_state`는 두 불리언으로 바뀝니다. frontend는 생성 타입을 다시 만들고 저장 훅·목·화면을 칸 단위로 고칩니다.

**Tech Stack:** backend FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL · pytest / frontend React · TanStack Query · MSW · Vitest

**Spec:** `docs/superpowers/specs/2026-11-14-independent-bookmark-applied-design.md` (backend 저장소)

## Global Constraints

- 저장소: backend `WOOWANGTHON_TEAM_36_BE`(브랜치 `feat/T70-independent-bookmark-applied`, 이미 `origin/main`에서 만듦), frontend `WOOWANGTHON_TEAM_36_FE`(브랜치 `feat/T71-independent-bookmark-applied`, `origin/main`에서 새로 만듦).
- 필드 이름은 `bookmarked`, `applied`. 목록 필터 값은 그대로 `state=bookmarked|applied`.
- 주석·커밋 메시지는 한국어, 커밋 제목은 카드 ID로 시작(`T70: ...`, `T71: ...`). 새 의존성 없음.
- frontend는 API 타입을 손으로 쓰지 않고 `src/api/schema.gen.ts`의 타입만 씁니다.
- 날짜·시각 코드는 건드리지 않습니다. 로그에 프로필 값·토큰을 남기지 않습니다.
- push와 PR은 사용자에게 확인받은 뒤에 합니다. backend PR과 frontend PR은 서로 링크합니다.

## Review Focus

1. `PUT` 본문에 `{"bookmarked": null}`만 온 경우: 변경으로 치지 않고 422 → Task 2 Step 1.
2. 구버전 화면이 보내는 `{"state": "BOOKMARKED"}`와 `DELETE`: 500이 아니라 422·405 → Task 2 Step 1.
3. 마지막 표시를 끈 뒤(둘 다 `false`인 행이 남음): 목록에서 표시 안 한 것과 똑같이 보이고 `state` 목록에 나오지 않음 → Task 2 Step 1.
4. 이미 저장된 행의 마이그레이션: `APPLIED` 행은 `applied`만 켜지고, 되돌릴 때 둘 다 켠 행은 `APPLIED` → Task 2 Step 6.
5. 신청완료 탭에서 하트를 끈 경우: 줄이 그대로 남음(관심 탭에서 끄면 사라짐) → Task 5 Step 1.

---

### Task 1: 계획서와 작업카드 고치기

**Files (작업 공간 루트 `Scholarship/docs/`, git 저장소 아님):**
- Modify: `docs/장학금_매칭_서비스_계획서.md` (6장 표 326행, 7.1 352행, 7.2 373행, 7.4 454행)
- Modify: `docs/작업카드.md` (끝에 T70, T71 추가)

- [ ] **Step 1: 계획서 네 군데를 고칩니다**
  - 6장 `USER_SCHOLARSHIP_STATES`: `string state "BOOKMARKED / APPLIED / HIDDEN"` → `bool bookmarked "관심"`, `bool applied "신청완료. 관심과 따로 켜고 끔 (2026-11-14)"`
  - 7.1 내 장학금: "관심 / 신청완료 목록 (마감된 장학금 포함). 관심과 신청완료는 따로 표시하며 둘 다 켠 장학금은 두 목록에 모두 나옴 (2026-11-14)"
  - 7.2: `PUT / DELETE` 행 → `PUT | /scholarships/{id}/state | 관심·신청완료를 따로 켜고 끔. {bookmarked?, applied?} 중 보낸 값만 바꿈 (2026-11-14)`
  - 7.4 마감 임박: "관심(`bookmarked`)이고 신청완료가 아니며 `OPEN`인 장학금의 …" (뒤 문장은 그대로)
- [ ] **Step 2: 작업카드에 T70(backend)·T71(frontend)을 기존 카드 형식으로 추가합니다** — 선행, 사양(6장·7.1·7.2·7.4), 건드릴 파일(아래 Task 2~5의 Files), 할 일, 완료 기준. 상태는 `[~] 진행 중`.

---

### Task 2: backend — DB와 상태 API (T70)

**Files:**
- Create: `alembic/versions/<새 id>_split_bookmarked_applied.py` (`down_revision`은 `uv run alembic heads` 결과. 작성 시점 `a7c41e9d2b58`)
- Modify: `app/models/user.py` (`UserScholarshipState`, `USER_SCHOLARSHIP_STATES` 삭제), `app/models/__init__.py`(그 이름을 내보내고 있으면 삭제)
- Modify: `app/schemas/api/scholarships.py` (`UserState` 삭제, `ScholarshipSummary`, `StateRequest`, `StateResponse`)
- Modify: `app/api/scholarships.py` (`_base_query`, `_summary`, `_Row`, `pick_representative`, `STATE_FILTERS`, `list_scholarships`, `get_scholarship`, `put_state`, `delete_state` 삭제)
- Test: `tests/api/test_scholarships.py`, `tests/models/test_schema.py`, `tests/api/test_auth.py`

**Interfaces:**
- Produces (Task 3이 씀): `UserScholarshipState.bookmarked: Mapped[bool]`, `UserScholarshipState.applied: Mapped[bool]` (둘 다 `default=False, server_default="false"`, NOT NULL). `state` 칸과 CHECK 제약은 없어짐.
- Produces (Task 4가 씀, OpenAPI):
  - `ScholarshipSummary.bookmarked: bool`, `ScholarshipSummary.applied: bool` (`my_state` 삭제, `ScholarshipDetail`도 상속으로 같음)
  - `StateRequest { bookmarked: bool | None = None, applied: bool | None = None }`, `extra="forbid"`, 둘 다 `None`이면 검증 오류(422)
  - `StateResponse { scholarship_id: int, bookmarked: bool, applied: bool }`
  - `DELETE /scholarships/{id}/state` 없음

- [ ] **Step 1: 실패하는 API 테스트를 씁니다** (`tests/api/test_scholarships.py`). `test_put_and_delete_state`, `test_invalid_state_is_422`를 아래로 바꿉니다. 장학금 만들기는 그 파일의 기존 헬퍼를 그대로 씁니다.

```python
def test_bookmark_and_applied_are_independent(student, factory):
    put = lambda body: student.put(f"/scholarships/{sid}/state", json=body).json()
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


def test_scholarship_marked_both_is_in_both_state_lists(student, factory):
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True, "applied": True})
    for state in ("bookmarked", "applied"):
        assert names(student.get("/scholarships", params={"state": state})) == ["둘다"]


def test_unmarking_everything_looks_unmarked(student, factory):
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": True})
    student.put(f"/scholarships/{sid}/state", json={"bookmarked": False})
    item = student.get("/scholarships").json()["items"][0]
    assert (item["bookmarked"], item["applied"]) == (False, False)
    assert student.get("/scholarships", params={"state": "bookmarked"}).json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [{}, {"bookmarked": None}, {"state": "BOOKMARKED"}, {"bookmarked": True, "hidden": True}],
)
def test_invalid_state_body_is_422(student, factory, body):
    assert student.put(f"/scholarships/{sid}/state", json=body).status_code == 422


def test_delete_state_is_removed(student, factory):
    assert student.delete(f"/scholarships/{sid}/state").status_code == 405
```

  같은 파일의 나머지 테스트는 기계적으로 옮깁니다: `json={"state": "BOOKMARKED"}` → `{"bookmarked": True}`, `{"state": "APPLIED"}` → `{"applied": True}`, `my_state` 단언 → 두 불리언(175행 기대값은 `"bookmarked": False, "applied": False`). 같은 장학금 묶기 테스트(761·791·830행 근처)에 "신청완료만 켠 글도 대표로 먼저 뽑힌다" 한 건을 더합니다.
  `tests/models/test_schema.py:151`의 INSERT는 `(user_id, scholarship_id, bookmarked) VALUES (..., true)`로, `tests/api/test_auth.py:418-419`는 `bookmarked=True` / `applied=True`로 바꿉니다.

- [ ] **Step 2: 실패를 확인합니다** — `uv run pytest tests/api/test_scholarships.py -q` → 새 테스트가 422 또는 `KeyError: 'bookmarked'`로 실패.

- [ ] **Step 3: 모델과 스키마를 고칩니다** — 위 Interfaces 그대로. `StateRequest`의 "둘 다 None" 검사는 `model_validator(mode="after")`.

- [ ] **Step 4: API를 고칩니다** (`app/api/scholarships.py`)
  - `_base_query`는 `state` 대신 `func.coalesce(UserScholarshipState.bookmarked, false())`, 같은 식의 `applied`를 고릅니다(행이 없으면 `False`). 쿼리 수는 그대로여야 합니다(기존 쿼리 수 테스트).
  - `_Row`의 `my_state: str | None` → `bookmarked: bool`, `applied: bool`. `_summary`도 두 값을 받아 `"bookmarked"`, `"applied"`로 담습니다.
  - `pick_representative`의 첫 정렬 키: `not (r.bookmarked or r.applied)`.
  - `STATE_FILTERS = {"bookmarked": UserScholarshipState.bookmarked, "applied": UserScholarshipState.applied}`, 필터는 `.where(STATE_FILTERS[state].is_(True))`.
  - `put_state`: `changes = body.model_dump(exclude_none=True)`를 `insert(...).values(user_id=..., scholarship_id=..., **changes).on_conflict_do_update(index_elements=["user_id", "scholarship_id"], set_=changes).returning(bookmarked, applied)` 문장 하나로 실행합니다. 읽고 나서 쓰지 않습니다(동시에 누른 두 요청이 서로 덮어쓰지 않게).
  - `delete_state` 삭제.

- [ ] **Step 5: 마이그레이션을 씁니다** — upgrade: 두 칸 추가(`server_default false`) → `UPDATE ... SET bookmarked = (state = 'BOOKMARKED'), applied = (state = 'APPLIED')` → `ck_user_scholarship_states_state` 삭제 → `state` 칸 삭제. downgrade: `state` 칸을 NULL 허용으로 추가 → 둘 다 `false`인 행 삭제 → `state = CASE WHEN applied THEN 'APPLIED' ELSE 'BOOKMARKED' END` → NOT NULL + CHECK(`state IN ('BOOKMARKED', 'APPLIED')`) → 두 칸 삭제. 형식은 `5e9a1b7c3d42_remove_hidden_state.py`를 따릅니다.

- [ ] **Step 6: 마이그레이션을 실제 DB에서 왕복시킵니다** — 로컬 개발 DB에서 `uv run alembic upgrade head` 전에 `BOOKMARKED`·`APPLIED` 행을 하나씩 넣어 두고, upgrade 뒤 `(true,false)`·`(false,true)`인지, 한 행을 둘 다 `true`로 바꾼 뒤 `uv run alembic downgrade -1` 하면 `APPLIED`인지 확인하고 다시 `upgrade head`. `uv run alembic check`로 모델과 차이가 없는지 봅니다. 로컬 DB를 쓸 수 없으면 건너뛰지 말고 사용자에게 알립니다.

- [ ] **Step 7: 통과를 확인합니다** — `uv run pytest tests/api tests/models -q` → 모두 통과(`tests/notifications`는 Task 3에서 고칩니다).

- [ ] **Step 8: 커밋** — `T70: 관심과 신청완료를 따로 저장 (state → bookmarked·applied)`. 설계 문서와 이 계획 파일을 함께 넣습니다.

---

### Task 3: backend — 알림 메일 (T70)

**Files:**
- Modify: `app/notifications/select.py` (`Recipient`, `_deadline_kind`, `_new_match`), `app/notifications/jobs.py` (`load_recipients`)
- Test: `tests/notifications/test_select.py`, `tests/notifications/conftest.py`, `tests/notifications/test_jobs.py`

**Interfaces:**
- Consumes: Task 2의 `UserScholarshipState.bookmarked`, `.applied`
- Produces: `Recipient.bookmarked: frozenset[int]`, `Recipient.applied: frozenset[int]` (장학금 id. `states` 필드는 삭제)

- [ ] **Step 1: 실패하는 테스트를 씁니다** — `test_select.py`의 헬퍼를 `recipient(bookmarked=(), applied=(), sent=())`로 바꾸고 기존 호출을 옮깁니다(`states={1: "BOOKMARKED"}` → `bookmarked=[1]`). 마감 임박 매개변수 표에 둘 다 켠 경우를 넣습니다.

```python
@pytest.mark.parametrize(
    ("bookmarked", "applied", "status"),
    [((), (), "OPEN"), ((), (1,), "OPEN"), ((1,), (1,), "OPEN"), ((1,), (), "CLOSED")],
)
def test_deadline_requires_bookmark_not_applied_and_open(bookmarked, applied, status):
    c = cand(1, created_at=OLD, apply_end=kst(2026, 11, 14, 23, 59), status=status)
    assert select_items(recipient(bookmarked=bookmarked, applied=applied), [c], NOW) == []
```

  `conftest.py`의 `bookmark(factory, user_id, scholarship_id, *, applied: bool = False)`는 `bookmarked=True, applied=applied`인 행을 넣습니다. `test_jobs.py`에는 51행 테스트를 본떠 "관심 + 신청완료인 D-3 장학금은 메일에 없다" 한 건을 더합니다.

- [ ] **Step 2: 실패를 확인합니다** — `uv run pytest tests/notifications -q` → `TypeError`(`Recipient`에 `bookmarked` 없음).
- [ ] **Step 3: 구현합니다** — `_deadline_kind`: 관심이 아니거나 신청완료면 `None`. `_new_match`: 신청완료면 `None`(지금과 같음). `load_recipients`는 쿼리 한 번으로 두 칸을 읽어 사용자별 집합 두 개를 만듭니다(쿼리 4번 유지).
- [ ] **Step 4: 전체 확인** — `uv run pytest` 전부 통과, `uv run ruff check . && uv run ruff format --check .` 깨끗.
- [ ] **Step 5: 커밋** — `T70: 신청완료한 관심 장학금에는 마감 메일을 보내지 않음`

---

### Task 4: frontend — 타입·저장 훅·목 API (T71)

**Files:**
- Regenerate: `src/api/schema.gen.ts`
- Modify: `src/api/hooks/scholarships.ts`, `src/mocks/handlers.ts`, `src/mocks/data.ts`
- Test: `src/mocks/handlers.test.ts`

**Interfaces:**
- Consumes: Task 2의 OpenAPI
- Produces (Task 5가 씀):
  - `export type StateChange = Schemas['StateRequest']` (`UserState` export는 삭제)
  - `useSetScholarshipState(id)`의 `mutate` / `mutateAsync` 인자: `{ id: number } & StateChange` — 예: `{ id, bookmarked: true }`, `{ id, applied: false }`
  - 목록·상세 항목의 `bookmarked: boolean`, `applied: boolean`

- [ ] **Step 1: 브랜치를 만들고 타입을 다시 만듭니다** — `git switch -c feat/T71-independent-bookmark-applied origin/main`. backend 브랜치에서 `uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi()))"`로 스키마를 스크래치 폴더에 저장하고 `npx openapi-typescript <그 파일> -o src/api/schema.gen.ts`(`npm run gen:api`와 같은 생성기). 앱 import가 안 되면 backend를 8000번에 띄우고 `npm run gen:api`. 확인: `schema.gen.ts`에 `my_state`가 없고 `bookmarked`가 있음.
- [ ] **Step 2: 실패하는 목 테스트를 씁니다** (`handlers.test.ts`의 '상태를 바꾸고 지울 수 있다'를 교체)

```ts
it('관심과 신청완료를 따로 바꾼다', async () => {
  const put = async (body: Schemas['StateRequest']) =>
    (await fetch(api('/scholarships/3/state'), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json()
  expect(await put({ bookmarked: true })).toEqual({ scholarship_id: 3, bookmarked: true, applied: false })
  expect(await put({ applied: true })).toEqual({ scholarship_id: 3, bookmarked: true, applied: true })
  expect(await put({ bookmarked: false })).toEqual({ scholarship_id: 3, bookmarked: false, applied: true })
  const detail = await getJson<Schemas['ScholarshipDetail']>('/scholarships/3')
  expect([detail.bookmarked, detail.applied]).toEqual([false, true])
})
```

- [ ] **Step 3: 실패 확인** — `npx vitest run src/mocks` → 새 테스트 실패.
- [ ] **Step 4: 목을 고칩니다** — `data.ts`: `my_state: 'BOOKMARKED'` → `bookmarked: true, applied: false`, `'APPLIED'` → `bookmarked: false, applied: true`, `null` → 둘 다 `false`(목 데이터의 구성은 바꾸지 않음). `handlers.ts`: 목록 필터는 `d[state]`가 `true`인 것, `PUT`은 보낸 칸만 바꾸고 두 값을 돌려줌, `DELETE` 핸들러 삭제.
- [ ] **Step 5: 저장 훅을 고칩니다** (`scholarships.ts`)
  - `StateWrite = { id: number } & StateChange`.
  - `applyStateToList(items, params, write)`: 해당 항목에 보낸 칸만 덮어쓴 뒤, `params.state`가 있고 그 칸이 `false`가 된 항목은 목록에서 뺍니다(`params.state`의 값 `'bookmarked' | 'applied'`가 곧 칸 이름). 다른 칸만 바뀌면 항목은 남습니다.
  - `useScholarship`의 진행 중 반영도 보낸 칸만 덮어씁니다.
  - `mutationFn`은 항상 `PUT`이고(`DELETE` 분기 삭제) 응답의 `{ bookmarked, applied }`를 돌려줍니다. `onSuccess`는 그 두 값을 상세·목록 캐시에 기록합니다.
- [ ] **Step 6: 통과 확인** — `npx vitest run src/mocks` 통과. (화면 파일의 타입 오류는 Task 5에서 없앱니다. 커밋은 Task 5 끝에 한 번.)

---

### Task 5: frontend — 카드·상세·내 정보 화면 (T71)

**Files:**
- Modify: `src/components/scholarship/ScholarshipCard.tsx`, `src/components/scholarship/format.ts`, `src/pages/DetailPage.tsx`, `src/pages/MyListPage.tsx`
- Test: `src/pages/Scholarships.test.tsx` (그 밖에 `my_state`·`/state`를 쓰는 테스트·e2e가 있으면 함께: `git grep -n "my_state\|UserState" -- . ':!src/api/schema.gen.ts'`가 빈 결과여야 함)

**Interfaces:**
- Consumes: Task 4의 `StateChange`, `useSetScholarshipState`, 항목의 `bookmarked`·`applied`

- [ ] **Step 1: 실패하는 화면 테스트를 씁니다** (`Scholarships.test.tsx`의 '내 장학금' 묶음)
  - '…하트는 숨긴다' 테스트를 **'신청완료한 장학금도 하트를 켜고 끌 수 있다'**로 바꿉니다: `/my`의 신청완료 탭에서 `국가근로장학금` 줄에 `applied-badge`가 있고 `{ name: '관심', pressed: false }` 버튼이 있음 → 누르면 `{ name: '관심 해제', pressed: true }` → 관심 탭으로 가면 `국가근로장학금`이 `applied-badge`와 함께 있음.
  - **'신청완료 탭에서 하트를 꺼도 줄은 남는다'**: 위 상태에서 신청완료 탭으로 돌아가 하트를 끔 → `국가근로장학금` 줄이 그대로 있고 버튼은 `{ name: '관심', pressed: false }` → 관심 탭에는 없음.
  - **'관심 장학금을 신청완료로 표시해도 관심은 그대로다'**: `/scholarships/1`(두산연강재단, 관심)에서 `신청 완료`를 누름 → `/my` 관심 탭에 `두산연강재단 장학금`이 `applied-badge`와 `{ name: '관심 해제', pressed: true }`로 남아 있고, 신청완료 탭에도 있음.
  - **'신청완료한 장학금의 하트 저장이 실패하면 되돌린다'**: `server.use`로 `PUT /api/scholarships/5/state`를 500으로 → 신청완료 탭에서 하트를 누르면 '관심 장학금에 추가하지 못했어요. 다시 시도해 주세요.'가 뜨고 버튼은 `pressed: false`, 줄과 `applied-badge`는 그대로.
  - 대시보드 테스트('대시보드 카드에서도 …')의 `queryByRole('button', { name: /관심/ })).not.toBeInTheDocument()`는 `getByRole('button', { name: '관심', pressed: false })`가 있는 것으로 바꿉니다.
- [ ] **Step 2: 실패 확인** — `npx vitest run src/pages/Scholarships.test.tsx` → 새 테스트가 하트 버튼을 못 찾아 실패.
- [ ] **Step 3: 화면을 고칩니다**
  - `ScholarshipCard.tsx`: `BookmarkHeart`에서 신청완료일 때 `null`을 돌려주는 줄을 지우고 `on = item.bookmarked`, 누르면 `{ id, bookmarked: !on }`. `StatusBadge`와 흐리게 하는 조건은 `item.applied`. 하트를 숨기던 이유를 적은 주석을 "관심과 신청완료는 따로 켜고 끕니다"로 고칩니다.
  - `format.ts`: `STATE_LABEL = { bookmarked: '관심', applied: '신청완료' } as const satisfies Record<NonNullable<ListParams['state']>, string>`, 카드는 `STATE_LABEL.applied`.
  - `DetailPage.tsx`: `StateButtons`는 `applied: boolean`을 받고 누르면 `{ id, applied: !applied }`.
  - `MyListPage.tsx`: 빈 화면 문구 → `'관심 장학금이 없습니다. 장학금 카드의 하트를 누르면 여기에 모입니다.'`
- [ ] **Step 4: 전체 확인** — `npm run test` 전부 통과, `npm run lint` 깨끗, `npx tsc -b`(또는 `npm run build`) 타입 오류 없음.
- [ ] **Step 5: 눈으로 확인** — `npm run dev:mock`으로 띄워 360px 폭에서 신청완료 줄·카드에 하트와 신청완료 배지가 함께 나와도 줄이 깨지지 않는지 봅니다.
- [ ] **Step 6: 커밋** — `T71: 신청완료한 장학금에서도 관심 하트를 켜고 끌 수 있게`

---

### Task 6: 마무리

- [ ] **Step 1:** `docs/작업카드.md`의 T70·T71을 `[x] 완료`로 바꿉니다.
- [ ] **Step 2:** 두 저장소의 완료 기준 명령 결과를 사용자에게 보여 주고, push·PR(서로 링크, backend 먼저 머지·배포)을 진행할지 확인받습니다.
