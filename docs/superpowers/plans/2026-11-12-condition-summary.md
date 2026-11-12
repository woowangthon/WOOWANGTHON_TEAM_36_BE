# T43 조건 요약 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 조건마다 짧은 요약을 API로 내려 줍니다. 목록 문장은 추출 때 Gemini가(`summary`), ✓/✗ 조건은 코드가(`requirement`) 만듭니다.

**Architecture:** `ScholarshipCriteria`의 `extra_conditions`·`basic_conditions` 항목을 `ConditionText{text, summary}`로 바꿉니다. 문자열 항목도 읽히므로 DB 마이그레이션은 없습니다. 규칙 엔진은 새 순수 함수 `describe()`로 `ConditionCheck.requirement`·`ambiguous`를 채웁니다. 웹 API는 필드 추가만 합니다. 프롬프트는 `extract_v10`으로 올리고, eval 결과에 `원문 → 요약` 목록을 넣어 사람이 극성을 확인합니다.

**Tech Stack:** Python 3, Pydantic v2, FastAPI, SQLAlchemy, pytest, google-genai(Structured Output)

**Spec:** `docs/superpowers/specs/2026-11-12-condition-summary-design.md`

## Global Constraints

- 사용자 조회 때 LLM을 부르지 않습니다. `describe()`는 DB·HTTP·LLM 없는 순수 함수입니다.
- `extraction/`은 사용자·프로필·API 코드를 import하지 않습니다(import 경계 테스트 유지).
- `summary`: 공백 정리 뒤 비었거나 40자 초과면 `None`(`DISPLAY_NAME_MAX = 40` 재사용). 판정·채점에 쓰지 않습니다.
- `condition_key()`는 계속 `text`로 만듭니다.
- 웹 API 변경은 **필드 추가만** 합니다. `extra_conditions`·`basic_conditions`(`list[str]`)는 그대로 둡니다.
- `model_version` = `{GEMINI_MODEL}+extract_v10`.
- 테스트는 Gemini를 호출하지 않습니다. 실제 호출은 Task 6의 eval에서만 합니다.
- 주석·커밋 메시지는 한국어로 쓰고, 커밋 제목은 `T43:`으로 시작합니다.

## Review Focus

1. **DB에 저장된 v9 이전 문자열 목록.** 상세·목록 API가 깨지지 않고 `summary = null`로 내려가야 합니다 → Task 3 `test_detail_reads_legacy_string_conditions`.
2. **요약만 다른 재추출.** 사용자의 기존 답이 유지돼야 합니다(같은 `text` → 같은 key) → Task 3 `test_answer_kept_when_only_summary_changes`.
3. **Gemini가 `summary`를 빼고 `{text}`만 보내거나 41자를 보낼 때.** 검증 실패가 아니라 `summary = None`이어야 합니다 → Task 1.
4. **성적 기준(`grade_basis`)만 모호할 때.** 값은 있으니 `requirement = "3.5 이상"`, `ambiguous = false`, outcome은 UNKNOWN입니다(spec 3.2: 모호 표시는 그 조건 필드 자체가 `ambiguous_fields`에 있을 때만) → Task 2·3.
5. **`ConditionName`에 새 조건이 추가될 때.** 두 묶음 중 하나에 넣지 않으면 테스트가 실패해야 합니다 → Task 2 `test_every_condition_is_classified`.

---

### Task 1: 추출 스키마 `ConditionText`

**Files:**
- Modify: `app/extraction/schemas.py` (`ScholarshipCriteria`의 `extra_conditions`·`basic_conditions`, 주석 갱신)
- Test: `tests/extraction/test_schemas.py`

**Interfaces:**
- Produces: `class ConditionText(BaseModel)` (`model_config = _STRICT`, `text: str = Field(min_length=1)`, `summary: str | None = None`). `ScholarshipCriteria.extra_conditions: list[ConditionText]`, `basic_conditions: list[ConditionText]`. `notes: list[str]`는 그대로입니다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_condition_text_reads_legacy_strings():
    c = ScholarshipCriteria.model_validate(
        {
            **MINIMAL,
            "extra_conditions": ["TOEIC 500점 이상"],
            "basic_conditions": ["징계사실이 없는 자"],
        }
    )
    assert c.extra_conditions == [ConditionText(text="TOEIC 500점 이상", summary=None)]
    assert c.basic_conditions[0].text == "징계사실이 없는 자"


