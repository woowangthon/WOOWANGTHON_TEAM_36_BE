# 편입생·외국인 판정 구현 계획 (T39 backend, T40 frontend)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로필에 편입생·외국인 여부를 받고, 학년·학기 조건과 새 조건(편입생 제외, 국적)을 규칙 엔진으로 판정합니다.

**Architecture:** backend 프로필에 bool 2개를 추가하고, 판정에는 `effective_semesters`(편입생이면 +4)를 씁니다. 추출 스키마에 `excludes_transfer_students`·`target_nationality`를 넣고 프롬프트를 `extract_v7`로 올립니다. frontend는 `gen:api`로 타입을 다시 만든 뒤 프로필 폼에 체크박스 2개를 넣습니다.

**Tech Stack:** FastAPI·Pydantic v2·SQLAlchemy·Alembic·pytest / React·React Hook Form·Zod·TanStack Query·MSW·Vitest

**Spec:** `docs/superpowers/specs/2026-11-12-account-profile-additions-design.md` 3.2, 4.3절. 이 계획에서 추가로 정한 것은 아래 "스펙에 더한 결정"에 있습니다.

## 스펙에 더한 결정 (2026-11-12)
1. **"편입생 제외"만 있는 공지**: 새 필드 `excludes_transfer_students: bool | None`. 편입생이면서 `completed_semesters == 0`(편입 첫 학기)이면 불충족, 그 밖은 충족(사용자 결정).
2. **배포 순서 대비**: `ProfileUpdate.is_transfer`·`is_foreign`은 `StrictBool = False` 기본값을 둡니다. 배포된 옛 화면이 필드 없이 PUT해도 422가 나지 않게 하기 위해서입니다. 응답 `Profile`에서는 필수입니다.
3. **`MeResponse.is_transfer: bool | None`** 추가: 내 장학금 화면의 학년 표시(`MyListPage.gradeLabel`)가 편입생에게 +4를 하기 위해 필요합니다. 프로필이 없으면 null.
4. 옛 칸 `user_profiles.grade`·`extra_semester`도 `effective_semesters`로 계산해 씁니다.

## Global Constraints
- 비밀번호·프로필 값은 로그에 남기지 않습니다. 사용자는 `user_id`로만 기록합니다.
- 추출 모듈(`app/extraction/`)은 사용자·프로필 모듈을 import하지 않습니다(import 경계 테스트).
- 숫자·학기 비교는 규칙 엔진 코드로만 합니다. LLM을 호출하지 않습니다.
- 새 필드는 평문으로 저장합니다. 암호화 대상은 소득·주소·특수자격뿐입니다.
- `ExtractedCriteria`를 바꾸면 계획서 5.2와 프롬프트 버전(`extract_v7`)을 함께 올립니다. 새 필드 기본값은 `None`이어서 v6 결과도 읽혀야 합니다.
- frontend는 `schema.gen.ts`의 타입만 씁니다. 손으로 쓴 API 타입은 쓰지 않습니다. 화면은 360px 폭에서도 깨지지 않아야 합니다.
- 문구
  - 체크박스: "편입생이에요", "외국인이에요 (대한민국 국적이 아님)"
  - 편입생일 때 칸 이름: "편입 후 마친 학기"
  - 편입생일 때 도움말: "3학년 편입 기준으로 4학기를 더해 학년을 계산해요"
- 범위: 이수학기는 편입생이 아니면 0~16, 편입생이면 0~12입니다. 오류 문장은 "편입 후 마친 학기는 0~12 사이로 입력하세요"입니다.
- 커밋 메시지는 카드 ID로 시작합니다(`T39: …`, `T40: …`).

