# '확인 필요' 판정 기준 수정 (추출 프롬프트 v6) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로필로 판정할 수 있는 조건(Case 1)과 조건이 아닌 안내(Case 2) 때문에 장학금이 '확인 필요'가 되지 않게 합니다. 수혜 이력·외부 기준 참조·섞인 문장 같은 Case 3은 확인 필요로 둡니다.

**Architecture:** 판정은 지금처럼 규칙 엔진(순수 함수)만 합니다.
- `ScholarshipCriteria`에 이수학기로 판정하는 필드 2개를 추가하고, `rules.py`에 규칙 2개를 둡니다.
- 문장 분류는 추출 프롬프트 v6에서 바꿉니다.
- 골든셋 정답과 채점을 새 기준에 맞추고, 실제 Gemini로 v5와 비교합니다.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, google-genai (Structured Output), uv

**Spec:** `docs/superpowers/specs/2026-11-12-extract-v6-condition-classes-design.md` (분류 기준은 3장, 작업 공간 `docs/조건문장_분류표.md`와 같음)

## Global Constraints

- 판정에 LLM을 쓰지 않습니다. 숫자·학기 비교는 `app/matching/rules.py`에서만 합니다(계획서 5.3).
- `app/extraction/`은 사용자·프로필 모듈을 import하지 않습니다(import 경계 테스트). 새 필드는 공지에서 뽑은 값입니다.
- 새 필드는 기본값이 `None`이어야 합니다. 그래야 DB(`scholarships.criteria`, JSONB)에 저장된 v5 결과가 그대로 읽힙니다. 마이그레이션은 없습니다.
- 새 필드:
  - `excludes_new_students: bool | None`
  - `min_remaining_semesters: int | None`, 범위 1~8
- 규칙:
  - `excludes_new_students`: 이수학기 0이면 `UNMET`, 1 이상이면 `MET`. 편입생은 아니라고 간주합니다.
  - `min_remaining_semesters`: `8 − completed_semesters ≥ N`이면 `MET`, 아니면 `UNMET`. 지금 다니는 학기도 남은 학기로 셉니다.
- 프롬프트 버전은 `extract_v6`이고, `model_version` = `{GEMINI_MODEL}+extract_v6`입니다.
- 테스트에서 Gemini를 호출하지 않습니다. 실제 호출은 Task 6의 `uv run python -m eval.run`에서만 합니다.
- 커밋 메시지는 한국어로 쓰고 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`로 끝냅니다. 이 작업은 카드 ID가 없어 v5(#48)처럼 `feat(…)`/`docs(…)` 접두어를 씁니다.
- 브랜치는 `feat/extract-v6-condition-classes`입니다(이미 있음). `main`에 직접 push하지 않습니다.

## Review Focus

1. **이수학기 0인 신입생에게 `min_remaining_semesters`를 적용하는 경우**: 남은 학기는 8이므로 N ≤ 8이면 충족해야 합니다. → Task 2의 `test_min_remaining_semesters` 경계값에 포함합니다.
2. **초과학기 학생(이수학기 8~16)**: `8 − 이수학기`가 0 이하입니다. N = 1이어도 불충족이어야 하고, 음수 때문에 예외가 나면 안 됩니다. → Task 2에서 이수학기 16 케이스로 확인합니다.
3. **v5로 저장된 criteria(JSON에 새 키 없음)를 읽는 경우**: 검증을 통과해야 하고 새 조건이 체크리스트에 나오면 안 됩니다. → Task 1의 `test_v5_criteria_without_new_fields_still_load`, Task 2의 `test_new_conditions_absent_are_not_compared`로 확인합니다.
4. **`excludes_new_students = false`**: 프롬프트는 `false`를 쓰지 말라고 하지만 모델이 낼 수 있습니다. `false`는 조건 없음으로 보고 비교하지 않아야 합니다. → Task 2에서 확인합니다.
5. **새 조건만 불충족이고 나머지는 충족인 경우**: 판정이 `INELIGIBLE`이어야 합니다. 새 조건이 3단계 판정에 실제로 들어가는지 확인하는 것입니다. → Task 2의 `test_new_condition_unmet_is_ineligible`로 확인합니다.

---

### Task 1: 추출 스키마에 새 필드 2개

**Files:**
- Modify: `app/extraction/schemas.py` (`CriteriaField`, `ScholarshipCriteria`, 모듈·필드 주석)
- Test: `tests/extraction/test_schemas.py`
- Modify: `docs/superpowers/specs/2026-11-12-extract-v6-condition-classes-design.md` (상태 줄을 `승인 (2026-11-12)`으로)

**Interfaces:**
- Produces:
  - `ScholarshipCriteria.excludes_new_students: bool | None = None`
  - `ScholarshipCriteria.min_remaining_semesters: int | None = Field(default=None, ge=1, le=8)`
  - `CriteriaField`에 `"excludes_new_students"`, `"min_remaining_semesters"` 추가. 위치는 `"excludes_extra_semester"` 바로 뒤입니다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/extraction/test_schemas.py`. 기존 `_example(**overrides)` 도우미를 씁니다)

