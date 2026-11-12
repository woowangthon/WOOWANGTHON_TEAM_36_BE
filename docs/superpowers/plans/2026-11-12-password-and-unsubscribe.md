# 비밀번호 규칙·변경, 알림 수신 거부 화면 구현 계획 (T37 backend, T36·T38 frontend)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비밀번호 규칙을 8~16자 영문·숫자·특수문자 조합으로 바꾸고 비밀번호 변경 API·화면을 만듭니다. 알림 메일 수신 토글과 로그인 없는 수신 거부 화면도 만듭니다.

**Architecture:**
- backend는 공용 검증 함수 하나를 가입과 변경에서 쓰고, `PUT /auth/me/password`가 `token_version`을 올려 다른 기기의 세션을 끊습니다.
- frontend는 카드마다 `main`에서 브랜치를 땁니다. T36과 T38이 모두 내 정보 화면(`ProfilePage`)을 고치므로, 서로 충돌하지 않게 새 기능은 별도 컴포넌트 파일로 만들고 `ProfilePage`에는 한 줄씩만 넣습니다.

**Tech Stack:** FastAPI·Pydantic v2·pytest / React·React Hook Form·Zod·TanStack Query·MSW·Vitest

**Spec:** `docs/superpowers/specs/2026-11-12-account-profile-additions-design.md` 3.1, 4.1, 4.2절

## Global Constraints
- 비밀번호·해시·토큰·쿠키는 로그에 남기지 않습니다. 사용자는 `user_id`로만 기록합니다.
- 비밀번호 규칙
  - 길이 8~16자
  - 모든 문자가 ASCII 영문, 숫자, `string.punctuation` 중 하나
  - 영문, 숫자, 특수문자가 각각 1자 이상
  - 가입과 변경에만 적용합니다. 로그인은 기존대로 1~128자만 확인합니다.
- 오류 문장(backend와 frontend가 같음)
  - "비밀번호는 8~16자로 입력하세요"
  - "영문을 1자 이상 넣어 주세요"
  - "숫자를 1자 이상 넣어 주세요"
  - "특수문자를 1자 이상 넣어 주세요"
  - "영문·숫자·특수문자만 쓸 수 있어요"
- 비밀번호 변경 API: `PUT /auth/me/password`. 본문은 `{current_password, new_password}`이고 응답은 `204`와 새 쿠키입니다.

  | 상황 | 응답과 문장 |
  | --- | --- |
  | 비밀번호 없는 계정 | `400` "Google 계정으로 로그인하는 계정이에요" |
  | 현재 비밀번호 틀림 | `400` "현재 비밀번호가 맞지 않아요" |
  | 새 비밀번호가 현재와 같음 | `400` "지금과 다른 비밀번호를 입력하세요" (스펙에 더한 결정) |
  | 시도 초과 | `429` "비밀번호 변경을 너무 많이 시도했어요. 잠시 후 다시 시도하세요", `user_id` 기준 분당 5회 |

- frontend는 상대 경로 `/api/...`만 쓰고, 타입은 `schema.gen.ts`만 씁니다. 360px 폭에서도 깨지지 않아야 합니다.
- 수신 거부 화면은 열기만 해서는 끄지 않습니다. 버튼을 눌러야 `POST`를 보냅니다.
- 문서·주석·커밋은 한국어로 쓰고, 커밋 제목은 카드 ID로 시작합니다.

## Review Focus
1. **새 규칙 전에 가입한 짧거나 단순한 비밀번호**: 로그인은 계속 되어야 합니다. Task 1의 `test_login_still_accepts_old_style_password`가 확인합니다.
2. **비밀번호를 바꾼 직후 같은 기기**: 새 쿠키로 로그인 상태가 유지되고, 옛 쿠키는 401이어야 합니다. Task 2가 확인합니다.
3. **수신 거부 링크에 토큰이 없거나 잘못된 경우**: 버튼을 누르기 전에는 요청하지 않고, 결과에 맞는 문장을 보여 줘야 합니다. Task 4가 확인합니다.
4. **알림 토글 실패**: 화면에 먼저 반영한 값을 되돌려야 합니다. Task 3이 확인합니다.
5. **Google 전용 계정의 내 정보 화면**: 비밀번호 변경 버튼 대신 안내 문장이 보여야 합니다. Task 6이 확인합니다.

