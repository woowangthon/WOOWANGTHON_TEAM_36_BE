# 계정·프로필 추가 기능 설계 (T36~T40)

- 작성일: 2026-11-12
- 관련 계획서: 4.1, 5.2, 5.3, 7.1, 7.2, 7.3, 7.4
- 관련 카드: T36(기존), T37~T40(신규)

## 1. 목적과 결정 사항

사용자 요청으로 다음 다섯 기능을 추가합니다.

| 기능 | 결정 |
| --- | --- |
| 메일 수신 거부 | 기존 카드 T36 그대로. backend API(`PUT /auth/me/notifications`, `POST /notifications/unsubscribe`)는 T35에서 완료 |
| 비밀번호 형식 | **8~16자, 영문(대소문자 무관)·숫자·ASCII 특수문자를 각각 1자 이상**. 공백·한글 등 그 밖의 문자는 거부. 가입과 변경에서만 검사하고 로그인은 기존대로(1~128자) |
| 비밀번호 형식 보기 | 새 비밀번호 칸 아래 형식 안내(충족 항목에 ✓) + 모든 비밀번호 칸에 보기/숨기기 버튼 |
| 비밀번호 변경 | 내 정보 화면에서 현재 비밀번호 확인 후 변경. 비밀번호가 없는 계정(`has_password = false`)은 메뉴를 숨김(7.3 "Google 로그인 전용" 유지) |
| 편입생 여부 | 계획서 4.1의 "편입 여부는 받지 않습니다"(2026-11-12) 결정을 **뒤집음**. 체크박스로 받고, 편입생의 이수학기는 "편입 후 마친 학기"로 입력받아 판정에는 **4학기(3학년 편입)를 더한 값**을 씀. "신·편입생 제외" 조건은 이 학교에서 마친 학기로 판정 |
| 외국인 여부 | 예/아니오 하나. 공지의 국적 조건(`target_nationality`)과 비교해 판정 |

## 2. 작업 나누기

| 순서 | 카드 | 저장소 | 내용 | 선행 |
| --- | --- | --- | --- | --- |
| 1 | T36 | frontend | 알림 수신 토글, `/unsubscribe` 화면, 동의·개인정보처리방침 문구 | T35(완료) |
| 2 | T37 | backend | 비밀번호 규칙, `PUT /auth/me/password` | — |
| 3 | T38 | frontend | 비밀번호 형식 안내, 보기 버튼, 변경 폼 | T37 |
| 4 | T39 | backend | 편입생·외국인 프로필 필드, 규칙 엔진, `extract_v7` 국적 필드, 골든셋 라벨 | — |
| 5 | T40 | frontend | 편입생·외국인 체크박스, 상세 화면 조건 이름 | T39 |

계획서 수정은 각 backend 카드가 자기 부분을 맡습니다. T37: 7.2·7.3, T39: 4.1·5.2·5.3과 `조건문장_분류표.md`.

## 3. backend

### 3.1 T37 비밀번호 규칙·변경

**규칙** (`app/schemas/api/auth.py`)
- 공용 검증 함수 `validate_new_password(v: str) -> str` 하나를 `SignupRequest.password`와 `PasswordChangeRequest.new_password`에서 씁니다.
- 통과 조건: 길이 8~16, 모든 문자가 ASCII 영문·숫자·특수문자(`string.punctuation`) 중 하나, 세 종류가 각각 1자 이상.
- 오류 메시지는 어긴 규칙을 한국어로 알려 줍니다(예: "특수문자를 1자 이상 넣어 주세요").
- `LoginRequest.password`는 지금처럼 `min_length=1, max_length=128`입니다. 새 규칙 전에 가입한 비밀번호로도 로그인할 수 있게 하기 위해서입니다.

**API** `PUT /auth/me/password`
- 요청 `PasswordChangeRequest { current_password: str (1~128자), new_password: str (새 규칙) }`
- 처리 순서
  1. 로그인 확인(기존 `current_user` 의존성)
  2. 횟수 제한: `user_id` 기준 분당 5회(로그인 제한과 같은 구조로 함수 추가)
  3. `password_hash`가 없으면 `400` "Google 계정으로 로그인하는 계정이에요"
  4. 현재 비밀번호가 틀리면 `400` "현재 비밀번호가 맞지 않아요". 세션이 끊긴 것으로 오해하지 않도록 `401`을 쓰지 않습니다
  5. 새 해시 저장, `token_version` + 1, 현재 요청에 새 JWT 쿠키 발급, `204`
