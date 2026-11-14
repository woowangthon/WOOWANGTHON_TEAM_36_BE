# T51 이수 학기 범위 조건 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "4∼5개 학기를 이수한 학생"처럼 지금까지 마친 학기 수로 대상을 한정하는 조건을 직접 답하는 조건 대신 규칙 엔진이 판정하게 합니다.

**Architecture:** 추출 필드 두 개(`min_completed_semesters`, `max_completed_semesters`)를 추가하고, 규칙 엔진이 판정용 이수학기와 비교해 체크리스트 한 행(`min_completed_semesters`)으로 보여 줍니다. 프롬프트는 `extract_v13`을 복사한 `extract_v14`에 새 규칙 한 묶음만 덧붙입니다. 프롬프트가 꼬이는지 볼 수 있게 실제 공지(한국장학재단 20757)를 골든셋 31번째로 넣고, 프롬프트를 바꾸기 전에 v13 기준선을 먼저 잽니다.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ruff. 새 의존성 없음.

**Spec:** `../docs/장학금_매칭_서비스_계획서.md` 5.2(`min_completed_semesters`·`max_completed_semesters` 행), 5.3("이수 학기" 행), 10장(골든셋 31건). 카드는 `../docs/작업카드.md` T51, 분류는 `../docs/조건문장_분류표.md` Case 1-a.

## Global Constraints

- 브랜치는 `feat/T51-completed-semesters-range`(최신 `main`에서 땀). 커밋 제목은 `T51:`로 시작하고 한국어로 씁니다. `main`에 직접 push하지 않습니다.
- 카드의 "건드릴 파일" 밖은 고치지 않습니다: `app/extraction/schemas.py`, `app/extraction/extractor.py`, `app/extraction/prompts/extract_v14.md`, `app/matching/rules.py`, `app/matching/engine.py`, `app/matching/describe.py`, `app/schemas/api/scholarships.py`, `eval/scoring.py`, `eval/run.py`(주석의 건수), `tests/*`, `CLAUDE.md`(골든셋 건수).
- 숫자 비교는 규칙 엔진 코드로만 합니다. `app/matching/`은 DB·HTTP·LLM에 의존하지 않는 순수 함수입니다.
- `app/extraction/`은 사용자·프로필 관련 모듈을 import하지 않습니다. 이수학기 상한 16은 `app/schemas/api/profile.py`의 `MAX_SEMESTERS`를 import하지 않고 숫자로 적습니다.
- 새 필드의 기본값은 `None`입니다. DB에 저장된 v13 이전 결과가 그대로 읽혀야 하고, 마이그레이션은 없습니다(조건은 JSON 저장).
- 프롬프트 v14는 v13 파일을 복사한 뒤 **새 줄만 추가**합니다. v13에 있던 줄은 한 글자도 고치지 않습니다. 줄 끝은 v13과 같은 CRLF입니다.
- 테스트는 Gemini와 실제 사이트를 호출하지 않습니다. Gemini 실제 호출은 `uv run python -m eval.run`에서만 합니다.
- 포맷은 바꾼 `.py` 파일에만 `uv run ruff format <파일>`을 돌립니다. 완료 기준은 `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`입니다.
- 만들지 않는 것: 입학 예정자 전용 표시, 부모 원거리 거주 판정(카드 T51 "만들지 않는 것").

## Review Focus

1. 범위가 한쪽만 있는 공지("4개 학기 이상 이수") → `null`인 쪽은 비교하지 않고 열린 범위로 판정합니다. (Task 2)
2. 편입생 → 편입 후 마친 학기에 4를 더한 판정용 이수학기로 비교합니다. 편입 첫 학기(0)는 4~5 범위에서 충족입니다. (Task 2)
3. 두 칸 중 하나만 `ambiguous_fields`에 있음 → 행 전체가 알 수 없음이고 요약 문구를 붙이지 않습니다. 값이 있는 쪽만으로 "4학기 이상 이수"라고 보여 주면 안 됩니다. (Task 2)
4. 추출값이 뒤집힘(최소 6, 최대 5) → 모두에게 조용히 지원 불가가 되지 않게 스키마 검증에서 실패시킵니다. (Task 1)
5. 같은 장학금에 "3학년 재학"과 "4∼5개 학기 이수"가 함께 있음 → 학년 행과 이수 학기 행이 둘 다 나오고, 2학년(이수 3)은 두 행 모두 불충족입니다. (Task 2)