---

## backend (`feat/T37-password-policy`)

### Task 1: 비밀번호 규칙
**Files:** Modify `app/schemas/api/auth.py`. Test `tests/api/test_auth.py`(가입 테스트 상수 `PASSWORD`를 새 규칙에 맞는 `"password12!"`로 바꾸고, 다른 테스트 파일에 같은 상수가 있으면 함께 바꿈)
**Produces:** `validate_new_password(v: str) -> str`, `PASSWORD_MIN = 8`, `PASSWORD_MAX = 16`

- [ ] RED: `test_signup_password_rules`를 매개변수 테스트로 씁니다. 각 값이 그 문장으로 422인지 확인합니다.

  | 값 | 결과 |
  | --- | --- |
  | `"abc12!"` | 길이 문장 |
  | `"abcdefgh12345678!"` | 길이 문장 |
  | `"abcdefgh"` | 숫자 문장 |
  | `"12345678!"` | 영문 문장 |
  | `"abcd1234"` | 특수문자 문장 |
  | `"abcd 12!"` | 허용 문자 문장 |
  | `"비밀번호12!a"` | 허용 문자 문장 |
  | `"abcd123!"` | 통과(8자) |
  | `"Abcdefgh1234567!"` | 통과(16자) |

  `test_login_still_accepts_old_style_password`도 씁니다. DB에 `hash_password("password123")`로 만든 계정은 로그인이 200이어야 합니다.
- [ ] `uv run pytest tests/api/test_auth.py -q`로 실패를 확인합니다.
- [ ] GREEN: `SignupRequest.password`에 `field_validator`로 `validate_new_password`를 붙입니다. 검사 순서는 길이 → 허용 문자 → 영문 → 숫자 → 특수문자입니다.
- [ ] 전체 테스트를 통과시킨 뒤 커밋합니다. 메시지: `T37: 가입 비밀번호 규칙 (8~16자, 영문·숫자·특수문자)`

### Task 2: 비밀번호 변경 API
**Files:** Modify `app/schemas/api/auth.py`(`PasswordChangeRequest`), `app/core/security.py`(`password_change_limiter`, `check_password_change_rate(user_id: int)`), `app/api/auth.py`. Test `tests/api/test_auth.py`(`reset_limiters`에 새 제한기 추가)

- [ ] RED
  - `test_change_password_success`: 204가 오고, 새 쿠키로 `/auth/me`가 200, 옛 쿠키로는 401, 새 비밀번호로 로그인 200, 옛 비밀번호로는 401입니다. DB `token_version`이 1 늘어납니다.
  - `test_change_password_wrong_current`: 400과 위 문장. `token_version`은 그대로입니다.
  - `test_change_password_google_only`: `password_hash=None`인 계정은 400과 위 문장입니다.
  - `test_change_password_same_as_current`: 400과 위 문장입니다.
  - `test_change_password_rule_violation`: `new_password="short"`는 422입니다.
  - `test_change_password_rate_limit`: 틀린 현재 비밀번호로 5번 400이 나온 뒤 6번째는 429입니다.
  - `test_change_password_requires_login`: 401입니다.
- [ ] 실패를 확인합니다.
- [ ] GREEN
  - `PasswordChangeRequest(current_password: str 1~128, new_password: 새 규칙)`, `extra="forbid"`
  - 제한기는 `WindowRateLimiter({"user": (5, 60.0)}, 위 문장)`
  - 라우트 `@router.put("/me/password", status_code=204, responses={**UNAUTHORIZED, 400, 422, 429})`. 처리 순서는 제한 → 비밀번호 없는 계정 → 현재 비밀번호 확인 → 같은 비밀번호 → 해시 저장, `token_version += 1`, `commit` → `issue_session` → `log_event("비밀번호 변경", user_id)`입니다.
