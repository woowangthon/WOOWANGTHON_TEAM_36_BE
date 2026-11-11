# 장학금 이메일 알림 설계

- 작성일: 2026-11-11
- 상태: 승인 (2026-11-11). 동의 문구는 갱신만 하고 재동의 없음, 카드 번호 T35·T36, `feat/signup-email-code` 머지를 전제로 진행
- 카드(제안): T35 backend 알림 발송, T36 frontend 수신 설정·수신 거부 화면

## 1. 목적

로그인 이메일(`users.email`)로 장학금 알림을 자동으로 보냅니다. 사용자가 사이트에 들어오지 않아도 지원할 수 있는 장학금과 마감 임박을 놓치지 않게 하는 것이 목적입니다.

알림이 나가는 경우는 두 가지입니다.

1. 사용자 조건에 맞는 새 장학금이 올라왔을 때
2. 사용자의 관심(`BOOKMARKED`) 장학금 마감 3일 전과 1일 전

## 2. 결정 사항

| 항목 | 결정 |
| --- | --- |
| 발송 수단 | Gmail SMTP. 표준 라이브러리 `smtplib`만 쓰고 새 의존성은 없음 |
| 수신 대상 | 모든 가입자 (동의 완료 + 프로필 있음) |
| 수신 설정 | 기본 켬. 메일의 수신 거부 링크와 내 정보 화면 토글로 끌 수 있음 |
| 새 장학금 기준 | 규칙 엔진 판정이 지원 가능(`ELIGIBLE`) 또는 확인 필요인 장학금. 메일에 둘을 구분해 표시 |
| 발송 시점 | 하루 한 번, 서울 시각 오전 9시 이후 첫 수집 때. 사용자당 1통에 새 장학금과 마감 임박을 묶음 |
| 실행 방식 | 기존 `POST /internal/crawl` 끝에서 실행. Cloud Scheduler 작업을 추가하지 않음 |

## 3. 선행 작업과의 관계

원격 브랜치 `feat/signup-email-code`(커밋 `472679a`, 미머지)가 가입 인증번호 메일을 추가하면서 아래를 이미 만들었습니다. 이 설계는 그 브랜치가 `main`에 머지된 뒤 그 위에서 작업하는 것을 전제로 합니다.

- `app/core/mail.py`: Gmail SMTP(`smtp.gmail.com:587`, STARTTLS) 발송, `MailError`
- 환경변수 `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_MOCK`

따라서 메일 모듈과 SMTP 환경변수를 새로 만들지 않고 이것을 확장합니다. 그 브랜치가 머지되지 않거나 구조가 바뀌면 이 설계의 5.3절을 다시 맞춥니다.

가입 인증번호가 들어가면 새로 가입하는 계정은 주소 소유가 확인됩니다. 그 전에 가입한 계정은 확인되지 않은 상태로 남습니다(9장 위험 참고).

## 4. 범위

포함:

- 알림 대상 선정, 메일 작성·발송, 발송 기록
- 수신 설정 API, 수신 거부 API
- 계획서·작업카드 갱신

제외:

- 앱 푸시, 문자, 카카오 알림
- 알림 종류별 개별 설정(새 장학금만 끄기 등). 설정은 켬/끔 하나입니다.
- 발송 시각 사용자 지정
- 신청완료(`APPLIED`) 장학금의 마감 알림
- 관리자 화면의 발송 현황

## 5. 설계

### 5.1 데이터 (Alembic 마이그레이션 1개)

`users`에 칸 추가:

| 칸 | 형식 | 설명 |
| --- | --- | --- |
| `email_notifications` | bool, NOT NULL, 기본 true | 알림 메일 수신 여부 |

`scholarships`에 칸 추가:

| 칸 | 형식 | 설명 |
| --- | --- | --- |
| `created_at` | timestamptz, NOT NULL, 기본 `now()` | 장학금 행이 처음 저장된 시각 |

기존 행의 `created_at`은 마이그레이션에서 해당 공지의 `notices.posted_at`으로 채웁니다. 첫 발송 때 과거 장학금이 한꺼번에 나가지 않게 하기 위해서입니다. 재추출은 같은 이름의 장학금 id를 유지하므로(계획서 5.2) `created_at`도 바뀌지 않고, 재추출만으로 알림이 다시 나가지 않습니다.

새 테이블 `notification_logs`:

| 칸 | 형식 | 설명 |
| --- | --- | --- |
| `id` | int PK | |
| `user_id` | FK `users.id`, ON DELETE CASCADE | |
| `scholarship_id` | FK `scholarships.id`, ON DELETE CASCADE | |
| `kind` | `NEW_MATCH` / `DEADLINE_D3` / `DEADLINE_D1` | CHECK 제약 |
| `apply_end` | timestamptz, NULL 가능 | 알림을 보낼 때의 마감 시각. `NEW_MATCH`는 NULL |
| `sent_at` | timestamptz | |