def test_condition_summary_cleaned_or_dropped():
    c = ScholarshipCriteria.model_validate(
        {
            **MINIMAL,
            "extra_conditions": [
                {"text": "a", "summary": "  다른  장학금을 받지 않는 학생 "},
                {"text": "b", "summary": "   "},
                {"text": "c", "summary": "가" * 41},
                {"text": "d"},
            ],
        }
    )
    assert [x.summary for x in c.extra_conditions] == [
        "다른 장학금을 받지 않는 학생",
        None,
        None,
        None,
    ]


def test_condition_text_in_gemini_schema():
    schema = json.dumps(ExtractedCriteria.model_json_schema())
    assert "ConditionText" in schema and '"summary"' in schema
```

`MINIMAL`은 이 파일에 이미 있는 최소 장학금 dict를 쓰고, 없으면 `scholarship_name`만 있는 dict를 만듭니다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction/test_schemas.py -k "condition_text or condition_summary" -v`
Expected: FAIL (`ConditionText` import 오류)

- [ ] **Step 3: 구현**

`ConditionText`에 `summary`용 `field_validator(mode="before")`를 둡니다. `_clean_display_name`과 같은 규칙(공백 합치기, 비었거나 40자 초과면 `None`)이므로, 그 정리 로직을 모듈 함수 `_clean_short_text(value: object) -> object`로 빼서 둘이 같이 씁니다. 목록 필드에는 `field_validator("extra_conditions", "basic_conditions", mode="before")`를 둬서 `str` 항목을 `{"text": item}`으로 바꿉니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/extraction -v`
Expected: 새 테스트 PASS. 문자열 비교를 하던 기존 테스트가 실패하면 `[x.text for x in ...]` 비교로 고칩니다.

- [ ] **Step 5: 커밋**

`git commit -m "T43: 조건 목록 항목을 {text, summary}로 (문자열 항목도 읽음)"`

---

### Task 2: ✓/✗ 조건 요약 `describe()`

**Files:**
- Create: `app/matching/describe.py`
- Test: `tests/matching/test_describe.py`

**Interfaces:**
- Consumes: `ScholarshipCriteria`(Task 1), `ConditionName`(`app/schemas/api/scholarships.py`)
- Produces:
  - `DESCRIBED: frozenset[ConditionName]`: 요약을 붙이는 13개
  - `NAME_ONLY: frozenset[ConditionName]`: 이름만 쓰는 6개
  - `is_ambiguous(condition: ConditionName, c: ScholarshipCriteria) -> bool`: `condition in c.ambiguous_fields` 또는 (`condition == "target_department"`이고 `c.target_department == "불명"`)
  - `describe(condition: ConditionName, c: ScholarshipCriteria) -> str | None`: `NAME_ONLY`이거나 모호하거나 값이 없으면 `None`

- [ ] **Step 1: 실패하는 테스트 작성** (spec 3.2 표의 값 그대로)

```python
def test_every_condition_is_classified():
    assert DESCRIBED | NAME_ONLY == set(get_args(ConditionName))
    assert not DESCRIBED & NAME_ONLY

@pytest.mark.parametrize(("condition", "fields", "expected"), [
    ("target_grades", {"target_grades": [2, 3]}, "2·3학년"),
    ("min_remaining_semesters", {"min_remaining_semesters": 1}, "1학기 이상"),
    ("enrollment_status", {"enrollment_status": ["재학"]}, "재학생"),
    ("enrollment_status", {"enrollment_status": ["재학", "휴학"]}, "재학생 또는 휴학생"),
    ("min_gpa", {"min_gpa": 3.5, "grade_basis": "직전학기"}, "직전학기 3.5 이상"),
    ("min_gpa", {"min_gpa": 3.5}, "3.5 이상"),
    ("min_score_100", {"min_score_100": 80, "grade_basis": "전체"}, "전체 학기 80점 이상"),
    ("min_credits", {"min_credits": 12}, "12학점 이상"),
    ("target_nationality", {"target_nationality": "domestic"}, "대한민국 국적"),
    ("target_nationality", {"target_nationality": "foreign"}, "외국 국적"),
    ("gender", {"gender": "여"}, "여학생"),
    ("max_age", {"max_age": 26}, "만 26세 이하"),
    ("max_income_bracket", {"max_income_bracket": 8}, "8구간 이하"),
    ("max_income_bracket", {"max_income_bracket": 0}, "기초생활수급자"),
    ("max_income_bracket", {"max_income_bracket": 0.5}, "차상위계층 이하"),
    ("regions", {"regions": ["충청남도 논산시"], "region_basis": "부모"}, "부모 주소 충청남도 논산시"),
    ("required_special", {"required_special": ["다자녀", "한부모"]}, "다자녀 또는 한부모"),
    ("target_department", {"target_department": "소프트웨어학부 포함"}, "소프트웨어학부 포함 학과"),
    ("target_department", {"target_department": "소프트웨어학부 불포함"}, "소프트웨어학부 외 학과"),
])
def test_describe(condition, fields, expected): ...

