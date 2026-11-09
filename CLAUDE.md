# backend 저장소

> 공통 규칙은 상위 폴더의 `CLAUDE.md`, 사양은 `../docs/장학금_매칭_서비스_계획서.md`를 따릅니다.

## 책임 (계획서 8.1)
- 크롤링, PDF 텍스트 추출, 정기 수집, **AI 추출(Gemini)·골든셋 평가**, DB, 회원·프로필·암호화, 규칙 엔진, 웹 API를 맡습니다.
- AI 추출은 `app/extraction/` 모듈을 함수로 직접 호출합니다(별도 서비스·HTTP 없음). `AI_MOCK=1`이면 Gemini 대신 목 응답을 씁니다.
- 이 저장소는 2026-11-04에 기존 ai 저장소를 합친 것입니다(계획서 8장).

## 구조 (계획서 8.2)
```
app/
├─ core/        # config, db(엔진·세션), crypto(AES-GCM), security(Argon2·JWT·시도 제한), logging
├─ models/      # SQLAlchemy 모델 (계획서 6장)        ★공용
├─ schemas/     # API DTO  ★공용
├─ crawlers/    # base.py, sw_dept.py, aibc.py, kosaf.py
├─ parsers/     # pdf.py, hwp.py, attachments.py(첨부 형식 판별·HWPX·DOCX·ZIP), deadline.py, dates.py
├─ extraction/  # schemas.py(ExtractRequest·ExtractedCriteria ★공용), extractor.py(Gemini 호출·재시도), prompts/extract_vN.md
├─ matching/    # rules.py(조건별 비교), engine.py(3단계 판정)
├─ scheduler/   # jobs.py (수집 작업, `python -m app.scheduler.jobs`)
└─ api/         # health, auth, google, profile, scholarships, admin, internal
eval/           # 골든셋 평가 스크립트 (`python -m eval.run`), results/
tests/fixtures/ # 사이트별 저장 HTML, PDF 샘플, AI 응답 샘플, golden/(공지 30건 + 정답 JSON)
```

## 규칙
- **공용 파일**(`models/`, `schemas/`, `extraction/schemas.py`)은 바꿀 때마다 Alembic 마이그레이션(모델)이나 프롬프트 버전(추출 스키마)을 함께 갱신하고, 사용자에게 영향 범위를 알립니다.
- DB 드라이버는 psycopg 3(`postgresql+psycopg://`)를 쓰고 `pool_pre_ping=True`를 켭니다. 앱은 `DATABASE_URL`(pooled), 마이그레이션은 `DATABASE_URL_DIRECT`를 씁니다.
- 시각 컬럼은 모두 `timestamptz`입니다. 날짜 해석은 `parsers/dates.py` 한 곳에서만 합니다(계획서 2장 "날짜 해석").
- **크롤러**:
  - `crawlers/base.py`의 요청 함수만 씁니다(1초 간격, User-Agent, 타임아웃).
  - 테스트는 `tests/fixtures/`의 저장 HTML로만 합니다. 테스트에서 네트워크 요청은 금지입니다.
- **수집 작업**(`scheduler/jobs.py`)은 요청 안에서 동기로 끝까지 실행합니다. `BackgroundTasks`는 쓰지 않습니다. 중복 실행은 한 트랜잭션 안에서 `pg_advisory_xact_lock`을 잡은 뒤 `CRAWL_RUNS`의 `RUNNING` 행을 확인·INSERT해서 막습니다. 조건부 INSERT 한 문장만으로는 동시 실행을 막지 못합니다(계획서 9.3).
- **규칙 엔진**(`matching/`)은 DB나 HTTP에 의존하지 않는 순수 함수로 만듭니다. 계획서 5.3 표의 **행마다 충족/불충족/알 수 없음 테스트**를 둡니다.
- **인증**:
  - JWT는 httpOnly·Secure·SameSite=Lax 쿠키로만 발급합니다. 토큰의 `token_version`을 요청마다 DB 값과 비교합니다.
  - 동의 전 사용자는 프로필·장학금 API에서 403을 받습니다.
  - 관리자는 `ADMIN_EMAILS`에 있고 `google_sub`가 있는 계정입니다(계획서 7.3).