- [ ] 전체 테스트와 ruff를 통과시킵니다. 계획서 7.2에 API 행을, 7.3에 규칙과 변경 문장을 추가합니다(작업 공간 문서). 그다음 커밋합니다. 메시지: `T37: 비밀번호 변경 API`

## frontend T36 (`feat/T36-notification-settings`, `main`에서)

### Task 3: 알림 수신 토글
**Files:**
- Create: `src/components/profile/NotificationToggle.tsx`, `src/components/profile/NotificationToggle.test.tsx`
- Modify: `src/api/hooks/auth.ts`(`useSetNotifications`), `src/mocks/handlers.ts`(`PUT /api/auth/me/notifications`), `src/pages/ProfilePage.tsx`(계정 영역에 `<NotificationToggle me={me} />` 한 줄)

- [ ] RED
  - 스위치(`role="switch"`, 이름 "장학금 알림 메일 받기")가 `me.email_notifications`를 보여 줍니다.
  - 누르면 바로 꺼짐으로 보이고 PUT 본문은 `{email_notifications: false}`입니다.
  - 서버가 500이면 다시 켜짐으로 돌아가고 오류 문장을 보여 줍니다.
- [ ] GREEN
  - `useSetNotifications`는 `onMutate`에서 `meKey` 데이터를 바꾸고(optimistic), `onError`에서 되돌리고, `onSettled`에서 `meKey`만 무효화합니다.
  - 스위치는 `button role="switch" aria-checked`로 만듭니다. 설명 문장은 "새 맞춤 장학금과 관심 장학금 마감 3일·1일 전에 하루 한 번 메일로 알려 드려요"입니다.
- [ ] 테스트 통과 후 커밋합니다. 메시지: `T36: 내 정보 화면 알림 메일 수신 토글`

### Task 4: 수신 거부 화면과 문구
**Files:**
- Create: `src/pages/UnsubscribePage.tsx`, `src/pages/UnsubscribePage.test.tsx`
- Modify: `src/routes.tsx`(`/unsubscribe`, 가드 밖), `src/api/hooks/auth.ts`(`useUnsubscribe`), `src/mocks/handlers.ts`(`POST /api/notifications/unsubscribe`: 토큰 `mock-unsub`이면 204, 그 밖은 400 "유효하지 않은 링크입니다"), `src/pages/ConsentPage.tsx`, `src/pages/PrivacyPage.tsx`

- [ ] RED
  - `/unsubscribe?token=mock-unsub`: 화면을 열었을 때 요청이 0건이고, "알림 메일 끄기" 버튼을 누르면 POST 1건과 "알림 메일을 껐어요" 문장이 보입니다.
  - 잘못된 토큰이면 "유효하지 않은 링크입니다"가 보입니다.
  - 토큰이 없으면 버튼 없이 "링크가 올바르지 않아요. 메일의 링크를 다시 눌러 주세요"가 보입니다.
  - 로그인하지 않아도 열립니다.
  - 동의 화면과 `/privacy`에 "장학금 알림 메일" 문구가 보입니다.
- [ ] GREEN
  - `AuthLayout`을 씁니다. 성공 문장 아래에 "다시 받으려면 로그인 후 내 정보에서 켤 수 있어요"를 넣습니다.
  - 문구
    - 동의 화면 목적: "… 가입 인증번호 발송과 장학금 알림 메일(새 맞춤 장학금·관심 장학금 마감 안내) 발송에도 사용"
    - `/privacy`: "이메일 주소는 로그인, 가입 인증번호, 장학금 알림 메일 발송에 사용합니다. 알림은 메일의 수신 거부 링크나 내 정보 화면에서 끌 수 있습니다. 그 밖의 광고 메일은 보내지 않습니다."
- [ ] 전체 테스트·eslint·tsc를 통과시킨 뒤 커밋하고 PR을 올립니다. 메시지: `T36: 수신 거부 화면과 알림 메일 안내 문구`

## frontend T38 (`feat/T38-password-ui`, `main`에서, T37 OpenAPI로 `gen:api`)