## Review Focus
1. **편입생이 13~16학기를 적은 경우**: 서버와 화면 모두 0~12 오류를 냅니다. Task 1과 Task 5에 테스트가 있습니다.
2. **옛 화면이 새 필드 없이 PUT**: 200으로 저장되고 두 값은 false입니다. Task 1에 테스트가 있습니다.
3. **DB에 저장된 v6 조건 JSON(새 키 없음)**: 읽을 수 있고 두 조건은 비교하지 않습니다(`None`). Task 2와 Task 3에 테스트가 있습니다.
4. **편입 첫 학기(0) 사용자**: 학년은 3학년이고, 신·편입생 제외와 편입생 제외는 불충족입니다. 성적 칸은 숨겨집니다. Task 3과 Task 5에 테스트가 있습니다.
5. **편입생의 내 장학금 학년 표시**: 4학기를 더해 계산합니다. 예: 편입 후 2학기 → "4학년 (편입 후 2학기 이수)". Task 6에 테스트가 있습니다.

---

## backend (`WOOWANGTHON_TEAM_36_BE`, 브랜치 `feat/T39-transfer-foreign`)

### Task 1: 프로필 필드·마이그레이션·API

**Files:**
- Modify: `app/schemas/api/profile.py`, `app/models/user.py`(`UserProfile`), `app/api/profile.py`, `app/schemas/api/auth.py`(`MeResponse`), `app/api/auth.py`(`_me`)
- Create: `alembic/versions/f3b8d2a6c104_profile_transfer_foreign.py` (down_revision `132c673f3141`)
- Test: `tests/api/test_profile.py`, `tests/api/test_auth.py`

**Interfaces:**
- Produces:
  - `TRANSFER_CREDIT_SEMESTERS = 4`
  - `MAX_TRANSFER_SEMESTERS = MAX_SEMESTERS - TRANSFER_CREDIT_SEMESTERS` (12)
  - `effective_semesters(p: Profile) -> int`
  - `Profile.is_transfer: bool`, `Profile.is_foreign: bool`
  - `ProfileUpdate.is_transfer: StrictBool = False`, `ProfileUpdate.is_foreign: StrictBool = False`
  - `MeResponse.is_transfer: bool | None = None`

- [ ] **Step 1: 실패하는 테스트**
  - `test_profile.py`의 `PROFILE`에 `"is_transfer": False, "is_foreign": False`를 넣습니다. 기존 왕복 테스트가 새 필드를 확인하게 됩니다.
  - `test_put_profile_transfer_foreign_roundtrip`: `is_transfer=True, is_foreign=True, completed_semesters=2` PUT → GET 응답이 같고, DB 옛 칸이 effective 6 기준(`row.grade == 4`, `row.extra_semester is False`)
  - `test_put_profile_without_new_fields_defaults_false`: 새 키를 뺀 본문 → 200, 응답의 두 값이 `False`
  - `test_put_profile_transfer_semesters_over_12_rejected`: `is_transfer=True, completed_semesters=13` → 422, 메시지에 "편입 후 마친 학기는 0~12"
  - `test_put_profile_non_transfer_16_ok`: `is_transfer=False, completed_semesters=16` → 200
  - `test_put_profile_rejects_string_bool`: `is_foreign="yes"` → 422
  - `test_auth.py` `test_me_includes_is_transfer`: 프로필 저장 후 `/auth/me`의 `is_transfer`가 저장 값과 같고, 프로필이 없으면 `None`

- [ ] **Step 2: 실행해 실패 확인** — `uv run pytest tests/api/test_profile.py tests/api/test_auth.py -q` → 새 테스트 FAIL

- [ ] **Step 3: 구현**
  - `profile.py`
    - 상수 2개와 `effective_semesters`를 추가합니다.
    - `Profile`과 `ProfileUpdate`에 필드를 추가합니다.
    - `_derive`에서 `completed_semesters`가 정해진 직후에 검사합니다. `is_transfer`이고 값이 12보다 크면 위 문장으로 `ValueError`를 냅니다.
    - `excludes_new_students` 문서에 있는 "편입생이 아니라고 간주" 문장은 Task 3에서 지웁니다.
  - `UserProfile`: `is_transfer`·`is_foreign`을 `Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")`로 추가합니다.
  - 마이그레이션: `add_column` 2개(`nullable=False, server_default=sa.false()`), `downgrade`에서 `drop_column`. 문서 문자열은 "옛 서버는 새 칸을 읽지 않으므로 그대로 동작"입니다.
  - `api/profile.py`
    - `PLAIN_FIELDS`에 두 이름을 추가합니다.
    - `put_profile`의 옛 칸 계산에는 `effective = semesters + (TRANSFER_CREDIT_SEMESTERS if body.is_transfer else 0)`을 씁니다.
  - `_me`: `is_transfer=profile.is_transfer if profile else None`