- 다른 기기의 세션은 `token_version` 비교(7.3)로 끊깁니다.
- 비밀번호·해시는 로그에 남기지 않습니다. 성공·실패는 `user_id`로만 기록합니다.

| 상황 | 응답 |
| --- | --- |
| 성공 | `204` + 새 쿠키 |
| 비로그인 | `401` |
| 비밀번호 없는 계정 | `400` |
| 현재 비밀번호 틀림 | `400` |
| 새 비밀번호 규칙 위반 | `422` |
| 시도 초과 | `429` |

### 3.2 T39 편입생·외국인

**프로필** (`Profile`, `ProfileUpdate`, `profiles` 테이블)
- `is_transfer: bool`, `is_foreign: bool`을 추가합니다. API에서는 필수이고, Alembic 마이그레이션은 `server_default=false`로 기존 행을 채웁니다. 기존 사용자의 판정은 바뀌지 않습니다.
- 평문으로 저장합니다(계획서 4.1의 암호화 대상인 소득·주소·특수자격에 들지 않음).
- `completed_semesters`의 뜻: 편입생이 아니면 마친 정규학기 수(기존), 편입생이면 **편입 후 이 학교에서 마친 학기 수**.
- 입력 범위: 편입생이 아니면 0~16(기존), 편입생이면 0~12.
- 판정용 값 `effective_semesters(p) = p.completed_semesters + (4 if p.is_transfer else 0)`을 `app/schemas/api/profile.py`에 둡니다.
- 이수학기 0이면 성적을 비우는 기존 규칙은 `completed_semesters` 기준을 유지합니다. 편입 첫 학기에도 이 학교 성적이 없기 때문입니다.

**규칙 엔진** (`app/matching/rules.py`, `engine.py`)

| 조건 | 규칙 |
| --- | --- |
| 학년 | `grade_of(effective_semesters)` |
| 초과학기 제외 | `is_extra_semester(effective_semesters)` |
| 남은 정규학기 | `8 − effective_semesters ≥ min_remaining_semesters` |
| 신·편입생 제외 | `completed_semesters ≥ 1`이면 충족(식은 기존과 같음). 편입생에게는 "이 학교에서 1학기 이상"이 되어 편입 첫 학기는 불충족 |
| 국적 (신규) | `target_nationality`가 `null`이면 비교 안 함. `"domestic"`이면 `not is_foreign`, `"foreign"`이면 `is_foreign` |

**추출** (`extract_v7`)
- `ExtractedCriteria`에 `target_nationality: Literal["domestic", "foreign"] | None = None`을 추가합니다. 기본값이 `None`이라 DB의 v6 결과도 그대로 읽힙니다.
- 프롬프트 `extract_v7.md`
  - "대한민국 국적자", "대한민국 국적의 … 재학생" → `target_nationality = "domestic"`. 같은 문장의 학교 조건 부분은 계속 `basic_conditions`에 남깁니다.
  - "외국인 유학생", "외국 국적" 대상 → `"foreign"`.
  - "이중국적자 제외", "영주권자 제외", "재외국민" 같은 세부 조건 → `extra_conditions`(확인 필요). 국적 필드는 위 기준대로 따로 채웁니다.
- `PROMPT_VERSION = "extract_v7"`, 계획서 5.2 표와 `조건문장_분류표.md` Case 1-c 예시를 고칩니다.
- 골든셋: 국적 문장이 있는 공지의 `*.expected.json`에 `target_nationality`를 넣고, 채점(`eval/scoring.py`)에 이 필드를 추가합니다.
- **사람**: `uv run python -m eval.run`은 Gemini를 호출하므로 사용자가 `GEMINI_API_KEY`로 실행하고 v6 점수와 비교합니다.
- 이미 저장된 공지는 재추출 전까지 `target_nationality = null`이라 국적 조건이 판정에 반영되지 않습니다. 반영하려면 관리자 재추출 또는 백필(`--backfill`)을 실행합니다.

## 4. frontend

공통: 모든 요청은 상대 경로 `/api/...`이고, 타입은 `npm run gen:api`로 만든 `schema.gen.ts`만 씁니다. 360px 폭에서 깨지지 않게 만듭니다.