def test_name_only_conditions_have_no_requirement(): ...   # NAME_ONLY 전부 → None
def test_ambiguous_field_has_no_requirement():             # max_income_bracket 모호 → None, is_ambiguous True
def test_unknown_department_is_ambiguous():                # 불명 → None, is_ambiguous True
def test_grade_basis_only_ambiguous_keeps_value():         # min_gpa=3.5, ambiguous=["grade_basis"] → "3.5 이상", is_ambiguous("min_gpa") False
```

`region_basis`가 `본인`이면 "본인 주소 …", `무관`이거나 없으면 지역만 씁니다("충청남도 논산시"). 지역이 여러 개면 " 또는 "로 잇습니다. 이 경우도 parametrize에 넣습니다. 숫자는 정수면 소수점 없이(`80`, `12`), 평점은 그대로(`3.5`, `3.0` → "3.0 이상") 씁니다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/matching/test_describe.py -v`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현** `app/matching/describe.py`

조건 이름에서 `(ScholarshipCriteria) -> str | None` 함수로 가는 dict 하나로 만듭니다. `DESCRIBED`는 그 dict의 키입니다. 모듈 docstring에는 "프로필로 이미 판정하는 조건의 기준값을 보여 주는 문구, 판정에 쓰지 않음(spec 3.2)"이라고 적습니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/matching/test_describe.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

`git commit -m "T43: ✓/✗ 조건 요약 describe() (코드가 값으로 생성)"`

---

### Task 3: 엔진·웹 API에 요약 싣기

**Files:**
- Modify: `app/schemas/api/scholarships.py` (`ConditionCheck`, `ExtraCheck`, `OpenCondition`, `ScholarshipDetail`, 새 `ConditionTextOut`)
- Modify: `app/matching/engine.py` (`Evaluation`, `evaluate`)
- Modify: `app/api/scholarships.py` (`_summary`의 `open_conditions`, 상세 응답, `_condition_target`의 key 계산)
- Test: `tests/matching/test_engine.py`, `tests/api/test_scholarships.py`

**Interfaces:**
- Consumes: `describe`, `is_ambiguous`(Task 2), `ConditionText`(Task 1)
- Produces (API, frontend가 `gen:api`로 받음):
  - `ConditionCheck.requirement: str | None = None`, `ConditionCheck.ambiguous: bool = False`
  - `ExtraCheck.summary: str | None = None`, `OpenCondition.summary: str | None = None`
  - `class ConditionTextOut(BaseModel): text: str; summary: str | None`
  - `ScholarshipDetail.basic_items: list[ConditionTextOut]`
  - `Evaluation.extra_conditions: list[str]`(text만, 이름 그대로), `Evaluation.basic_conditions: list[str]`(text만), 새 `Evaluation.basic_items: list[ConditionText]`

- [ ] **Step 1: 실패하는 테스트 작성**

`test_engine.py`:
```python
def test_checks_carry_requirement_and_ambiguous():
    # min_gpa=3.5/직전학기, max_income_bracket 모호, excludes_new_students=True
    # → min_gpa.requirement == "직전학기 3.5 이상", ambiguous False
    #   max_income_bracket.requirement is None, ambiguous True
    #   excludes_new_students.requirement is None, ambiguous False

def test_extra_check_carries_summary_and_text_key():
    # extra_conditions=[{"text": "타 장학금과 중복 수혜 불가", "summary": "다른 장학금을 받지 않는 학생"}]
    # → extra_checks[0].summary == "다른 장학금을 받지 않는 학생"
    #   extra_checks[0].key == condition_key("타 장학금과 중복 수혜 불가")
    #   evaluation.extra_conditions == ["타 장학금과 중복 수혜 불가"]

def test_answer_kept_when_only_summary_changes():
    # 같은 text, summary만 다른 두 criteria에 answers={key: "YES"} → 두 결과의 extra_checks[0].answer == "YES"
```

`test_scholarships.py`:
```python
def test_detail_has_summaries(...):
    # 상세 응답: conditions[].requirement·ambiguous, extra_checks[].summary,
    #   basic_items == [{"text": ..., "summary": ...}], basic_conditions == [text]
def test_list_open_conditions_have_summary(...):  # 목록 open_conditions[].summary
def test_detail_reads_legacy_string_conditions(...):
    # DB criteria에 문자열 목록(v9 이전)을 넣은 장학금 → 200, extra_checks[].summary is None,
    #   basic_items[0] == {"text": ..., "summary": None}
```