- [ ] **Step 4: 통과 확인** — 같은 명령으로 PASS. `uv run pytest tests/migrations -q`도 PASS(head 하나)

- [ ] **Step 5: 커밋** — `T39: 프로필에 편입생·외국인 여부 추가`

### Task 2: 추출 스키마·프롬프트 v7·채점·골든셋

**Files:**
- Modify: `app/extraction/schemas.py`, `app/extraction/extractor.py`(`PROMPT_VERSION`), `eval/scoring.py`(`AUX_VALUE_FIELDS`), `tests/fixtures/golden/*.expected.json`(국적·편입 문장이 있는 4건: `sw_dept_1153811`, `sw_dept_1154665`, `sw_dept_1154730`, `sw_dept_1154905`)
- Create: `app/extraction/prompts/extract_v7.md` (v6을 복사해 고침)
- Test: `tests/extraction/test_schemas.py`, `tests/eval/test_scoring.py`

**Interfaces:**
- Produces:
  - `Nationality = Literal["domestic", "foreign"]`
  - `ScholarshipCriteria.excludes_transfer_students: bool | None = None`
  - `ScholarshipCriteria.target_nationality: Nationality | None = None`
  - 두 필드는 `CriteriaField`에도 추가합니다(`excludes_new_students` 다음, `enrollment_status` 앞에 이 순서로).

- [ ] **Step 1: 실패하는 테스트**
  - `test_v6_criteria_without_new_fields_load`: 새 키가 없는 dict로 `ScholarshipCriteria.model_validate` → 두 값이 `None`
  - `test_target_nationality_rejects_other`: `"korean"` → `ValidationError`
  - `test_new_fields_in_criteria_field`: 두 이름이 `CRITERIA_FIELDS`에 있음
  - `test_scoring.py`: `AUX_VALUE_FIELDS`에 두 필드가 들어 있고, 값이 같거나 다를 때 보조 지표가 맞게 세어지는지(기존 `excludes_new_students` 보조 지표 테스트와 같은 모양)

- [ ] **Step 2: 실행해 실패 확인** — `uv run pytest tests/extraction tests/eval -q`