### 4.1 T36 알림 수신 설정·수신 거부
- 내 정보 화면(`ProfilePage`)의 계정 영역에 "장학금 알림 메일 받기" 스위치를 둡니다. `PUT /api/auth/me/notifications`는 optimistic update로 먼저 반영하고 실패하면 되돌리며, 끝나면 `useMe` 쿼리만 무효화합니다.
- `/unsubscribe?token=...`: 라우팅 가드 밖(로그인 불필요). 화면을 열기만 해서는 끄지 않고, "알림 메일 끄기" 버튼을 눌러야 `POST /api/notifications/unsubscribe`를 보냅니다. 결과는 성공, 잘못된 링크, 토큰 없음 세 가지로 표시합니다.
- 동의 화면과 `/privacy` 문구에 "이메일을 장학금 알림 발송에 이용"을 추가합니다(4.2).

### 4.2 T38 비밀번호
- `src/components/form/PasswordInput.tsx`: 입력 칸 + 보기/숨기기 버튼(`aria-label` "비밀번호 보기"/"비밀번호 숨기기", `aria-pressed`). 로그인·가입·변경의 모든 비밀번호 칸에 씁니다.
- `src/lib/password.ts`: backend와 같은 규칙의 Zod `passwordSchema`와 항목별 검사 함수. 가입·변경에서만 씁니다.
- `PasswordRules` 안내: 새 비밀번호 칸 아래에 "8~16자", "영문 포함", "숫자 포함", "특수문자 포함"을 표시하고, 입력하는 동안 충족한 항목에 ✓를 붙입니다.
- 비밀번호 변경: 계정 영역의 "비밀번호 변경" 버튼으로 펼치는 별도 `<form>`. 칸은 현재 비밀번호, 새 비밀번호, 새 비밀번호 확인입니다.
  - 성공: "비밀번호를 바꿨어요. 다른 기기에서는 다시 로그인해야 해요" 안내 후 폼 닫기
  - `400` 현재 비밀번호 틀림 → 현재 비밀번호 칸 오류, `429` → 잠시 후 다시 시도 안내
  - `me.has_password = false` → 버튼 대신 "Google 계정으로 로그인하는 계정이에요"

### 4.3 T40 편입생·외국인
- 기본 정보 영역
  - 이수학기 칸 위에 "편입생이에요" 체크박스. 켜면 칸 이름 "편입 후 마친 학기", 도움말 "3학년 편입 기준으로 4학기를 더해 학년을 계산해요", 범위 0~12.
  - 학년 미리보기는 4를 더한 값으로 계산합니다.
  - "외국인이에요 (대한민국 국적이 아님)" 체크박스.
  - 두 체크박스 모두 기본은 꺼져 있습니다.
- 상세 화면 조건 이름에 `target_nationality` → "국적"을 추가합니다.
- MSW 목 데이터에 국적 조건 장학금과 편입생·외국인 프로필 값을 추가합니다.

## 5. 테스트

| 카드 | 테스트 |
| --- | --- |
| T36 | 토글 실패 시 되돌리기, `/unsubscribe` 버튼을 누르기 전에는 요청 없음, 결과 3종 표시 |
| T37 | 규칙 경계값(7·8·16·17자, 종류 누락, 공백·한글), 기존 비밀번호 로그인 유지, 변경 성공 시 `token_version` 증가와 새 쿠키, 틀린 현재 비밀번호, Google 계정, 횟수 제한 |
| T38 | `passwordSchema` 단위 테스트, 보기 버튼 토글, 안내 ✓ 표시, 변경 폼 `400`·`429` 표시, `has_password = false`면 버튼 없음 |
| T39 | 편입생 학년(편입 0학기 → 3학년), 남은 정규학기, 신·편입생 제외(편입 첫 학기 불충족), 국적 3가지, 마이그레이션 기본값, v6 결과 읽기 호환 |
| T40 | 편입생 체크 시 칸 이름·범위·학년 미리보기 변경, 외국인 값 저장 |

완료 기준 명령: frontend `npm run test`, `npm run lint` / backend `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`

## 6. 저장소 간 영향
- T37: `PasswordChangeRequest`와 새 API 1개가 생기고, `SignupRequest.password` 검증이 강해집니다. frontend T38에서 `gen:api`를 실행합니다.
- T39: `Profile`·`ProfileUpdate`에 필드 2개, 장학금 판정 응답의 조건 키에 `target_nationality`가 추가됩니다. frontend T40에서 `gen:api`를 실행합니다.
- 각 backend PR과 frontend PR은 서로 링크합니다.