조건 답 PUT 테스트는 객체 항목으로 저장된 장학금에서도 key를 찾아야 합니다(`_condition_target`). 기존 PUT 테스트 하나의 fixture를 객체 항목으로 바꿔 확인합니다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/matching/test_engine.py tests/api/test_scholarships.py -v`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현**

`evaluate`에서 `ConditionCheck`를 만들 때 `requirement=None if amb else describe(name, criteria)`, `ambiguous=amb`(`amb = is_ambiguous(name, criteria)`)를 넣습니다. `ExtraCheck`·`OpenCondition`에는 `item.summary`를 넣고, key는 `condition_key(item.text)`로 만듭니다. `_condition_target`은 `key_of(item.text)`로 바꿉니다. `ScholarshipDetail` 필드 주석은 spec 4.2를 따릅니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q`
Expected: 전부 PASS. `tests/notifications/test_select.py` 등에서 문자열 비교를 하던 곳이 깨지면 `.text`로 고칩니다.

- [ ] **Step 5: 커밋**

`git commit -m "T43: 엔진·API에 조건 요약 (requirement·ambiguous·summary·basic_items, 필드 추가만)"`

---

### Task 4: 프롬프트 extract_v10

**Files:**
- Create: `app/extraction/prompts/extract_v10.md` (v9 복사 후 수정)
- Modify: `app/extraction/extractor.py` (`PROMPT_VERSION = "extract_v10"`, 목 응답의 `extra_conditions`를 `{text, summary}`로)
- Test: `tests/extraction/test_extractor.py`, 그리고 `extract_v9` 문자열을 기대하는 테스트(`tests/extraction/test_schemas.py:267`, `tests/scheduler/test_jobs.py:216`, `tests/extraction/test_extractor.py:126,341`)

**Interfaces:**
- Consumes: `ConditionText`(Task 1)
- Produces: `model_version` 끝이 `+extract_v10`

- [ ] **Step 1: 실패하는 테스트 작성**

`test_extractor.py`의 프롬프트 규칙 목록 테스트(현재 170~187행)에 아래를 추가하고, 버전 문자열 기대값을 `extract_v10`으로 바꿉니다.
```python
new_rules = [
    "`summary`",  # 조건 요약(extract_v10)
    "맞아요",  # 긍정형: "맞아요" = 충족
    "어느 목록에도 넣지 않는다",  # 같은 공지에 세부 자격이 있는 규정 출처
    "직접 답할 부분만",  # 섞인 문장 요약
]
```
그리고 다음 테스트를 추가합니다: `test_prompt_v10_drops_source_line_rule` → `"세부 자격이 같은 공지에 적힌 규정의 출처 문장: "`(v9 `basic_conditions` 예시 줄)이 v10 프롬프트에 **없음**을 확인합니다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/extraction/test_extractor.py -v`
Expected: FAIL

- [ ] **Step 3: 프롬프트 작성** (spec 5장, 3.1)

v9에서 바꾸는 곳은 다음과 같습니다.
- 2장 규칙 7 머리: 세 목록 중 `extra_conditions`·`basic_conditions`의 항목은 `{"text": 원문 그대로, "summary": 요약}`이고 `notes`는 문자열입니다.
- 규칙 7 아래에 "**요약(`summary`) 규칙**" 하위 목록을 둡니다. spec 3.1의 글머리 4개(25자 명사형 "~학생"·"~이상", "~함/없음/아님" 금지, 긍정형 "맞아요" = 충족, 섞인 문장은 직접 답할 부분만, 지어내지 않음)와 3.1 예시 표 9줄을 그대로 옮깁니다.
- `basic_conditions`의 "세부 자격이 같은 공지에 적힌 규정의 출처 문장" 줄을 지우고, 규칙 7에 "**어느 목록에도 넣지 않는다**: 세부 자격이 같은 공지에 이어 적힌 규정의 출처 문장(\"1. 기본 자격요건: 「충북대학교 등록금재원 장학금 관리지침」을 따름\")과 번호·소제목만 있는 줄" 항목을 추가합니다. 외부 기준 참조 단락의 "그 세부 자격은 필드로 옮기고 이 문장은 `basic_conditions`에 넣는다"를 "…이 문장은 어느 목록에도 넣지 않는다"로 고칩니다.
- 규칙 4(`evidence`는 원문 그대로)에 "목록 항목의 `text`도 원문 그대로"를 덧붙입니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

`git commit -m "T43: 추출 프롬프트 v10 - 조건 요약(summary)과 규정 출처 문장 제외"`

---

### Task 5: eval 결과에 요약 목록

**Files:**
- Modify: `eval/scoring.py` (새 함수), `eval/run.py` (report에 넣고 개수 출력)
- Test: `tests/eval/test_scoring.py`

**Interfaces:**
- Produces: `condition_summaries(notice_id: str, predicted: ExtractedCriteria | None) -> list[dict[str, str | None]]`. 항목 키는 `notice`, `scholarship`(`scholarship_name`), `list`(`"extra"`/`"basic"`), `text`, `summary`이고, 장학금 순서 → extra → basic 순입니다. `report["condition_summaries"]`는 공지 ID 순으로 모두 이은 목록입니다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_condition_summaries_lists_extra_then_basic():
    predicted = ExtractedCriteria.model_validate(
        {
            "notice_kind": "모집",
            "scholarships": [
                {
                    **MINIMAL,
                    "scholarship_name": "A",
                    "extra_conditions": [
                        {
                            "text": "타 장학금과 중복 수혜 불가",
                            "summary": "다른 장학금을 받지 않는 학생",
                        }
                    ],
                    "basic_conditions": ["징계사실이 없는 자"],
                }
            ],
        }
    )
    assert condition_summaries("n1", predicted) == [
        {
            "notice": "n1",
            "scholarship": "A",
            "list": "extra",
            "text": "타 장학금과 중복 수혜 불가",
            "summary": "다른 장학금을 받지 않는 학생",
        },
        {
            "notice": "n1",
            "scholarship": "A",
            "list": "basic",
            "text": "징계사실이 없는 자",
            "summary": None,
        },
    ]


def test_condition_summaries_failed_notice_is_empty():
    assert condition_summaries("n1", None) == []
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/eval/test_scoring.py -v` → FAIL