- 인덱스: `(user_id, kind)`
- 같은 알림을 두 번 보내지 않기 위한 기록입니다. 마감 알림은 `apply_end`까지 같아야 "이미 보냄"으로 봅니다. 마감이 연장되면 새 마감일 기준으로 다시 알립니다.
- 탈퇴하면 CASCADE로 함께 지워집니다(계획서 4.2).

새 테이블 `notification_runs`:

| 칸 | 형식 | 설명 |
| --- | --- | --- |
| `run_date` | date PK | 서울 날짜 |
| `status` | `RUNNING` / `DONE` / `FAILED` | CHECK 제약 |
| `started_at` | timestamptz | |
| `finished_at` | timestamptz, NULL 가능 | |
| `sent_count` | int | 보낸 메일 수 |
| `failed_count` | int | 실패한 메일 수 |

### 5.2 실행 흐름

`app/api/internal.py`의 `crawl`이 `run_crawl()`을 마친 뒤 `run_daily_notifications(now)`를 부릅니다. 수집이 `skipped`로 끝나도 부릅니다.

`run_daily_notifications`:

1. 서울 시각이 9시 전이면 아무것도 하지 않습니다.
2. 한 트랜잭션에서 `pg_advisory_xact_lock`(수집과 다른 키)을 잡고 오늘 날짜의 `notification_runs` 행을 확인합니다.
   - `DONE`이면 끝냅니다.
   - `RUNNING`이고 시작한 지 1시간이 안 됐으면 끝냅니다.
   - 행이 없거나, `FAILED`이거나, 1시간 넘은 `RUNNING`이면 `RUNNING`으로 INSERT/UPDATE하고 진행합니다.
3. 대상을 고르고 발송합니다(5.3, 5.4).
4. `DONE`과 발송 수를 기록합니다. SMTP 접속 자체가 실패하면 `FAILED`로 기록하고, 다음 시각 수집이 다시 시도합니다. 성공한 메일은 이미 `notification_logs`에 있으므로 다시 시도해도 중복되지 않습니다.

알림 단계에서 난 예외는 잡아서 로그만 남기고, `/internal/crawl` 응답(`CrawlResponse`)은 바꾸지 않습니다. 알림 실패가 수집 결과를 실패로 만들지 않습니다.

로컬·수동 실행: `uv run python -m app.notifications.jobs` (`--force`를 주면 9시 조건과 오늘 실행 여부를 무시).

`BackgroundTasks`는 쓰지 않고 요청 안에서 끝까지 실행합니다(계획서 9.3).

### 5.3 대상 선정 (`app/notifications/select.py`)

수신 사용자:

- `privacy_agreed_at`이 있고, `user_profiles` 행이 있고, `email_notifications = true`

새 장학금(`NEW_MATCH`):

- `scholarships.created_at`이 실행 시각 기준 최근 48시간 이내
- 상태가 `OPEN` 또는 `ALWAYS`
- 공지 종류가 `모집` 또는 `기준안내` (대시보드 목록과 같은 기준)
- 그 사용자에게 같은 장학금의 `NEW_MATCH` 기록이 없음
- 그 사용자가 이미 `APPLIED`로 표시한 장학금이 아님
- 규칙 엔진 `evaluate` 결과가 지원 불가(`INELIGIBLE`)가 아님

48시간으로 잡는 이유는 하루 발송이 통째로 실패해도 다음 날 보내기 위해서입니다.

마감 임박(`DEADLINE_D3`, `DEADLINE_D1`):

- 그 사용자의 상태가 `BOOKMARKED`
- 장학금 상태가 `OPEN`이고 `apply_end`가 있음
- 서울 날짜 기준 `apply_end`의 날짜 − 오늘 = 3이면 `DEADLINE_D3`, 1이면 `DEADLINE_D1`
- 같은 `(사용자, 장학금, kind, apply_end)` 기록이 없음

날짜 차이는 정확히 3과 1일 때만 보냅니다. 그날 발송이 끝내 실패하면 그 알림은 건너뜁니다(3일 전을 놓쳐도 1일 전 알림은 나갑니다).

구현 메모:

- 날짜 계산은 `parsers/dates.py`의 함수만 씁니다. `datetime.now()`는 쓰지 않습니다.
- 판정에 필요한 프로필 조립(`_load_profile`)과 "못 읽은 첨부" 조회(`_unread_attachments_exist`)는 지금 `app/api/scholarships.py` 안에 있습니다. 대시보드와 알림이 같은 판정을 내도록 이 둘을 공용 함수로 꺼내 양쪽에서 씁니다. 판정 로직 자체(`app/matching/`)는 건드리지 않습니다.
- 후보 장학금은 한 번에 조회하고, 사용자마다 쿼리를 다시 날리지 않습니다(N+1 금지). 프로필·상태·발송 기록도 사용자 전체를 묶어 조회합니다.
- 사용자 조회 때 LLM을 호출하지 않습니다. `app/extraction/`은 이 모듈을 import하지 않고, 이 모듈도 `app/extraction/extractor.py`를 import하지 않습니다.
- 선정 로직은 "후보 목록 + 프로필 + 기록 → 보낼 항목"의 순수 함수로 만들어 DB 없이 테스트합니다.