```python
def test_new_student_and_remaining_semester_fields():
    s = ExtractedCriteria.model_validate(
        _example(excludes_new_students=True, min_remaining_semesters=1)
    ).scholarships[0]
    assert s.excludes_new_students is True
    assert s.min_remaining_semesters == 1


@pytest.mark.parametrize("value", [0, 9])
def test_min_remaining_semesters_range(value):
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(min_remaining_semesters=value))


def test_v5_criteria_without_new_fields_still_load():
    s = ScholarshipCriteria.model_validate({"scholarship_name": "v5 장학금"})
    assert s.excludes_new_students is None and s.min_remaining_semesters is None
```

`_example`이 장학금 필드를 덮어쓰는 방식이 다르면 그 파일의 기존 사용법을 따릅니다. 기존 `test_criteria_fields_cover_scholarship_fields`는 수정 없이 통과해야 합니다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction/test_schemas.py -q`
Expected: 새 테스트 3개가 FAIL(`extra_forbidden`). `test_criteria_fields_cover_scholarship_fields`는 아직 PASS.

- [ ] **Step 3: 구현**

`ScholarshipCriteria`의 `excludes_extra_semester` 아래에 두 필드를 추가하고, 같은 이름을 `CriteriaField`에 넣습니다. 주석에는 의미("신입생·편입생 제외면 true, 조건 없으면 null", "남은 정규학기 N학기 이상")와 "extract_v6에서 추가, 기본값 None이라 v5 결과도 읽힘"을 적습니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/extraction -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** (스펙 상태 줄 변경 포함)

```bash
git add app/extraction/schemas.py tests/extraction/test_schemas.py docs/superpowers/specs/2026-11-12-extract-v6-condition-classes-design.md
git commit -m "feat(extraction): 신·편입생 제외와 남은 정규학기 조건 필드 추가"
```

---

### Task 2: 규칙 엔진 규칙 2개와 API 조건 이름

**Files:**
- Modify: `app/matching/rules.py` (학년·재학 절)
- Modify: `app/matching/engine.py` (`RULES`, 모듈 docstring)
- Modify: `app/schemas/api/scholarships.py` (`ConditionName`)
- Test: `tests/matching/test_engine.py` (기존 `profile()`, `criteria()`, `outcome()` 도우미)

**Interfaces:**
- Consumes: Task 1의 두 필드
- Produces:
  - `rules.excludes_new_students(p: Profile, c: ScholarshipCriteria) -> Outcome`
  - `rules.min_remaining_semesters(p: Profile, c: ScholarshipCriteria) -> Outcome`
  - `rules.REGULAR_SEMESTERS = 8`
  - `ConditionName`에 `"excludes_new_students"`, `"min_remaining_semesters"`를 `"excludes_extra_semester"` 바로 뒤에 추가
  - `RULES` 순서: `target_grades`, `excludes_extra_semester`, `excludes_new_students`, `min_remaining_semesters`, `enrollment_status`, …

- [ ] **Step 1: 실패하는 테스트 작성** (`# --- 학년` 절의 `excludes_extra_semester` 테스트 뒤)