- **암호화**: 민감 필드는 `core/crypto.py`의 함수로만 암·복호화합니다. 복호화한 값을 로그나 예외 메시지에 넣지 않습니다.
- **AI 추출**(`extraction/`, 계획서 3장 AI 행·5.2·8.1·9.3):
  - **개인정보 경계**: 공개 함수는 `extract(request: ExtractRequest) -> ExtractionResult` 하나이고, 공지 텍스트 외의 인자를 받지 않습니다. `extraction/`은 `app.api`, `app.core.crypto`, `app.core.security`, 사용자·프로필·상태 모델을 import하지 않습니다(import 경계 테스트). 추출은 수집 작업에서만 부르고, 학생 요청 경로에서는 부르지 않습니다. 관리자 재추출 API는 `extract_status = PENDING`만 기록하고, 다음 수집이 한 번에 최대 20건씩 처리합니다(계획서 9.3).
  - `extraction/schemas.py`가 유일한 정의입니다(사본 없음). 필드는 계획서 5.2 표와 정확히 같아야 합니다.
  - `null`은 "조건 없음"입니다. 모호한 조건은 `null`로 두고 필드 이름을 `ambiguous_fields`에 넣습니다. 이 규칙은 프롬프트에 반드시 적습니다.
  - 프롬프트는 `extraction/prompts/extract_vN.md` 파일로 관리합니다. 프롬프트를 크게 바꾸면 새 버전 파일을 만들고, 결과의 `model_version`에 `{GEMINI_MODEL}+{프롬프트 버전}`을 넣습니다.
  - Gemini는 Structured Output(`response_schema`)으로 `ExtractedCriteria`를 강제하고, 응답은 Pydantic으로 다시 검증합니다.
  - 모델 이름은 환경변수 `GEMINI_MODEL`에서 읽습니다. 코드에 모델 이름을 박지 않습니다.
  - 429·5xx는 지수 백오프로 최대 3회 재시도합니다. 입력 텍스트는 3만 자에서 자릅니다.
  - Gemini 호출 1회 60초, 공지 1건의 재시도 포함 전체 100초입니다. 넘거나 재시도를 다 쓰거나 검증에 실패하면 `ExtractionError`를 던지고, 수집 작업은 `extract_status = FAILED`로 기록한 뒤 다음 공지를 계속 처리합니다.
  - 테스트는 Gemini를 실제로 호출하지 않습니다(클라이언트를 목으로 대체). 실제 호출은 `eval/`과 카드의 수동 확인에서만 합니다.
- **골든셋 평가**(`eval/`): `uv run python -m eval.run` → 필드별 정답률과 전체 정답률을 출력하고 `eval/results/{model_version}.json`에 저장합니다. 채점은 계획서 10장 "골든셋 정답률 계산"을 따르고 목표는 90%입니다. 결과 파일은 커밋합니다(프롬프트 변경 이력 추적용).
- **지연 대책**(계획서 9.5, 9.8):
  - 앱 시작 시 DB 연결이나 데이터 미리 불러오기를 하지 않습니다.
  - PyMuPDF·olefile·BeautifulSoup/lxml·`google-genai`는 모듈 맨 위가 아니라 수집·추출 코드 안에서 import합니다.
  - 화면 하나에 API 한 번이 되게 응답을 설계하고, N+1 쿼리를 만들지 않습니다(`selectinload`/`joinedload`).
  - 요청 로그에 `method`, `path`, `status`, `duration_ms`를 남깁니다.
- CORS는 설정하지 않습니다(Vercel 프록시로 같은 출처, 계획서 9.2).
- redirect_uri는 요청 호스트가 아니라 `GOOGLE_REDIRECT_URI`로 만듭니다.

## 명령
- 실행: `docker compose up` (postgres + backend, `--profile full`이면 frontend 포함) 또는 `uv run uvicorn app.main:app --port 8000 --reload`
- 테스트: `uv run pytest` (DB가 필요한 테스트는 compose의 postgres 사용). 테스트는 `.env`와 셸 환경변수를 읽지 않으므로(`tests/conftest.py`), 설정값이 필요한 테스트는 `monkeypatch.setenv`로 넣습니다
- 린트: `uv run ruff check . && uv run ruff format .`
- 마이그레이션: `uv run alembic revision --autogenerate -m "..."`, `uv run alembic upgrade head`
- 수집: `uv run python -m app.scheduler.jobs` (`--backfill`, `--source SW_DEPT`)
- 골든셋 평가: `uv run python -m eval.run`

## 환경변수 (계획서 9.6)
`DATABASE_URL`, `DATABASE_URL_DIRECT`, `JWT_SECRET`, `FIELD_ENCRYPTION_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`(최신 안정 Flash 모델 이름, 계획서 9.6·T08에서 확정), `AI_MOCK`(로컬 전용), `CRON_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `ADMIN_EMAILS`