### 5.4 발송 (`app/core/mail.py` 확장, `app/notifications/jobs.py`)

`app/core/mail.py`:

- 지금은 `send_signup_code`가 한 통마다 접속합니다. 접속·로그인을 한 번 하고 여러 통을 보낼 수 있는 공용 함수를 추가하고, `send_signup_code`도 그 함수를 쓰게 합니다.
- `MAIL_MOCK=1`이면 실제로 보내지 않고 성공으로 처리합니다.
- `SMTP_USER`나 `SMTP_PASSWORD`가 없으면 알림 실행을 건너뛰고 경고 로그 한 줄을 남깁니다.

메일 내용(일반 텍스트 + 간단한 HTML):

- 제목: `[장학금 매칭] 새 장학금 N건, 마감 임박 M건` (없는 쪽은 뺌)
- 본문: 구역 두 개(새로 올라온 장학금 / 마감이 다가온 관심 장학금). 항목마다 장학금 이름, 마감일(없으면 "상시"), `지원 가능` 또는 `확인 필요` 표시, 상세 화면 링크(`{APP_BASE_URL}/scholarships/{id}`)
- 하단: 수신 거부 링크, 이 메일을 받는 이유 한 줄
- 헤더: `List-Unsubscribe`
- 넣지 않는 것: 판정 근거, 프로필 값, 조건 내용. 받는 사람이 계정 주인이 아닐 수 있기 때문입니다.

발송 규칙:

- 보낼 항목이 없는 사용자에게는 보내지 않습니다.
- 한 통이 실패하면 `user_id`와 예외 종류만 로그에 남기고 다음 사용자로 넘어갑니다. 주소, 프로필 값, 메일 본문은 로그에 넣지 않습니다.
- 성공한 메일의 항목만 `notification_logs`에 기록합니다(사용자 단위로 커밋).
- 한 번 실행에 최대 450통입니다(Gmail 하루 약 500통 한도, 가입 인증번호 메일 몫을 남김). 넘으면 나머지는 보내지 않고 경고 로그를 남깁니다.

### 5.5 API

| 메서드 | 경로 | 인증 | 설명 |
| --- | --- | --- | --- |
| PUT | `/auth/me/notifications` | 로그인 | 본문 `{"email_notifications": bool}`. `MeResponse` 반환 |
| GET | `/auth/me` | 로그인 | 응답에 `email_notifications` 추가 |
| POST | `/notifications/unsubscribe` | 없음 | 본문 `{"token": str}`. 성공 204, 토큰이 틀리면 400 |

수신 거부 토큰:

- `JWT_SECRET`으로 서명하고 `user_id`와 용도(`unsubscribe`)를 담습니다. 만료는 두지 않습니다(오래된 메일의 링크도 동작해야 함).
- 로그인 쿠키용 JWT와 용도 값이 달라 서로 바꿔 쓸 수 없습니다. 이 토큰으로 할 수 있는 일은 알림 끄기뿐입니다.
- 메일의 링크는 프론트 `/unsubscribe?token=...`으로 가고, 화면의 버튼이 POST를 보냅니다. 메일 보안 검사기가 링크를 미리 열어도 설정이 바뀌지 않게 GET으로는 끄지 않습니다.
- 토큰을 로그에 남기지 않습니다.

### 5.6 환경변수

| 변수 | 설명 |
| --- | --- |
| `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_MOCK` | `feat/signup-email-code`에서 추가된 것을 그대로 사용 |
| `APP_BASE_URL` (신규) | 메일 링크의 기준 주소. 예: `https://<프로덕션>.vercel.app`. 없으면 알림 실행을 건너뜀 |

`.env.example`에는 이름만 적습니다. 배포 워크플로(`.github/workflows/deploy.yml`)에 `APP_BASE_URL`을 추가합니다.

## 6. 파일

backend (T35):

- 신규: `app/notifications/__init__.py`, `select.py`, `message.py`(제목·본문 작성), `jobs.py`
- 신규: `app/models/notification.py`, `app/api/notifications.py`, `app/schemas/api/notifications.py`
- 수정: `app/models/user.py`, `app/models/notice.py`, `app/models/__init__.py`, `app/core/mail.py`, `app/core/config.py`, `app/core/security.py`(수신 거부 토큰), `app/api/internal.py`, `app/api/auth.py`, `app/api/scholarships.py`(공용 함수로 꺼내기), `app/schemas/api/auth.py`, `app/main.py`, `.env.example`, `.github/workflows/deploy.yml`, `CLAUDE.md`
- 신규: Alembic 마이그레이션 1개, `tests/notifications/`, `tests/api/test_notifications.py`