- [ ] **Step 3: 구현**

`run_one`은 이미 `predicted`를 가지고 있습니다. 거기서 `condition_summaries(notice_id, predicted)`를 계산해 run dict에 넣고, `main`이 `report["condition_summaries"]`로 모읍니다. 표 출력 뒤에 `요약 {N}개 (summary 없음 {M}개) — 결과 파일의 condition_summaries를 사람이 확인하세요`를 출력합니다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/eval -v` → PASS

- [ ] **Step 5: 커밋**

`git commit -m "T43: eval 결과에 조건 요약 목록(condition_summaries) 출력"`

---

### Task 6: 완료 확인과 실제 eval

**Files:**
- Create: `eval/results/{GEMINI_MODEL}+extract_v10.json` (eval 결과, 커밋 대상)
- Modify: 작업 공간 `docs/작업카드.md` (T43 상태. 사람 확인 뒤에)

- [ ] **Step 1: 완료 기준 명령**

Run: `uv run pytest` 그리고 `uv run ruff check . && uv run ruff format --check .`
Expected: 모두 통과. 결과를 사용자에게 그대로 보여 줍니다.

- [ ] **Step 2: 실제 Gemini로 골든셋 평가**

Run: `uv run python -m eval.run` (`AI_MOCK=0`, `.env`의 `GEMINI_API_KEY`·`GEMINI_MODEL`)
Expected: v9 결과가 없으면 v8 결과(`eval/results/gemini-3.8-flash+extract_v8.json`)와 필드 정답률·needs_check를 비교해 보고합니다. 정답률이 떨어지면 해당 필드의 mismatches를 보여 줍니다.

- [ ] **Step 3: 출처 문장 제거 확인**

결과 JSON에서 `sw_dept_1154016`의 `extra_conditions`·`basic_conditions`·`notes` 어디에도 "관리지침"이 없는지 확인합니다.

- [ ] **Step 4: 요약 목록을 사용자에게 보여 주고 극성 확인 요청**

`condition_summaries` 전체를 `원문 → 요약` 표로 보여 줍니다. 극성이 의심되는 항목(원문에 "제외·불가·없는"이 있는데 요약이 지원 가능한 학생을 가리키지 않는 것)은 표시합니다. 고칠 것이 있으면 프롬프트를 고치고 Step 2부터 다시 합니다.

- [ ] **Step 5: 결과 커밋, 카드 갱신, PR**

`git commit -m "T43: 골든셋 평가 결과 (extract_v10)"`. 사용자가 확인을 끝내면 `docs/작업카드.md`의 T43을 `[x] 완료`로 바꿉니다. PR 본문에는 API 추가 필드 목록(frontend T44 영향)과 배포 뒤 재추출 절차(spec 8장)를 적습니다. T41(#57)이 머지되기 전이면 base를 `feat/T41-display-name`으로 두거나, 머지 뒤 `main`으로 rebase한 다음 PR을 엽니다.