```python
@pytest.mark.parametrize(("semesters", "expected"), [(0, "UNMET"), (1, "MET"), (8, "MET")])
def test_excludes_new_students(semesters, expected):
    c = criteria(excludes_new_students=True)
    assert outcome("excludes_new_students", profile(completed_semesters=semesters), c) == expected


def test_excludes_new_students_false_is_not_compared():
    c = criteria(excludes_new_students=False)
    assert outcome("excludes_new_students", profile(completed_semesters=0), c) is None


@pytest.mark.parametrize(
    ("minimum", "semesters", "expected"),
    [
        (1, 0, "MET"),  # 신입생: 남은 학기 8
        (1, 7, "MET"),  # 마지막 정규학기 재학 중
        (1, 8, "UNMET"),  # 초과학기
        (1, 16, "UNMET"),  # 음수여도 예외 없이 불충족
        (2, 6, "MET"),
        (2, 7, "UNMET"),
        (8, 0, "MET"),
    ],
)
def test_min_remaining_semesters(minimum, semesters, expected):
    c = criteria(min_remaining_semesters=minimum)
    p = profile(completed_semesters=semesters)
    assert outcome("min_remaining_semesters", p, c) == expected


@pytest.mark.parametrize("field", ["excludes_new_students", "min_remaining_semesters"])
def test_new_conditions_ambiguous_is_unknown(field):
    assert outcome(field, profile(), criteria(ambiguous_fields=[field])) == "UNKNOWN"


def test_new_conditions_absent_are_not_compared():
    conditions = {check.condition for check in evaluate(profile(), criteria(), TODAY).checks}
    assert not conditions & {"excludes_new_students", "min_remaining_semesters"}


def test_new_condition_unmet_is_ineligible():
    c = criteria(excludes_new_students=True)
    assert evaluate(profile(completed_semesters=0), c, TODAY).result == "INELIGIBLE"


def test_new_conditions_follow_extra_semester_in_checklist():
    c = criteria(
        excludes_extra_semester=True,
        excludes_new_students=True,
        min_remaining_semesters=1,
        enrollment_status=["재학"],
    )
    order = [check.condition for check in evaluate(profile(), c, TODAY).checks]
    assert order[:4] == [
        "excludes_extra_semester",
        "excludes_new_students",
        "min_remaining_semesters",
        "enrollment_status",
    ]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/matching -q`
Expected: 새 테스트가 FAIL(조건이 체크리스트에 없어 `None`, 순서 불일치). 기존 테스트는 PASS.

- [ ] **Step 3: 구현**

- `rules.py`: `excludes_extra_semester` 아래에 두 함수를 둡니다.
  - `excludes_new_students`는 `if not c.excludes_new_students: return None`이고, `p.completed_semesters >= 1`이면 충족입니다.
  - `min_remaining_semesters`는 `None`이면 비교하지 않고, `REGULAR_SEMESTERS - p.completed_semesters >= c.min_remaining_semesters`면 충족입니다.
  - docstring에 두 가지를 적습니다. 편입생은 아니라고 간주한다는 것(2026-11-12)과, 지금 다니는 학기도 남은 학기로 센다는 것입니다.
- `engine.py`: `RULES`에 `_no_date(...)`로 추가합니다. 모듈 docstring의 조건 설명은 그대로 둡니다.
- `scholarships.py`: `ConditionName`에 두 값을 추가합니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/matching tests/api -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/matching app/schemas/api/scholarships.py tests/matching/test_engine.py
git commit -m "feat(matching): 신·편입생 제외와 남은 정규학기 조건을 이수학기로 판정"
```

커밋 본문에 frontend 영향을 적습니다. `ConditionName`에 값 2개가 늘어서 `npm run gen:api`로 타입을 다시 만들고 체크리스트 라벨을 추가해야 합니다.

---

### Task 3: 추출 프롬프트 v6

**Files:**
- Create: `app/extraction/prompts/extract_v6.md` (`extract_v5.md` 복사 후 수정)
- Modify: `app/extraction/extractor.py` (`PROMPT_VERSION = "extract_v6"`. `_mock_result`는 새 필드 기본값이 `None`이라 고치지 않음)
- Modify: `app/extraction/schemas.py:110-118` (세 목록 주석을 v6 기준으로)
- Test: `tests/extraction/test_extractor.py`, `tests/extraction/test_schemas.py:224`, `tests/scheduler/test_jobs.py:215`

**Interfaces:**
- Consumes: Task 1 필드 이름
- Produces: `PROMPT_VERSION == "extract_v6"`, `PROMPT_PATH`가 `extract_v6.md`를 가리킴

- [ ] **Step 1: 테스트 수정**
  - `"+extract_v5"`를 기대하는 세 곳을 `"+extract_v6"`로 바꿉니다: `test_extractor.py:126`, `test_schemas.py:224`, `test_jobs.py:215`.
  - `test_prompt_contains_required_rules`의 목록에 아래 문자열을 추가합니다.
    - `"`excludes_new_students`"`, `"`min_remaining_semesters`"`
    - `"충북대학교"` (학교 정보, Case 1-c)
    - `"세 목록 어디에도 다시 넣지 않는다"` (필드로 옮긴 문장)
    - `"문장 전체"` (섞인 문장)
    - `"수혜·선발 이력"` (Case 3)

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction tests/scheduler/test_jobs.py -q`
Expected: 버전 기대값과 프롬프트 규칙 테스트가 FAIL