frontend (T36, 별도 PR):

- `npm run gen:api`로 타입 재생성
- 내 정보 화면에 알림 수신 토글
- `/unsubscribe` 화면(로그인 없이 접근)

문서:

- 계획서: 2장(범위에 이메일 알림 추가), 3장(메일 발송 행), 4.2(이메일 이용 목적), 6장(새 테이블·칸), 7.2(API 3개), 7.3("메일 발송 기능이 없으므로" 문구), 9.3(수집 뒤 알림 실행), 9.6(환경변수), 9.9(Gmail 한도)
- 작업카드: T35, T36 추가

## 7. 테스트

모든 테스트는 SMTP에 실제로 접속하지 않습니다(목으로 대체).

- 선정(순수 함수)
  - 지원 가능·확인 필요는 포함, 지원 불가는 제외
  - 이미 `NEW_MATCH` 기록이 있으면 제외, 48시간보다 오래된 장학금 제외, `CLOSED` 제외, `APPLIED` 제외
  - 마감 3일 전·1일 전만 포함(2일 전, 당일, 4일 전 제외). 서울 날짜 경계(23:59 마감, 00:00 마감)
  - 마감이 연장되면 같은 `kind`라도 다시 포함
  - 수신을 끈 사용자, 동의 전 사용자, 프로필 없는 사용자 제외
- 메시지: 제목의 건수, 구역이 비면 생략, 근거·프로필 값이 본문에 없음, 수신 거부 링크 포함
- 실행
  - 9시 전에는 실행하지 않음, 같은 날 두 번째 호출은 건너뜀, `FAILED` 뒤에는 다시 시도
  - 한 통이 실패해도 나머지를 보내고, 실패한 사용자는 기록하지 않음
  - 450통 상한
  - 알림 단계의 예외가 `/internal/crawl` 응답을 실패로 만들지 않음
- API
  - 토글 PUT, `/auth/me` 응답
  - 수신 거부: 올바른 토큰 204, 변조된 토큰 400, 로그인 JWT를 넣으면 400
- 경계: `app/extraction/`이 `app/notifications`를 import하지 않음(기존 import 경계 테스트에 추가)
- 로그: 발송 실패 로그에 이메일 주소가 없음

완료 기준 명령: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `MAIL_MOCK=1 uv run python -m app.notifications.jobs --force`

## 8. 사람이 할 일

- 서비스용 Gmail 계정, 2단계 인증, 앱 비밀번호 (`feat/signup-email-code`에서 이미 준비했다면 같은 계정 사용)
- Cloud Run에 `APP_BASE_URL` 등록
- 배포 뒤 본인 계정으로 실제 메일 1통 수신 확인, 수신 거부 링크 동작 확인

## 9. 위험과 미결 사항

| 항목 | 내용 | 대응 |
| --- | --- | --- |
| 확인되지 않은 주소 | 인증번호 도입 전에 가입한 계정은 주소 소유가 확인되지 않음. 남의 주소로 가입된 계정이 있으면 그 주소 주인이 알림을 받음 | 메일에 판정 근거와 프로필 값을 넣지 않음. 수신 거부 링크는 로그인 없이 동작 |
| 판정 결과의 간접 노출 | 소득분위 등으로 판정한 "지원 가능" 목록 자체가 민감정보를 짐작하게 할 수 있음 | 위와 같음. 완전히 없애려면 수신 대상을 확인된 계정으로 좁혀야 함 |
| 동의 문구 | 개인정보 동의 문구에 "이메일을 알림 발송에 이용"이 없음 | 문구만 갱신하고 기존 동의자에게 재동의를 받지 않음 (2026-11-11 결정) |
| Gmail 한도·계정 정지 | 하루 약 500통. 스팸 신고가 쌓이면 발송 계정이 제한될 수 있음 | 사용자당 하루 1통, 실행당 450통 상한, `List-Unsubscribe` 헤더 |
| 수집 요청 시간 증가 | 발송이 수집 요청 안에서 실행됨. 수신자 50명 기준 1분 안쪽으로 예상 | Cloud Scheduler 작업의 시도 제한 시간을 T35에서 확인. 부족하면 `/internal/notify`와 세 번째 Scheduler 작업으로 분리 |
| 선행 브랜치 | `feat/signup-email-code`가 머지되지 않으면 `app/core/mail.py`가 없음 | 머지 뒤 시작. 일정이 어긋나면 그 브랜치의 메일 모듈만 먼저 가져오는 방안을 사용자와 상의 |