- [ ] **Step 3: 구현**
  - 스키마 필드와 주석을 추가합니다(v6 주석과 같은 형식, "extract_v7에서 추가").
  - `basic_conditions` 주석에서 "국적"을 지웁니다.
  - `PROMPT_VERSION = "extract_v7"`
  - `extract_v7.md`에서 v6과 달라지는 점:
    - 37행 근처 `basic_conditions` 예시에서 "대한민국 국적"을 지웁니다. "대한민국 국적의 국내 4년제 … 재학생"은 국적 부분을 `target_nationality = "domestic"`으로 옮기고, 학교 부분만 `basic_conditions`에 남깁니다.
    - 42행 근처 "필드로 옮긴 문장" 예시에 국적을 추가합니다.
    - 64행의 `excludes_new_students` 규칙에서 "편입생만 제외하면 `basic_conditions`에 넣는다(사용자는 모두 편입생이 아니라고 본다)"를 "→ `excludes_transfer_students = true`"로 바꿉니다.
    - 새 항목 `excludes_transfer_students`: "편입생 제외", "편입생은 지원 불가"면 `true`. "신·편입생 제외"는 `excludes_new_students`만 `true`로 하고 이 필드는 `null`입니다(신입생 제외에 편입 첫 학기가 함께 판정되기 때문). 공지에 없으면 `null`이고 `false`는 쓰지 않습니다.
    - 새 항목 `target_nationality`: "대한민국 국적자", "대한민국 국적의 … 학생"이면 `"domestic"`, "외국인 유학생", "외국 국적" 대상이면 `"foreign"`. "이중국적자 제외", "영주권자 제외", "재외국민", "대한민국 고등학교를 졸업한 자"처럼 국적만으로 판정할 수 없는 부분은 `extra_conditions`입니다(국적 필드는 따로 채움). 공지에 없으면 `null`입니다.
  - 골든셋 4건: 국적 문장을 `basic_conditions`에서 빼고 `target_nationality: "domestic"`을 넣습니다(학교 부분은 남김). `evidence`에도 근거 문장을 넣습니다. `sw_dept_1154905`의 "편입생 제외"는 `excludes_transfer_students: true`로 옮깁니다. `sw_dept_1154730`의 "편입" 4건은 문장을 읽고 위 규칙으로 분류합니다(선발 뒤 편입은 `notes`, 타 대학 편입 예정은 `extra_conditions`, 그대로 두는 것이 맞으면 그대로).
  - `eval/scoring.py`: `AUX_VALUE_FIELDS`에 두 필드를 추가하고, 모듈 문서에 "(extract_v7)" 한 줄을 추가합니다.

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/extraction tests/eval -q` PASS

- [ ] **Step 5: 커밋** — `T39: 추출 프롬프트 v7 (국적·편입생 제외 필드)`

### Task 3: 규칙 엔진

**Files:**
- Modify: `app/matching/rules.py`, `app/matching/engine.py`(`RULES`, 모듈 문서), `app/schemas/api/scholarships.py`(`ConditionName`)
- Test: `tests/matching/test_engine.py`

**Interfaces:**
- Consumes: Task 1 `effective_semesters`, `Profile.is_transfer`·`is_foreign`. Task 2의 두 조건 필드
- Produces:
  - `rules.excludes_transfer_students(p, c) -> Outcome`
  - `rules.target_nationality(p, c) -> Outcome`
  - `ConditionName`에 두 이름 추가
  - `RULES` 순서: `excludes_new_students` 다음에 `excludes_transfer_students`, `min_remaining_semesters`, `enrollment_status`. `target_nationality`는 `gender` 바로 앞

- [ ] **Step 1: 실패하는 테스트** (`profile()` 헬퍼 기본값에 `is_transfer=False, is_foreign=False` 추가)
  - `test_transfer_target_grades`: (`is_transfer=True`, 학기 0, `[3]`) → MET, (True, 0, `[1]`) → UNMET, (True, 4, `[4]`) → MET
  - `test_transfer_extra_semester`: (True, 4, `excludes_extra_semester=True`) → UNMET (effective 8)
  - `test_transfer_min_remaining`: (True, 2, `min_remaining_semesters=2`) → MET (8−6=2), (True, 3, 2) → UNMET
  - `test_transfer_excludes_new_students`: (True, 0) → UNMET, (True, 1) → MET
  - `test_excludes_transfer_students`: (False, 0) → MET, (True, 0) → UNMET, (True, 1) → MET. 필드가 `None`·`False`면 `None`
  - `test_target_nationality`: (`"domestic"`, 외국인 아님) → MET, (`"domestic"`, 외국인) → UNMET, (`"foreign"`, 외국인) → MET, (`"foreign"`, 외국인 아님) → UNMET. `None`이면 `None`
  - `test_new_conditions_ambiguous_is_unknown`의 매개변수에 두 필드를 추가합니다.
  - `test_foreign_domestic_only_is_ineligible`: `evaluate(profile(is_foreign=True), criteria(target_nationality="domestic"), TODAY).result == "INELIGIBLE"`
  - `test_api_contract.py`가 `ConditionName`과 `RULES` 일치를 검사한다면 그대로 통과해야 합니다.

- [ ] **Step 2: 실행해 실패 확인** — `uv run pytest tests/matching -q`

- [ ] **Step 3: 구현**
  - `target_grades`·`excludes_extra_semester`·`min_remaining_semesters`는 `effective_semesters(p)`를 씁니다.
  - `excludes_new_students`는 식을 그대로 두고 문서만 고칩니다("편입생은 편입 후 마친 학기로 봄").
  - 새 함수 2개를 위 규칙대로 만듭니다.
  - `engine.py` 모듈 문서의 "`basic_conditions`(국적·…)"에서 국적을 지웁니다.

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체 PASS

- [ ] **Step 5: 커밋** — `T39: 편입생·국적 조건 판정`

### Task 4: 문서와 완료 기준

**Files:**
- Modify (작업 공간, git 아님): `../docs/장학금_매칭_서비스_계획서.md`, `../docs/조건문장_분류표.md`, `../docs/작업카드.md`

- [ ] **Step 1: 계획서**
  - 4.1: 표에 "편입생 여부"(필수, 체크박스, 편입생이면 이수학기 = 편입 후 마친 학기이고 판정은 +4, 평문)와 "외국인 여부"(필수, 체크박스, 국적 조건, 평문)를 추가합니다. "편입 여부는 받지 않습니다" 문장은 이 결정(2026-11-12 변경, `extract_v7`)으로 바꿉니다.
  - 5.2: 표에 두 필드를 추가합니다.
  - 5.3: 학년·초과학기·남은 정규학기 행의 "이수학기"를 "판정용 이수학기(편입생 +4)"로 바꾸고, "신·편입생 제외" 행의 "편입생은 아니라고 봄"을 지웁니다. "편입생 제외"·"국적" 행을 추가합니다.
- [ ] **Step 2: 분류표** — Case 1-c 예시에서 국적을 빼고 "국적은 `target_nationality`"를 적습니다.
- [ ] **Step 3: 작업카드** — 스펙 2장 표대로 T37~T40 카드를 추가합니다(선행, 사양, 건드릴 파일, 완료 기준). T39·T40은 끝나면 `[x] 완료`로 표시합니다.
- [ ] **Step 4: 완료 기준 실행** — `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`
- [ ] **Step 5: 사람** — `uv run python -m eval.run`(Gemini 호출)은 사용자가 실행해 v6 점수와 비교합니다. 배포 뒤 관리자 재추출이나 백필을 실행해야 저장된 공지에 새 조건이 반영됩니다.

## frontend (`WOOWANGTHON_TEAM_36_FE`, 브랜치 `feat/T40-transfer-foreign`)

### Task 5: API 타입과 프로필 폼 규칙

**Files:**
- Modify: `src/api/schema.gen.ts`(생성), `src/components/profile/profileForm.ts`
- Test: `src/components/profile/profileForm.test.ts`

**Interfaces:**
- Consumes: backend Task 1~3의 OpenAPI
- Produces:
  - `MAX_TRANSFER_SEMESTERS = 12`, `TRANSFER_CREDIT_SEMESTERS = 4`
  - 폼 필드 `is_transfer: boolean`, `is_foreign: boolean`
  - `gradeLabel(semesters: number, isTransfer: boolean): string` (`MyListPage`에서 옮김)

- [ ] **Step 1: 타입 생성** — backend를 `uv run uvicorn app.main:app --port 8000`으로 띄우고 `npm run gen:api`를 실행합니다. `Profile`·`ProfileUpdate`에 두 필드, `MeResponse.is_transfer`, `ConditionCheck.condition`에 두 이름이 생겼는지 확인합니다.
- [ ] **Step 2: 실패하는 테스트**
  - 편입생이고 13 → `completed_semesters` 오류 "편입 후 마친 학기는 0~12 사이로 입력하세요". 편입생이 아니고 16 → 오류 없음
  - `toProfile`이 두 bool을 그대로 보내고, `toFormValues`가 그대로 읽으며 프로필이 없으면 두 값이 `false`
  - `formFieldOf(['is_transfer'])` → `'is_transfer'`
  - `gradeLabel(0, true)` → `"3학년 (편입 후 0학기 이수)"`, `gradeLabel(4, true)` → `"4학년 초과학기 (편입 후 4학기 이수)"`, `gradeLabel(5, false)` → `"3학년 (5학기 이수)"`(기존 문구 유지)
- [ ] **Step 3: 실행해 실패 확인** — `npm run test -- profileForm`
- [ ] **Step 4: 구현** — 위 Interfaces대로 `fields`, `rules`, `toProfile`, `toFormValues`(빈 폼에 `false`), `SAME_NAME`을 고칩니다. `gradeLabel`은 `effective = semesters + (isTransfer ? 4 : 0)`로 학년을 계산하고, 괄호 안에는 입력값을 보여 줍니다.
- [ ] **Step 5: 통과 확인 후 커밋** — `T40: 프로필 폼에 편입생·외국인 규칙 추가 (API 타입 다시 생성)`

### Task 6: 화면·목 데이터

**Files:**
- Modify: `src/pages/ProfilePage.tsx`, `src/pages/MyListPage.tsx`, `src/components/scholarship/format.ts`(`CONDITION_LABEL`), `src/mocks/data.ts`
- Test: `src/pages/AuthFlow.test.tsx` 또는 프로필 화면 테스트가 있는 파일, `src/pages/Scholarships.test.tsx`

**Interfaces:**
- Consumes: Task 5의 폼 필드와 `gradeLabel`

- [ ] **Step 1: 실패하는 테스트**
  - 프로필 화면에서 "편입생이에요"를 체크하면 이수학기 칸 이름이 "편입 후 마친 학기"가 되고, 도움말에 "3학년 편입 기준으로 4학기를 더해" 문구가 보입니다. 체크를 풀면 원래대로 돌아옵니다.
  - 두 체크박스를 체크하고 저장하면 PUT 본문에 `is_transfer: true, is_foreign: true`가 들어갑니다(MSW 요청을 캡처하는 기존 방식대로).
  - 상세 화면에서 `target_nationality` 조건이 "국적", `excludes_transfer_students`가 "편입생 제외"로 표시됩니다.
- [ ] **Step 2: 실행해 실패 확인** — `npm run test`
- [ ] **Step 3: 구현**
  - `ProfilePage`
    - 이수학기 칸 위에 "편입생이에요" 체크박스를 두고, `useWatch`로 칸 이름·도움말·placeholder(`0~12`)를 바꿉니다.
    - "외국인이에요 (대한민국 국적이 아님)" 체크박스는 기본 정보 영역 끝(양성인재 fieldset 앞)에 둡니다.
    - 360px 폭에서 줄바꿈이 깨지지 않게 합니다.
  - `MyListPage`: `summaryFacts`가 `gradeLabel(me.completed_semesters, me.is_transfer ?? false)`를 씁니다.
  - `CONDITION_LABEL`: `excludes_transfer_students: '편입생 제외'`, `target_nationality: '국적'`
  - `mocks/data.ts`
    - `mockProfile`에 `is_transfer: false, is_foreign: false`를 넣습니다.
    - 목 장학금 하나에 `{ condition: 'target_nationality', outcome: 'MET', evidence: '대한민국 국적의 대학교 재학생' }`을 추가합니다.
    - me 목 데이터에 `is_transfer`를 추가합니다.
- [ ] **Step 4: 완료 기준** — `npm run test`, `npm run lint` PASS. `npm run dev:mock`으로 360px 폭에서 프로필 화면을 직접 확인합니다.
- [ ] **Step 5: 커밋** — `T40: 프로필 화면에 편입생·외국인 체크박스 추가`
- [ ] **Step 6: PR** — backend와 frontend PR을 서로 링크합니다. backend PR이 먼저 머지돼야 합니다.