- [ ] **Step 3: `extract_v6.md` 작성** (스펙 3.1·4.3)

v5를 복사한 뒤 아래만 고칩니다. 나머지 절(0, 1, 2의 1~6·8, 3)은 그대로 둡니다.

- **2장 규칙 7**을 분류표대로 다시 씁니다.
  - `extra_conditions`: 그 밖의 자격, 외부 기준 참조(누리집·홈페이지·교내 관리지침), 수혜·선발·신청 이력(있어야 하든 있으면 제외든: 중복 수혜·기존 장학생·수혜 횟수·다른 장학금 신청 요구), 섞인 문장.
  - `basic_conditions`: 학교 정보로 알 수 있는 조건과 징계·품행 조건만 둡니다.
  - `notes`: 선발 방식·우대·안내, 선발 뒤 의무·계속 지원 요건, 그 장학금의 신청·추천 절차, 예외·완화 규정.
  - 예시는 스펙 3.1 표의 문장을 씁니다.
  - v5 `basic_conditions` 예시에서 중복 수혜·수혜 횟수·기존 장학생을 지웁니다. v5 `notes` 예시에서 "관리지침·규정을 따른다는 문장"을 지웁니다. v5 `extra_conditions` 예시에서 "신·편입생 제외"와 "잔여 정규학기 수"를 지웁니다.
- **규칙 7 아래에 경계 원칙 4개**를 그대로 옮깁니다(스펙 3.1 "경계에 있는 문장"). 아래 두 문장은 Step 1 테스트 문자열과 같아야 합니다.
  - 필드로 옮긴 문장은 세 목록 어디에도 다시 넣지 않는다.
  - 남는 조건이 있으면 **문장 전체**를 원문 그대로 `extra_conditions`에 넣는다.
- **서비스 대상 문단**을 0장 앞(첫 문단 뒤)에 추가합니다. 사용자는 모두 충북대학교(국립 4년제, 충청북도 청주시 소재 지방 대학) 소프트웨어학부(4년제 학과) 학부 재학생입니다. 학교 정보로 충족 여부를 알 수 있는 조건은 `basic_conditions`에 넣습니다.
- **3장 필드별 규칙**에 두 필드를 `excludes_extra_semester` 뒤에 추가합니다.
  - `excludes_new_students`: "신·편입생 제외", "편입생은 제외", "편입학 예정자 지원 불가" → `true`. 공지에 없으면 `null`이고 `false`는 쓰지 않습니다.
  - `min_remaining_semesters`: "잔여정규학기 N개 학기 이상" → `N`.
- **`target_grades` 항목**에 한 줄을 추가합니다. "N학년 M학기 진학 예정자", "K학기 진학 예정자"는 이 필드로 옮기고 `extra_conditions`에 넣지 않는다는 내용입니다.

- [ ] **Step 4: 구현과 통과 확인**

`PROMPT_VERSION`을 올리고, `schemas.py`의 세 목록 주석을 v6 기준으로 고칩니다. 수혜 이력과 외부 기준 참조가 `extra_conditions`로 옮겨졌다는 것도 주석에 적습니다.

Run: `uv run pytest tests/extraction tests/scheduler -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/extraction tests/extraction tests/scheduler/test_jobs.py
git commit -m "feat(extraction): 조건 문장 분류 기준을 고친 추출 프롬프트 v6"
```

---

### Task 4: 골든셋 채점에 새 필드 보조 지표

**Files:**
- Modify: `eval/scoring.py` (`AUX_FIELDS`, `score_notice`, 모듈 docstring)
- Test: `tests/eval/test_scoring.py`

**Interfaces:**
- Produces: `AUX_FIELDS = ("needs_check", "excludes_new_students", "min_remaining_semesters")`. `eval/run.py`는 `AUX_FIELDS`를 순회해 출력하므로 수정하지 않습니다.

- [ ] **Step 1: 실패하는 테스트 작성** (`test_needs_check_is_aux_and_not_in_total` 옆, 기존 `BASE`·`criteria()` 사용)