---

### Task 1: 추출 스키마에 이수 학기 범위 필드 추가

**Files:**
- Modify: `app/extraction/schemas.py` (`CriteriaField`, `ScholarshipCriteria`, `_check_values`)
- Test: `tests/extraction/test_schemas.py`

**Interfaces:**
- Produces: `ScholarshipCriteria.min_completed_semesters: int | None`, `ScholarshipCriteria.max_completed_semesters: int | None` (둘 다 0~16, 기본값 `None`). `CriteriaField`에 같은 이름 두 개가 있어 `ambiguous_fields`·`evidence`의 키로 쓸 수 있습니다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/extraction/test_schemas.py` 끝에 추가)

```python
def test_v13_criteria_without_completed_semesters_load():
    """extract_v14 이전에 저장된 조건(새 키 없음)도 읽혀야 합니다."""
    s = ScholarshipCriteria.model_validate({"scholarship_name": "v13 장학금"})
    assert s.min_completed_semesters is None
    assert s.max_completed_semesters is None


@pytest.mark.parametrize(
    "fields",
    [
        {"min_completed_semesters": 4, "max_completed_semesters": 5},
        {"min_completed_semesters": 4},
        {"max_completed_semesters": 5},
        {"min_completed_semesters": 4, "max_completed_semesters": 4},
        {"min_completed_semesters": 0, "max_completed_semesters": 16},
    ],
)
def test_completed_semesters_accepts_range(fields):
    s = ExtractedCriteria.model_validate(_example(**fields)).scholarships[0]
    assert s.min_completed_semesters == fields.get("min_completed_semesters")
    assert s.max_completed_semesters == fields.get("max_completed_semesters")


@pytest.mark.parametrize(
    "fields",
    [
        {"min_completed_semesters": -1},
        {"max_completed_semesters": 17},
        {"min_completed_semesters": 6, "max_completed_semesters": 5},
    ],
)
def test_completed_semesters_rejects_bad_range(fields):
    with pytest.raises(ValidationError):
        ExtractedCriteria.model_validate(_example(**fields))