### Task 5: 비밀번호 규칙·입력 칸·안내
**Files:**
- Create: `src/lib/password.ts`, `src/lib/password.test.ts`, `src/components/form/PasswordInput.tsx`, `src/components/form/PasswordRules.tsx`
- Modify: `src/api/schema.gen.ts`(생성), `src/pages/LoginPage.tsx`, `src/pages/SignupPage.tsx`, `src/pages/AuthFlow.test.tsx`(가입 비밀번호를 `password12!`로 바꾸고 규칙 테스트 추가)

**Produces:**
- `PASSWORD_RULES: {key: 'length'|'letter'|'digit'|'special', label: string, test(v): boolean}[]`. 라벨은 "8~16자", "영문 포함", "숫자 포함", "특수문자 포함"입니다.
- `newPasswordSchema`(Zod, 위 문장들)
- `PasswordInput`(Input props + 보기 버튼)
- `PasswordRules({value})`

- [ ] RED
  - `password.test.ts`: Task 1 표와 같은 값과 같은 문장을 검사합니다.
  - `AuthFlow.test.tsx`
    - 가입 화면에서 `abcd1234`를 입력하면 "특수문자를 1자 이상 넣어 주세요"가 보입니다.
    - 안내 목록에서 충족한 항목에 `data-met="true"`가 붙습니다.
    - 보기 버튼(`aria-label` "비밀번호 보기")을 누르면 `type`이 `text`가 되고 `aria-pressed="true"`가 됩니다.
    - 로그인 화면에도 보기 버튼이 있습니다.
- [ ] GREEN: `SignupPage`의 비밀번호 Zod를 `newPasswordSchema`로 바꾸고, 두 칸을 `PasswordInput`으로 바꾸고, 첫 칸 아래에 `PasswordRules`를 넣습니다. `LoginPage`도 `PasswordInput`으로 바꿉니다.
- [ ] 테스트 통과 후 커밋합니다. 메시지: `T38: 비밀번호 형식 안내와 보기 버튼 (API 타입 다시 생성)`

### Task 6: 비밀번호 변경 폼
**Files:**
- Create: `src/components/profile/PasswordChange.tsx`, `src/components/profile/PasswordChange.test.tsx`
- Modify: `src/api/hooks/auth.ts`(`useChangePassword`), `src/mocks/handlers.ts`(`PUT /api/auth/me/password`: 현재 비밀번호가 `MOCK_PASSWORD`가 아니면 400 "현재 비밀번호가 맞지 않아요", 맞으면 204), `src/pages/ProfilePage.tsx`(계정 영역에 `<PasswordChange me={me} />` 한 줄)

- [ ] RED
  - `has_password=false`면 "Google 계정으로 로그인하는 계정이에요"만 보이고 버튼이 없습니다.
  - "비밀번호 변경" 버튼을 누르면 칸 3개가 열립니다.
  - 새 비밀번호와 확인이 다르면 "비밀번호가 서로 달라요"가 보입니다.
  - 현재 비밀번호가 틀리면(400) 그 칸 아래에 문장이 보입니다.
  - 429면 "잠시 후 다시 시도" 문장이 보입니다.
  - 성공하면 "비밀번호를 바꿨어요. 다른 기기에서는 다시 로그인해야 해요"가 보이고 폼이 닫힙니다.
- [ ] GREEN
  - React Hook Form과 Zod를 씁니다. 칸은 현재 비밀번호(1자 이상), 새 비밀번호(`newPasswordSchema`), 확인입니다.
  - `useChangePassword`는 성공하면 `meKey`만 무효화합니다.
  - 프로필 폼과 다른 별도 `<form>`입니다.
- [ ] 전체 테스트·eslint·tsc를 통과시킨 뒤 커밋하고 PR을 올립니다(backend T37 PR과 서로 링크). 메시지: `T38: 내 정보 화면 비밀번호 변경`

## 스펙에 더한 결정
- 새 비밀번호가 현재 비밀번호와 같으면 400으로 막습니다.
- T36과 T38은 같은 화면을 고치므로 새 기능을 각자 별도 컴포넌트 파일로 만듭니다(브랜치 충돌 방지).