```python
def test_new_semester_fields_are_aux_and_not_in_total():
    want = {**BASE, "excludes_new_students": True, "min_remaining_semesters": 1}
    got = {**BASE, "excludes_new_students": None, "min_remaining_semesters": 1}
    score = score_notice("n1", criteria(scholarships=[want]), criteria(scholarships=[got]))
    summary = summarize([score])
    assert summary["fields"]["excludes_new_students"]["correct"] == 0
    assert summary["fields"]["min_remaining_semesters"] == {
        "correct": 1,
        "total": 1,
        "accuracy": 1.0,
    }
    assert summary["correct"] == 6 and summary["total"] == 6
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/eval/test_scoring.py -q`
Expected: 새 테스트 FAIL(KeyError)

- [ ] **Step 3: 구현**

`score_notice`의 장학금 루프에서 `needs_check` 옆에 두 보조 필드를 `_check(score, name, …, want, got)`로 비교합니다. 짝이 없는 장학금은 `SCHOLARSHIP_FIELDS`처럼 틀린 것으로 셉니다. docstring에도 "extract_v6 보조 지표"를 적습니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/eval -q`
Expected: 모두 PASS. `test_golden_set_expected_answers_score_perfectly`도 PASS(정답끼리 비교)

- [ ] **Step 5: 커밋**

```bash
git add eval/scoring.py tests/eval/test_scoring.py
git commit -m "feat(eval): 신·편입생 제외·남은 정규학기를 골든셋 보조 지표로 채점"
```

---

### Task 5: 골든셋 정답을 v6 기준으로 (사람 검토 필요)

**Files:**
- Modify: `tests/fixtures/golden/*.expected.json` (장학금이 있는 공지만)
- Modify: `tests/fixtures/golden/README.md` (라벨링 규칙 3번)
- Test: `tests/eval/test_scoring.py::test_golden_set_expected_answers_score_perfectly`

**Interfaces:**
- Consumes: Task 1 스키마(정답 파일은 `ExtractedCriteria`로 검증됨)

- [ ] **Step 1: 정답 고치기**

장학금마다 세 목록과 새 필드를 스펙 3.1 표대로 고칩니다. v5 골든셋 결과에서 확인한 대상은 아래와 같습니다. 정답 파일의 실제 문장을 열어 맞춥니다.
- **새 필드**:
  - `sw_dept_1154730` 두 장학금: `excludes_new_students: true`, `min_remaining_semesters: 1`
  - `sw_dept_1154615`, `sw_dept_1154905`: `excludes_new_students: true`
  - 해당 문장은 세 목록에서 뺍니다.
- **basic → extra (수혜 이력)**:
  - 1154342 "상반기 장학생 제외", 1154346 "인재육성 장학금 수혜자 제외", 1154414 "기존 선발 학생 지원 불가"
  - 1154590 "최대 2회", 1154665 "타 장학금과 중복 수혜 불가", 1154730 "1개 기부자 유형만"·"중복 지원 불가"
- **notes → extra (외부 기준 참조)**: 1154016 두 장학금의 "「충북대학교 등록금재원 장학금 관리지침」을 따름"
- **extra → notes**:
  - 1154665 "수여식…", "사전 테스트 면접…", "선발 후 편입하는 경우…"
  - 1154905 "군필자…"
  - kosaf_21427 "각 사업계획서를 반드시 확인 후 신청"
  - 1153811 "신청서 작성, 온라인 사전교육…"
- **extra → basic**: 1154730 지역 1차 산업 "지방 소재…", 1154665 "5년제·6년제 학과…"
- **extra에서 빼기 (필드로 옮겨짐)**: 1153906 "2학년 1학기 진학 예정자", 1154665 "3학기 진학 예정자"
- **basic → notes**: 1154905 "대학 총장의 추천을 받은 자"
- **검토가 필요한 문장**: 1154665 "선발 후 2027년 1학기, 2학기 모두 재학이 가능한 자(…1년 이내 휴학 확인 시 지원 중단 및 자격 상실)". 2026-11-11 검토에서는 `extra_conditions`였습니다. 새 분류표는 "1년 이내 휴학 시 지원 중단"을 Case 2로 둡니다. 정답에서는 `extra_conditions`로 두고 검토자에게 묻습니다.

- [ ] **Step 2: 정답 형식 확인**

Run: `uv run pytest tests/eval/test_scoring.py::test_golden_set_expected_answers_score_perfectly -q`
Expected: PASS

- [ ] **Step 3: README 갱신**

라벨링 규칙 3번에 "2026-11-12 `extract_v6` 기준"을 추가합니다. 분류표 요약(스펙 3.1), 바꾼 공지 목록, 검토 대기 표시를 적습니다.

- [ ] **Step 4: 커밋 후 사용자에게 검토 요청**

```bash
git add tests/fixtures/golden
git commit -m "docs(golden): 조건 문장 분류 정답을 extract_v6 기준으로 고침 (검토 대기)"
```

**사용자 검토 전에는 Task 6을 시작하지 않습니다.** 바꾼 문장 목록과 Step 1의 검토 문장을 보여 주고 확인을 받습니다. 검토 결과에 따라 정답을 고친 뒤, README의 검토 대기 표시를 "검토 완료(날짜·검토자)"로 바꿉니다.

---

### Task 6: 실제 Gemini 골든셋 평가 (사람 할 일 포함)

**Files:**
- Create: `eval/results/gemini-3.8-flash+extract_v6.json` (`eval.run`이 만듦)

- [ ] **Step 1: 환경 확인**

`.env`에 `GEMINI_API_KEY`·`GEMINI_MODEL`이 없습니다. 지금은 SMTP 값만 있습니다. 사용자에게 키를 넣어 달라고 요청합니다. 키 값은 묻지도 출력하지도 않습니다. `AI_MOCK`이 켜져 있으면 `eval.run`이 거부합니다.

- [ ] **Step 2: 실행**

Run: `uv run python -m eval.run`
Expected: 표 출력과 결과 파일 생성. 아래를 확인합니다.
- 전체 정답률 ≥ 90%. v5는 6개 필드 145/145였습니다.
- `needs_check (보조)`: 새 정답 기준으로 v5(22/23)와 비교합니다.
- 새 보조 필드 2개의 정답률

- [ ] **Step 3: 결과 보고와 커밋**

틀린 항목(`mismatches`)을 사용자에게 보여 줍니다. 아래 두 경우에는 프롬프트를 고치고 다시 평가합니다.
- `needs_check`가 v5보다 떨어진 경우
- Case 3 문장이 `notes`·`basic`으로 빠진 경우. 지원 가능을 잘못 늘리는 방향이라 위험합니다.

```bash
git add eval/results/gemini-3.8-flash+extract_v6.json
git commit -m "chore(eval): 골든셋 extract_v6 측정 결과 (gemini-3.8-flash)"
```

---

### Task 7: 계획서 반영과 최종 확인

**Files:**
- Modify: `../docs/장학금_매칭_서비스_계획서.md` 5.2 표·5.3(판정 2단계와 조건별 비교 표)·4.1(편입 간주 한 줄). 작업 공간 문서라 git 밖입니다.
- Modify: 작업 공간 `docs/조건문장_분류표.md` (상단 "확정 전 초안" → "확정 (2026-11-12, extract_v6)")

- [ ] **Step 1: 계획서 고치기**
  - 5.2 표:
    - `target_grades…` 행에 `excludes_new_students`, `min_remaining_semesters`를 추가합니다.
    - `extra_conditions` 행을 세 목록 설명으로 바꿉니다(스펙 3.1 요약).
    - 빠져 있던 `min_score_100`, `grade_basis`, `evidence_source`를 추가합니다.
  - 5.3:
    - 판정 2단계 문장에 "`basic_conditions`·`notes`는 판정에 영향 없음"을 추가합니다.
    - 조건별 비교 표에 "신·편입생 제외", "남은 정규학기" 행을 추가합니다(Global Constraints의 규칙).
  - 4.1: "편입 여부는 받지 않고 편입생이 아니라고 간주(2026-11-12)"를 추가합니다.

- [ ] **Step 2: 전체 확인**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: 린트 통과, 모든 테스트 PASS. DB 테스트는 compose의 postgres가 필요합니다. 실패가 나오면 숨기지 않고 출력 그대로 보고합니다.

- [ ] **Step 3: 마무리 보고**

사용자에게 아래 다섯 가지를 알립니다.
- 바뀐 공용 파일: `extraction/schemas.py`와 프롬프트 버전
- API 계약 변경: `ConditionName` 2개 추가 → frontend `npm run gen:api`와 라벨 PR이 필요
- 운영 반영 방법: 관리자 재추출로 `PENDING`을 걸고, 수집이 20건씩 처리
- 3.3 예상 판정 변화와 실제 평가 결과의 차이
- push·PR 여부는 사용자에게 묻기