def test_completed_semesters_in_criteria_field_and_json_schema():
    schema = ExtractedCriteria.model_json_schema()
    for name in ("min_completed_semesters", "max_completed_semesters"):
        assert name in CRITERIA_FIELDS
        assert name in schema["$defs"]["ScholarshipCriteria"]["properties"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction/test_schemas.py -k completed_semesters -v`
Expected: FAIL (`extra_forbidden` 또는 `AttributeError`)

- [ ] **Step 3: 스키마 구현**

`CriteriaField`에는 `"min_remaining_semesters"` 바로 뒤에 두 이름을 넣습니다. `ScholarshipCriteria`에는 `min_remaining_semesters` 필드 바로 뒤에 `Field(default=None, ge=0, le=16)`인 두 필드를 넣고, 주석에 "extract_v14에서 추가(T51), 지금까지 마친 정규학기 수의 범위, 한쪽만 있으면 다른 쪽은 None, 학년으로 바꾸지 않고 원문 숫자 그대로"를 적습니다. `_check_values`에는 둘 다 값이 있고 최소가 최대보다 크면 `ValueError("min_completed_semesters는 max_completed_semesters보다 클 수 없습니다")`를 던지는 검사를 추가합니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/extraction -v`
Expected: PASS (기존 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
uv run ruff format app/extraction/schemas.py tests/extraction/test_schemas.py
git add app/extraction/schemas.py tests/extraction/test_schemas.py
git commit -m "T51: 추출 스키마에 이수 학기 범위 필드 추가"
```

---

### Task 2: 규칙 엔진·요약 문구·API 조건 이름

`describe.py`의 분류 테스트(`DESCRIBED | NAME_ONLY == ConditionName`) 때문에 조건 이름, 규칙, 요약 문구는 한 번에 바뀌어야 합니다.

**Files:**
- Modify: `app/schemas/api/scholarships.py` (`ConditionName`)
- Modify: `app/matching/rules.py`, `app/matching/engine.py`, `app/matching/describe.py`
- Test: `tests/matching/test_engine.py`, `tests/matching/test_describe.py`

**Interfaces:**
- Consumes: Task 1의 두 필드.
- Produces:
  - `ConditionName`에 `"min_completed_semesters"` (API `ConditionCheck.condition` 값, frontend T52가 라벨 "이수 학기"로 씀)
  - `rules.completed_semesters(p: Profile, c: ScholarshipCriteria, *, ambiguous: bool = False) -> Outcome`
  - 체크리스트 순서: `min_remaining_semesters` 바로 다음, `enrollment_status` 앞
  - `describe("min_completed_semesters", c)` 문구: `"4~5학기 이수"`, `"4학기 이수"`(최소 = 최대), `"4학기 이상 이수"`, `"5학기 이하 이수"`

- [ ] **Step 1: 실패하는 엔진 테스트 작성** (`tests/matching/test_engine.py`, "입학 학년도" 절 뒤에 새 절로 추가)

```python
# --- 이수 학기 범위 (extract_v14, T51) -------------------------------------------------


@pytest.mark.parametrize(
    ("minimum", "maximum", "p", "expected"),
    [
        (4, 5, {"completed_semesters": 3}, "UNMET"),
        (4, 5, {"completed_semesters": 4}, "MET"),
        (4, 5, {"completed_semesters": 5}, "MET"),
        (4, 5, {"completed_semesters": 6}, "UNMET"),
        (4, None, {"completed_semesters": 16}, "MET"),  # 최대가 없으면 위로 열린 범위
        (4, None, {"completed_semesters": 3}, "UNMET"),
        (None, 5, {"completed_semesters": 1}, "MET"),  # 최소가 없으면 아래로 열린 범위
        (None, 5, {"completed_semesters": 6}, "UNMET"),
        (4, 5, {"is_transfer": True, "completed_semesters": 0}, "MET"),  # 판정용 4
        (4, 5, {"is_transfer": True, "completed_semesters": 2}, "UNMET"),  # 판정용 6
    ],
)
def test_completed_semesters_range(minimum, maximum, p, expected):
    c = criteria(min_completed_semesters=minimum, max_completed_semesters=maximum)
    assert outcome("min_completed_semesters", profile(**p), c) == expected


def test_completed_semesters_absent_is_not_compared():
    assert outcome("min_completed_semesters", profile(), criteria()) is None


@pytest.mark.parametrize("field", ["min_completed_semesters", "max_completed_semesters"])
def test_completed_semesters_either_ambiguous_is_unknown(field):
    # 모호한 쪽은 null이고 다른 쪽에 값이 있어도 행 전체가 알 수 없음입니다.
    other = {"min_completed_semesters": 4, "max_completed_semesters": 5}
    del other[field]
    c = criteria(**other, ambiguous_fields=[field])
    check = next(
        check
        for check in evaluate(profile(), c, TODAY).checks
        if check.condition == "min_completed_semesters"
    )
    assert check.outcome == "UNKNOWN"
    assert check.ambiguous is True
    assert check.requirement is None


def test_completed_semesters_evidence_falls_back_to_max_key():
    c = criteria(
        max_completed_semesters=5,
        evidence={"max_completed_semesters": "5개 학기 이하를 이수한 학생"},
    )
    check = evaluate(profile(), c, TODAY).checks[0]
    assert check.condition == "min_completed_semesters"
    assert check.evidence == "5개 학기 이하를 이수한 학생"


def test_completed_semesters_follows_remaining_in_checklist():
    c = criteria(
        min_remaining_semesters=1,
        min_completed_semesters=4,
        max_completed_semesters=5,
        enrollment_status=["재학"],
    )
    order = [check.condition for check in evaluate(profile(), c, TODAY).checks]
    assert order == ["min_remaining_semesters", "min_completed_semesters", "enrollment_status"]


def test_grade_and_completed_semesters_are_separate_rows():
    """국가우수 2년지원: "3학년 재학" + "4∼5개 학기 이수". 2학년은 두 행 모두 불충족입니다."""
    c = criteria(target_grades=[3], min_completed_semesters=4, max_completed_semesters=5)
    evaluation = evaluate(profile(completed_semesters=3), c, TODAY)
    outcomes = {check.condition: check.outcome for check in evaluation.checks}
    assert outcomes == {"target_grades": "UNMET", "min_completed_semesters": "UNMET"}
    assert evaluation.result == "INELIGIBLE"
    assert evaluate(profile(completed_semesters=4), c, TODAY).result == "ELIGIBLE"
```

- [ ] **Step 2: 실패하는 요약 문구 테스트 작성** (`tests/matching/test_describe.py`)

`test_every_condition_is_classified`의 `len(DESCRIBED) == 14`를 `15`로 바꾸고, `test_describe`의 parametrize 목록에 아래 네 줄을 추가합니다.

```python
CASES = [
    (
        "min_completed_semesters",
        {"min_completed_semesters": 4, "max_completed_semesters": 5},
        "4~5학기 이수",
    ),
    (
        "min_completed_semesters",
        {"min_completed_semesters": 4, "max_completed_semesters": 4},
        "4학기 이수",
    ),
    ("min_completed_semesters", {"min_completed_semesters": 4}, "4학기 이상 이수"),
    ("min_completed_semesters", {"max_completed_semesters": 5}, "5학기 이하 이수"),
]
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/matching -k "completed_semesters or classified or describe" -v`
Expected: FAIL (조건 이름 없음, `len(DESCRIBED)` 불일치)

- [ ] **Step 4: 구현**

- `app/schemas/api/scholarships.py`: `ConditionName`에서 `"min_remaining_semesters"` 뒤에 `"min_completed_semesters",  # 이수 학기 범위 (extract_v14). max_completed_semesters와 한 행`을 넣습니다.
- `app/matching/rules.py`: `min_remaining_semesters` 뒤에 `completed_semesters(p, c, *, ambiguous=False)`를 추가합니다. `ambiguous`면 `UNKNOWN`, 두 칸이 모두 `None`이면 `None`, 그 밖에는 `effective_semesters(p)`가 값이 있는 쪽의 경계 안에 있으면 `MET`, 아니면 `UNMET`입니다. docstring에 "마친 학기만 세고 지금 다니는 학기는 세지 않는다"를 적습니다.
- `app/matching/engine.py`: `_regions`와 같은 방식의 래퍼 `_completed_semesters(p, c)`가 두 필드 이름 중 하나라도 `c.ambiguous_fields`에 있으면 `ambiguous=True`로 넘깁니다. `RULES`에서 `min_remaining_semesters` 다음에 `("min_completed_semesters", _no_date(_completed_semesters))`를 넣고, `EVIDENCE_KEYS`에 `"min_completed_semesters": ("min_completed_semesters", "max_completed_semesters")`를 넣습니다. 모듈 docstring에 한 줄을 더합니다.
- `app/matching/describe.py`: `_completed_semesters(c)`를 `_DESCRIBERS["min_completed_semesters"]`로 등록하고("요약을 붙이는 14개" 주석은 15개로), `_own_field_ambiguous`가 `condition == "min_completed_semesters"`일 때 `max_completed_semesters`가 모호해도 참을 돌려주게 합니다.

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/matching tests/api -v`
Expected: PASS (API 스키마를 쓰는 테스트 포함)

- [ ] **Step 6: 커밋**

```bash
uv run ruff format app/matching app/schemas/api/scholarships.py tests/matching
git add app/matching app/schemas/api/scholarships.py tests/matching
git commit -m "T51: 이수 학기 범위를 규칙 엔진이 한 행으로 판정"
```

---

### Task 3: 골든셋 31번째 공지와 보조 지표, v13 기준선

프롬프트를 바꾸기 **전에** 표본과 채점을 넣어, v13이 이 공지에서 무엇을 틀리는지 먼저 기록합니다.

**Files:**
- Create: `tests/fixtures/golden/kosaf_20757.input.json`, `tests/fixtures/golden/kosaf_20757.expected.json`
- Modify: `eval/scoring.py` (`AUX_VALUE_FIELDS`, 모듈 docstring), `eval/run.py`(docstring의 "30건"), `tests/fixtures/golden/README.md`, `CLAUDE.md`(구조 절의 "공지 30건")
- Test: `tests/eval/test_scoring.py`
- Create(사람 또는 승인 뒤 실행): `eval/results/gemini-4-hydrogen+extract_v13.json`

**Interfaces:**
- Consumes: Task 1의 두 필드.
- Produces: `eval.scoring.AUX_VALUE_FIELDS`에 `"min_completed_semesters"`, `"max_completed_semesters"`. 전체 정답률(`correct`·`total`)에는 넣지 않습니다.

- [ ] **Step 1: 입력 파일 넣기**

2026-11-14에 프로젝트 크롤러(`KosafCrawler.fetch_detail("20757")` + `read_attachments` + `extraction_text`)로 받아 둔 파일을 그대로 복사합니다. 사이트에 다시 요청하지 않습니다.

```bash
cp "C:/Users/nicej/AppData/Local/Temp/claude/f0aeb25eb991579b/328e3d91-9879-42d3-a1eb-d52e7673ebdb/scratchpad/golden/kosaf_20757.input.json" tests/fixtures/golden/
```

제목 "2026년 국가우수장학금(이공계) 업무처리기준 안내", 게시일 2026-04-07, 본문 + HWPX 첨부 2개, `text` 85,969자입니다.

- [ ] **Step 2: 정답 초안 작성** (`kosaf_20757.expected.json`)

`tests/fixtures/golden/README.md`의 라벨링 규칙대로 원문을 읽고 만듭니다. `notice_kind`는 `기준안내`이고 장학금은 아래 4개입니다. 표에 없는 필드는 원문에 조건이 없으면 `null`·빈 목록입니다. 채운 필드마다 `evidence`에 근거 문장을, `evidence_source`에 첨부 파일 이름을 넣습니다.

| 장학금 (원문 유형 이름) | 확정한 값 | 근거 문장 |
| --- | --- | --- |
| 성적우수 유형(1·2차는 대상·기준이 같아 하나) | `min_admission_year` 2026, `target_grades` null, `min_gpa` null. "고교 성적 기준 또는 '26년도 수능성적 기준 충족자"는 `extra_conditions` 한 항목 | "'26년도 국내 4년제 대학교 자연과학·공학계열 학과(부)에 입학한 신입생으로서…" |
| 재학 중 우수자 2년 지원 유형 | `target_grades` [3], `min_completed_semesters` 4, `max_completed_semesters` 5, `min_gpa` 3.5, `min_score_100` 87, `grade_basis` 전체, `grade_either` true. "졸업 이수학점 기준의 40% 이상", "선발 이력이 없으며"는 `extra_conditions` | "3학년 재학 중인 자", "4년제 학과의 경우 4∼5개 학기를 이수한 학생을 대상으로 선발", "총평균 백분위 87점 이상 또는 평점 3.5이상/4.5만점 기준" |
| 과학기술전문사관 후보생 지원 유형 | 숫자 조건 없음. "과학기술전문사관 후보생으로 선발되어 자격을 유지하는 자"는 `extra_conditions` | 같은 문장 |
| 재학 중 우수자 일시 지원 유형 | `target_grades` [3], `enrollment_status` ["재학"], `min_gpa` 3.5, `min_score_100` 87, `grade_basis` 직전학기, `grade_either` true, `min_credits` 12, 이수 학기 두 칸 null | "3학년 재학 중인 자", "선발 당시 재학생(휴학생 제외)", "직전학기 백분위 87점 이상 또는 3.5이상/4.5만점 기준", "직전학기 12학점 이상" |

네 장학금 공통: `apply_end` null(원문에 날짜 없음), `max_income_bracket` null, `target_nationality` domestic("대한민국 국적을 소지한 자"), `target_department` `소프트웨어학부 포함`(자연과학·공학계열 한정), `target_program` `제한없음`.

`ExtractedCriteria.model_validate(...)`를 통과한 값을 `model_dump(mode="json")`으로 저장합니다(기존 정답 파일과 같은 형식).

- [ ] **Step 3: 실패하는 채점 테스트 작성** (`tests/eval/test_scoring.py`)

`test_golden_set_expected_answers_score_perfectly`의 `summary["notices"] == 30`을 `31`로 바꾸고 아래를 추가합니다.

```python
def test_completed_semesters_fields_are_aux_and_not_in_total():
    """이수 학기 범위는 보조 지표로만 셉니다(extract_v14)."""
    want = {**BASE, "min_completed_semesters": 4, "max_completed_semesters": 5}
    got = {**BASE, "min_completed_semesters": 4, "max_completed_semesters": None}
    score = score_notice("n1", criteria(scholarships=[want]), criteria(scholarships=[got]))
    summary = summarize([score])
    assert summary["fields"]["min_completed_semesters"]["correct"] == 1
    assert summary["fields"]["max_completed_semesters"]["correct"] == 0
    assert summary["correct"] == 6 and summary["total"] == 6


def test_golden_20757_pins_semester_range_and_grade():
    """회귀 표본: 2년 지원 유형은 학년 [3]과 이수 학기 4~5가 함께 정답입니다."""
    expected = ExtractedCriteria.model_validate_json(
        (run.GOLDEN_DIR / "kosaf_20757.expected.json").read_text(encoding="utf-8")
    )
    ranged = [s for s in expected.scholarships if s.min_completed_semesters is not None]
    assert len(expected.scholarships) == 4
    assert len(ranged) == 1
    assert (ranged[0].min_completed_semesters, ranged[0].max_completed_semesters) == (4, 5)
    assert ranged[0].target_grades == [3]
    assert ranged[0].grade_either is True
```

- [ ] **Step 4: 실패 확인**

Run: `uv run pytest tests/eval -v`
Expected: 새 테스트 두 개 중 보조 지표 테스트가 `KeyError: 'min_completed_semesters'`로 FAIL

- [ ] **Step 5: 채점·문서 반영**

- `eval/scoring.py`: `AUX_VALUE_FIELDS` 끝에 두 이름을 추가하고, 모듈 docstring에 "보조 지표 `min_completed_semesters`·`max_completed_semesters`(extract_v14): 위와 같습니다. 해당 공지는 `kosaf_20757` 1건입니다." 한 줄을 더합니다.
- `eval/run.py` docstring의 "30건 전체" → "31건 전체".
- `CLAUDE.md` 구조 절의 "golden/(공지 30건 + 정답 JSON)" → "31건".
- `tests/fixtures/golden/README.md`: 첫 줄 "공지 31건", 구성 표의 KOSAF를 11건(기준안내 2)으로 고치고, "애매했던 판단" 표에 `kosaf_20757` 행을 추가합니다. 적을 내용: 2026-11-14 T51에서 회귀 표본으로 추가, 장학금을 4개로 나눈 이유(유형마다 대상·기준이 다름, 성적우수 1·2차는 같아서 하나), 4년제·5년제 값이 따로 있어 4년제 값을 씀, "직전 4∼5개 학기"는 성적 산정 기간이라 조건이 아님, 정답은 Claude 초안이고 사람 검토 대기.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/eval tests/extraction -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
uv run ruff format eval/scoring.py eval/run.py tests/eval/test_scoring.py
git add eval tests/eval tests/fixtures/golden CLAUDE.md
git commit -m "T51: 골든셋에 국가우수장학금 업무처리기준(20757)을 31번째로 추가"
```

- [ ] **Step 8: v13 기준선 측정** (Gemini 실제 호출, 사람 또는 승인 뒤 실행)

Run: `uv run python -m eval.run`
Expected: `eval/results/gemini-4-hydrogen+extract_v13.json` 생성. 기존 30건의 주요 필드는 v11 수준(145/145 안팎)이고, `kosaf_20757`에서 `min_completed_semesters`·`max_completed_semesters`가 틀리는 것이 기준선입니다(v13 프롬프트는 이 필드를 모름). 장학금 개수가 정답 4개와 다르게 나오면 정답을 고치지 말고 결과를 사용자에게 보고합니다.

```bash
git add eval/results
git commit -m "T51: extract_v13 기준선 평가 결과 (31건)"
```

---

### Task 4: 프롬프트 extract_v14

**Files:**
- Create: `app/extraction/prompts/extract_v14.md` (v13 복사 + 새 줄)
- Modify: `app/extraction/extractor.py` (`PROMPT_VERSION`)
- Test: `tests/extraction/test_extractor.py`, `tests/extraction/test_schemas.py`, `tests/scheduler/test_jobs.py`
- Create(사람 또는 승인 뒤 실행): `eval/results/gemini-4-hydrogen+extract_v14.json`

**Interfaces:**
- Consumes: Task 1의 필드 이름, Task 3의 표본.
- Produces: `PROMPT_VERSION = "extract_v14"`. `model_version`은 `"{GEMINI_MODEL}+extract_v14"`, 목 응답은 `"mock+extract_v14"`.

- [ ] **Step 1: 실패하는 테스트 작성**

버전 문자열을 고칩니다: `tests/extraction/test_extractor.py`의 `"gemini-test-flash+extract_v13"`·`"mock+extract_v13"`, `tests/extraction/test_schemas.py`의 `"mock+extract_v13"`, `tests/scheduler/test_jobs.py`의 `"mock+extract_v13"`을 모두 `v14`로.

`tests/extraction/test_extractor.py`에서 프롬프트에 꼭 있어야 하는 문구 목록(`"`grade_either`",  # 새 필드(extract_v12)` 다음)에 아래 두 줄을 넣고, 파일 끝에 테스트 두 개를 추가합니다.

```python
NEW_RULES = (
    "`min_completed_semesters`",  # 새 필드(extract_v14)
    "`max_completed_semesters`",
)


def test_completed_semesters_rule_keeps_numbers_as_written():
    """이수 학기 수는 원문 숫자 그대로 두고 학년으로 바꾸지 않습니다(extract_v14, T51)."""
    line = _prompt_line("- `min_completed_semesters`")
    assert "4∼5개 학기를 이수한 학생" in line
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "학년으로 바꿔 `target_grades`를 채우지 않는다" in prompt
    assert '"N학기 진학 예정자"는 이 필드가 아니라 `target_grades`다' in prompt
    assert "4년제 값을 쓴다" in prompt
    assert "성적 산정 기간" in prompt


def test_v14_only_adds_lines_to_v13():
    """v14는 v13에 줄을 더하기만 합니다. 있던 규칙을 고치면 이 테스트가 실패합니다."""
    v13 = (PROMPT_PATH.parent / "extract_v13.md").read_text(encoding="utf-8").splitlines()
    v14 = PROMPT_PATH.read_text(encoding="utf-8").splitlines()
    remaining = iter(v14)
    assert all(line in remaining for line in v13)
    assert len(v14) - len(v13) == 5
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction tests/scheduler/test_jobs.py -v`
Expected: FAIL (버전 문자열, 새 문구 없음)

- [ ] **Step 3: 프롬프트와 버전 반영**

`extract_v13.md`를 `extract_v14.md`로 복사하고 `PROMPT_VERSION = "extract_v14"`로 바꿉니다. v14에는 "3. 필드별 규칙"의 `- `min_remaining_semesters`: …` 줄 **바로 뒤에** 아래 다섯 줄만 넣습니다(문구 그대로).

```text
- `min_completed_semesters`, `max_completed_semesters`: 대상을 **지금까지 마친 정규학기 수**로 한정하면 그 범위(0~16). "4∼5개 학기를 이수한 학생"은 `4`, `5`. "4개 학기 이상 이수한 자"는 `4`, `null`. "6개 학기 이하 이수자"는 `null`, `6`.
  - 공지에 적힌 학기 수를 그대로 옮긴다. 학년으로 바꿔 `target_grades`를 채우지 않는다(같은 장학금에 "3학년 재학"이 따로 적혀 있으면 그 문장만 `target_grades`에 넣는다). 이 문장은 `extra_conditions`에 넣지 않는다.
  - "N학기 진학 예정자"는 이 필드가 아니라 `target_grades`다. "이수 예정"처럼 앞으로 마칠 학기까지 세는 문장은 두 필드를 `null`로 두고 둘 다 ambiguous에 넣는다.
  - 학제별로 따로 적혀 있으면("4년제 학과는 4∼5개 학기, 5년제 학과는 5∼6개 학기") 4년제 값을 쓴다.
  - 지원 횟수·지원 기간("최대 4학기 지원"), 성적 산정 기간("직전 4∼5개 학기 성적"), 남은 학기(`min_remaining_semesters`)는 이 필드가 아니다. 공지에 이 조건이 없으면 `null`이다. 근거 문장을 `evidence`에 넣는다.
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/extraction tests/scheduler/test_jobs.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format app/extraction/extractor.py tests/extraction tests/scheduler/test_jobs.py
git add app/extraction tests/extraction tests/scheduler/test_jobs.py
git commit -m "T51: 이수 학기 범위 추출 규칙 추가 (extract_v14)"
```

- [ ] **Step 6: v14 평가와 기준선 비교** (Gemini 실제 호출, 사람 또는 승인 뒤 실행)

Run: `uv run python -m eval.run`
Expected: `eval/results/gemini-4-hydrogen+extract_v14.json` 생성, 전체 정답률 90% 이상. v13 결과와 공지별로 비교해 아래 세 가지를 보고합니다.

1. `kosaf_20757`에서 `min_completed_semesters` 4, `max_completed_semesters` 5가 맞는지, 2년 지원의 `target_grades`가 `[3]`으로 남는지
2. 기존 30건에서 v13보다 틀린 주요 필드가 생겼는지(꼬임 여부)
3. 기존 30건에서 새 두 필드를 `null`이 아닌 값으로 잘못 채운 공지가 있는지

회귀가 있으면 프롬프트를 고치기 전에 사용자에게 먼저 알립니다.

```bash
git add eval/results
git commit -m "T51: extract_v14 평가 결과 (31건)"
```

---

### Task 5: 전체 확인과 카드 마무리

**Files:**
- Modify: `../docs/작업카드.md` (T51 상태), `../docs/claude/backend.CLAUDE.md`(골든셋 건수, 저장소 `CLAUDE.md`의 초안 사본)

- [ ] **Step 1: 완료 기준 실행**

Run: `uv run pytest`
Expected: 전부 PASS (DB가 필요한 테스트는 compose의 postgres가 떠 있어야 합니다. 떠 있지 않아 건너뛰거나 실패한 테스트는 숨기지 않고 그대로 보고합니다.)

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: 오류 없음

- [ ] **Step 2: 카드 상태와 문서 사본 갱신**

`../docs/작업카드.md`의 `### T51 … · [ ] 대기`를 `[x] 완료`로 바꾸고, `../docs/claude/backend.CLAUDE.md`의 "공지 30건"을 "31건"으로 맞춥니다(이 폴더는 git 저장소가 아닙니다).

- [ ] **Step 3: 사용자에게 넘길 것 정리**

PR 본문에 적을 내용: API 변경은 `ConditionName`에 `min_completed_semesters` 추가 한 건이고 frontend T52(`npm run gen:api` + 라벨 "이수 학기")가 필요합니다. 배포 뒤 사람이 할 일은 201·202·219·221 관리자 재추출과 판정 확인, 골든셋 `kosaf_20757` 정답 검토입니다. push와 PR 생성은 사용자가 요청하면 합니다.
