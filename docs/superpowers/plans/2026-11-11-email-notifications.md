# 장학금 이메일 알림 (T35) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 하루 한 번(서울 9시 이후 첫 수집 때) 사용자별로 "조건에 맞는 새 장학금"과 "관심 장학금 마감 3일 전·1일 전"을 메일 1통으로 보냅니다.

**Architecture:** `POST /internal/crawl`이 수집을 마친 뒤 `app/notifications/jobs.py`의 `run_daily_notifications()`를 부릅니다. 대상 선정은 DB 없는 순수 함수(`select.py`), 메일 작성은 `message.py`, 발송은 기존 `app/core/mail.py`(Gmail SMTP)를 확장해 씁니다. 중복 발송은 `notification_runs`(하루 1회 잠금)와 `notification_logs`(보낸 항목 기록)로 막습니다.

**Tech Stack:** FastAPI, SQLAlchemy 2 + psycopg 3, Alembic, PyJWT, 표준 라이브러리 `smtplib`/`email`, pytest. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-11-email-notifications-design.md`

**범위:** 이 계획은 backend(T35)와 문서 갱신만 다룹니다. frontend(T36: 내 정보 화면 토글, `/unsubscribe` 화면, `npm run gen:api`)는 T35 PR이 머지된 뒤 frontend 저장소에서 별도 계획으로 진행합니다.

## Global Constraints

- **선행**: `feat/signup-email-code`가 `main`에 머지돼 있어야 합니다(`app/core/mail.py`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_MOCK`). Task 1에서 확인합니다.
- 브랜치는 `main`에서 `feat/T35-email-notifications`로 땁니다. 커밋 제목은 `T35:`로 시작하고 한국어로 씁니다. `main`에 직접 push하지 않습니다.
- 새 라이브러리를 추가하지 않습니다.
- 로그에 넣지 않는 것: 이메일 주소, 프로필 값, 메일 본문, 수신 거부 토큰, SMTP 비밀번호. 사용자는 `user_id`로만 기록합니다. 로그는 `app.core.logging.log_event`를 씁니다.
- 날짜·시각은 `app/parsers/dates.py`의 `now_kst`, `to_kst`, `kst`만 씁니다. `datetime.now()`를 쓰지 않습니다.
- 시각 칸은 모두 `DateTime(timezone=True)`입니다.
- 테스트는 SMTP에 접속하지 않습니다. 테스트는 `.env`를 읽지 않으므로 필요한 값은 `monkeypatch.setenv`로 넣습니다.
- `smtplib`, `ssl`, `email`은 모듈 맨 위가 아니라 보내는 함수 안에서 import합니다(계획서 9.5).
- `BackgroundTasks`를 쓰지 않습니다. 사용자 조회·알림 경로에서 LLM을 호출하지 않습니다.
- `app/extraction/`은 `app.notifications`를 import하지 않습니다.
- 메일에 판정 근거, 프로필 값, 조건 내용을 넣지 않습니다.
- 스펙의 값: 발송 시각 9시, 새 장학금 창 48시간, 마감 알림은 날짜 차이 3과 1, 실행당 최대 450통, `RUNNING` 무시 기준 1시간.
- DB가 필요한 테스트는 `docker compose up -d postgres`가 켜져 있어야 합니다.

## Review Focus

1. **한 사용자의 프로필 복호화 실패**(키 교체, 손상된 행): 그 사용자만 건너뛰고 나머지는 발송해야 합니다. → Task 6
2. **`criteria` JSON이 현재 스키마와 맞지 않는 장학금**(옛 추출 결과): 그 장학금만 후보에서 빼고 실행은 계속해야 합니다. → Task 6
3. **장학금 이름의 HTML 특수문자·줄바꿈**: HTML 본문에서 이스케이프하고, 제목 헤더에 이름을 넣지 않습니다. → Task 4
4. **두 인스턴스가 같은 시각에 `/internal/crawl`을 받는 경우**(Cloud Run 최대 인스턴스 2): 알림은 한 번만 나가야 합니다. → Task 6
5. **발송 도중 사용자가 탈퇴**: 기록 INSERT가 FK 오류로 실패해도 그 사용자만 건너뛰고 계속해야 합니다. → Task 6

## File Structure

| 파일 | 책임 |
| --- | --- |
| `app/models/notification.py` (신규) | `NotificationLog`, `NotificationRun` 모델 |
| `app/notifications/select.py` (신규) | 후보 + 수신자 → 보낼 항목. 순수 함수 |
| `app/notifications/message.py` (신규) | 항목 → 제목·본문(텍스트, HTML) |
| `app/notifications/jobs.py` (신규) | 하루 1회 잠금, DB 조회, 발송, 기록, CLI |
| `app/core/mail.py` (수정) | 접속 한 번으로 여러 통 보내는 `mail_session` |
| `app/core/security.py` (수정) | 수신 거부 토큰 |
| `app/api/notifications.py` (신규) | `POST /notifications/unsubscribe` |
| `app/api/auth.py`, `app/schemas/api/auth.py` (수정) | `PUT /auth/me/notifications`, `MeResponse.email_notifications` |
| `app/api/internal.py` (수정) | 수집 뒤 알림 실행 |

---

### Task 1: 선행 확인, 브랜치, 계획서·작업카드 갱신

**Files:**
- Modify: `../docs/장학금_매칭_서비스_계획서.md` (작업 공간 문서, git 저장소 밖)
- Modify: `../docs/작업카드.md`

- [ ] **Step 1: 선행 머지 확인 후 브랜치 생성**

```bash
git checkout main && git pull
git log --oneline -15 | grep -i "인증번호"      # 가입 인증번호 커밋이 보여야 함
test -f app/core/mail.py && grep -n "smtp_user\|mail_mock" app/core/config.py
git checkout -b feat/T35-email-notifications
```

`app/core/mail.py`가 없으면 여기서 멈추고 사용자에게 알립니다.

- [ ] **Step 2: 계획서 갱신** (스펙 6장 "문서" 목록 그대로)

| 절 | 바꿀 내용 |
| --- | --- |
| 2장 범위 표 | 포함 열에 "이메일 알림(새 맞춤 장학금, 관심 장학금 마감 3일 전·1일 전)" 추가 |
| 3장 기술 표 | "메일 발송: Gmail SMTP(`smtplib`), 가입 인증번호와 알림 공용" 행 추가 |
| 4.2 | 이메일 이용 목적에 "장학금 알림 발송" 추가. 기존 동의자 재동의 없음(2026-11-11 결정) |
| 6장 | `users.email_notifications`, `scholarships.created_at`, `NOTIFICATION_LOGS`, `NOTIFICATION_RUNS` (스펙 5.1 표) |
| 7.2 API 표 | `PUT /auth/me/notifications`, `POST /notifications/unsubscribe` 추가, `/auth/me`에 `email_notifications` |
| 7.3 | "메일 발송 기능이 없으므로" 문구가 남아 있으면 현재 상태에 맞게 수정 |
| 9.3 | "수집 뒤 서울 9시 이후이고 오늘 보내지 않았으면 알림 발송" 행 추가 |
| 9.6 | `APP_BASE_URL` 추가 |
| 9.9 | Gmail 하루 약 500통, 알림은 실행당 450통 상한 |

- [ ] **Step 3: 작업카드에 T35, T36 추가**

T34 뒤에 기존 카드 형식으로 추가합니다. T35 = backend, 선행 T17·T19·T23, 사양은 이 스펙 경로, 완료 기준은 Task 8의 명령. T36 = frontend, 선행 T35, 할 일은 `npm run gen:api`·내 정보 화면 토글·`/unsubscribe` 화면(로그인 없이 접근, 버튼을 눌러 `POST /api/notifications/unsubscribe`). 둘 다 "사람이 할 일"에 스펙 8장 내용을 적습니다.

- [ ] **Step 4: 사용자에게 문서 변경 내용 알림** (작업 공간 문서는 git 밖이라 커밋 없음)

---

### Task 2: 모델과 마이그레이션

**Files:**
- Create: `app/models/notification.py`
- Create: `alembic/versions/<rev>_email_notifications.py` (`down_revision`은 `uv run alembic heads`로 확인한 현재 head)
- Modify: `app/models/user.py`, `app/models/notice.py`, `app/models/__init__.py`
- Test: `tests/models/test_schema.py`

**Interfaces:**
- Produces:
  - `User.email_notifications: Mapped[bool]` (기본 true, `server_default="true"`)
  - `Scholarship.created_at: Mapped[datetime]` (`server_default=func.now()`, NOT NULL)
  - `NOTIFICATION_KINDS = ("NEW_MATCH", "DEADLINE_D3", "DEADLINE_D1")`, `NOTIFICATION_RUN_STATUSES = ("RUNNING", "DONE", "FAILED")`
  - `NotificationLog(id, user_id, scholarship_id, kind, apply_end, sent_at)` 테이블 `notification_logs`, 두 FK 모두 `ondelete="CASCADE"`, `Index("ix_notification_logs_user_id_kind", "user_id", "kind")`
  - `NotificationRun(run_date: date PK, status, started_at, finished_at, sent_count, failed_count)` 테이블 `notification_runs`, 카운트 기본 0
  - `app.models`에서 `NotificationLog`, `NotificationRun` export

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/models/test_schema.py`에 기존 스타일로 추가)

```python
def test_notification_schema(conn):
    insp = sa.inspect(conn)
    assert {"notification_logs", "notification_runs"} <= set(insp.get_table_names())
    users = {c["name"]: c for c in insp.get_columns("users")}
    assert users["email_notifications"]["nullable"] is False
    sch = {c["name"]: c for c in insp.get_columns("scholarships")}
    assert sch["created_at"]["nullable"] is False


def test_notification_log_kind_is_checked(conn):
    # 사용자·공지·장학금 1건씩 넣은 뒤
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(sa.text(
            "INSERT INTO notification_logs (user_id, scholarship_id, kind, sent_at) "
            "VALUES (:u, :s, 'WRONG', now())"), {"u": user_id, "s": scholarship_id})


def test_deleting_user_removes_notification_logs(conn):
    # 기록 1건을 넣고 users 행을 지우면 notification_logs가 0건
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/models/test_schema.py -k notification -v` → FAIL

- [ ] **Step 3: 모델 작성 후 마이그레이션 생성**

`uv run alembic revision --autogenerate -m "email notifications"`로 만든 뒤 손으로 고칩니다.

- `scholarships.created_at`은 세 단계로 넣습니다: nullable로 추가 → `UPDATE scholarships s SET created_at = n.posted_at FROM notices n WHERE n.id = s.notice_id` → NOT NULL + `server_default=sa.text("now()")`. 기존 장학금이 첫 발송 때 "새 장학금"으로 나가지 않게 하기 위함입니다.
- `downgrade`는 두 테이블과 두 칸을 지웁니다.
- 파일 머리말 주석은 기존 마이그레이션처럼 한국어 한 줄 설명으로 씁니다.

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/models -v` → PASS (기존 `upgrade`/`downgrade` 왕복 테스트 포함)

- [ ] **Step 5: 커밋** — `T35: 알림 수신 설정·발송 기록 테이블과 scholarships.created_at 추가`

---

### Task 3: 대상 선정 (순수 함수)

**Files:**
- Create: `app/notifications/__init__.py`, `app/notifications/select.py`
- Test: `tests/notifications/__init__.py`, `tests/notifications/test_select.py`

**Interfaces:**
- Consumes: `app.matching.engine.evaluate(profile, criteria, today, *, has_unread_attachments)`, `app.parsers.dates.to_kst`
- Produces:

```python
NEW_WINDOW = timedelta(hours=48)
NEW_NOTICE_KINDS = ("모집", "기준안내")       # 대시보드 목록과 같은 기준
NEW_STATUSES = ("OPEN", "ALWAYS")
DEADLINE_KINDS = {3: "DEADLINE_D3", 1: "DEADLINE_D1"}
SentKey = tuple[int, str, datetime | None]   # (scholarship_id, kind, apply_end). NEW_MATCH는 None

@dataclass(frozen=True)
class Candidate:
    scholarship_id: int
    name: str
    status: str
    notice_kind: str | None
    apply_end: datetime | None
    created_at: datetime
    criteria: ScholarshipCriteria
    has_unread: bool

@dataclass(frozen=True)
class Recipient:
    user_id: int
    profile: Profile
    states: dict[int, str]        # scholarship_id -> "BOOKMARKED" | "APPLIED"
    sent: frozenset[SentKey]

@dataclass(frozen=True)
class Item:
    scholarship_id: int
    name: str
    kind: str                     # NEW_MATCH | DEADLINE_D3 | DEADLINE_D1
    apply_end: datetime | None
    result: MatchResult | None    # 마감 알림은 None (판정하지 않음)

def select_items(recipient: Recipient, candidates: Sequence[Candidate], now: datetime) -> list[Item]
```

규칙(스펙 5.3):
- `NEW_MATCH`: `created_at > now - NEW_WINDOW`, `status in NEW_STATUSES`, `notice_kind in NEW_NOTICE_KINDS`, `states.get(id) != "APPLIED"`, `(id, "NEW_MATCH", None) not in sent`, `evaluate(...).result != "INELIGIBLE"`. `today`는 `to_kst(now).date()`.
- 마감: `states.get(id) == "BOOKMARKED"`, `status == "OPEN"`, `apply_end` 있음, `(to_kst(apply_end).date() - to_kst(now).date()).days`가 3 또는 1, `(id, kind, apply_end) not in sent`.
- 한 장학금이 둘 다 해당하면 마감 항목만 넣습니다(같은 메일에 두 번 나오지 않게).
- 반환 순서: 마감 항목(마감 빠른 순) → 새 장학금(마감 빠른 순, 마감 없는 것은 뒤).

- [ ] **Step 1: 실패하는 테스트 작성**

프로필과 조건은 `tests/api/test_scholarships.py`의 `PROFILE`, `ELIGIBLE`, `CONDITIONAL`, `INELIGIBLE`과 같은 값을 씁니다. `NOW = kst(2026, 11, 11, 9, 0)`.

```python
def test_new_match_includes_eligible_and_conditional_only():
    cands = [cand(1, ELIGIBLE), cand(2, CONDITIONAL), cand(3, INELIGIBLE)]
    items = select_items(recipient(), cands, NOW)
    assert [(i.scholarship_id, i.kind, i.result) for i in items] == [
        (1, "NEW_MATCH", "ELIGIBLE"), (2, "NEW_MATCH", "CONDITIONAL")]

def test_unread_attachment_lowers_eligible_to_conditional():   # has_unread=True → CONDITIONAL

@pytest.mark.parametrize("change", [
    {"created_at": NOW - timedelta(hours=49)},
    {"status": "CLOSED"},
    {"notice_kind": "결과"},
])
def test_new_match_excludes(change): ...                        # []

def test_new_match_excludes_applied_and_already_sent():
    # states={1: "APPLIED"} → 제외, sent={(2, "NEW_MATCH", None)} → 제외

@pytest.mark.parametrize(("deadline", "kind"), [
    (kst(2026, 11, 14, 23, 59), "DEADLINE_D3"),
    (kst(2026, 11, 14, 0, 0), "DEADLINE_D3"),     # 서울 날짜 경계
    (kst(2026, 11, 12, 23, 59), "DEADLINE_D1"),
    (kst(2026, 11, 13, 23, 59), None),            # 2일 전
    (kst(2026, 11, 15, 0, 0), None),              # 4일 전
    (kst(2026, 11, 11, 23, 59), None),            # 당일
])
def test_deadline_days(deadline, kind): ...       # BOOKMARKED, 오래된 created_at

def test_deadline_uses_seoul_date_for_utc_values():
    # apply_end = datetime(2026, 11, 14, 14, 59, tzinfo=UTC) (서울 11-14 23:59) → DEADLINE_D3

def test_deadline_requires_bookmark_and_open():   # 상태 없음, APPLIED, status CLOSED → []

def test_extended_deadline_notifies_again():
    # sent={(1, "DEADLINE_D3", 옛 마감)}, 새 apply_end가 3일 뒤 → DEADLINE_D3 포함
    # sent에 같은 apply_end가 있으면 제외

def test_bookmarked_new_scholarship_appears_once_as_deadline():
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/notifications/test_select.py -v` → FAIL (모듈 없음)
- [ ] **Step 3: `select.py` 구현**
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS
- [ ] **Step 5: 커밋** — `T35: 알림 대상 선정 함수 추가`

---

### Task 4: 수신 거부 토큰과 메일 내용

**Files:**
- Modify: `app/core/security.py`, `app/core/config.py`, `.env.example`
- Create: `app/notifications/message.py`
- Test: `tests/core/test_security.py`, `tests/notifications/test_message.py`

**Interfaces:**
- Consumes: `Item` (Task 3)
- Produces:

```python
# app/core/security.py
UNSUBSCRIBE_PURPOSE = "unsubscribe"
def create_unsubscribe_token(user_id: int) -> str      # payload {"sub": str(user_id), "purpose": "unsubscribe"}, 만료 없음
def read_unsubscribe_token(token: str) -> int          # 틀리면 InvalidTokenError

# app/core/config.py
app_base_url: str | None = None                        # 메일 링크 기준 주소. 끝의 "/"는 붙이지 않음

# app/notifications/message.py
@dataclass(frozen=True)
class MailContent:
    subject: str
    text: str
    html: str

def unsubscribe_url(base_url: str, token: str) -> str  # {base}/unsubscribe?token={token}
def build_message(items: Sequence[Item], *, base_url: str, unsubscribe_token: str) -> MailContent
```

고정 문구:
- 제목: `[장학금 매칭] 새 장학금 N건, 마감 임박 M건`. 한쪽이 0건이면 그 부분과 쉼표를 뺍니다.
- 구역 제목: `마감이 다가온 관심 장학금`, `새로 올라온 장학금` (이 순서, 빈 구역은 생략)
- 항목 표시: 새 장학금은 `지원 가능` 또는 `확인 필요`, 마감 알림은 `마감 3일 전` 또는 `마감 1일 전`
- 마감: `to_kst(apply_end)`를 `YYYY-MM-DD HH:MM`으로, 없으면 `상시`
- 링크: `{base_url}/scholarships/{id}` (frontend `src/routes.tsx`의 `/scholarships/:id`)
- 하단: `장학금 매칭에 가입한 이메일이라 이 메일을 받았습니다.` 다음 줄에 `수신 거부: {unsubscribe_url}`
- HTML은 `html.escape`로 이름을 이스케이프합니다. 제목에는 장학금 이름을 넣지 않습니다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_security.py
def test_unsubscribe_token_round_trip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    assert read_unsubscribe_token(create_unsubscribe_token(7)) == 7

def test_access_token_is_not_an_unsubscribe_token(monkeypatch):
    with pytest.raises(InvalidTokenError):
        read_unsubscribe_token(create_access_token(7, 0))

def test_unsubscribe_token_is_not_an_access_token(monkeypatch):
    with pytest.raises(InvalidTokenError):
        decode_access_token(create_unsubscribe_token(7))

def test_tampered_unsubscribe_token_is_rejected(monkeypatch):   # 마지막 글자 변경, "", "abc"

# tests/notifications/test_message.py
def test_subject_counts():
    assert build(new=2, deadline=1).subject == "[장학금 매칭] 새 장학금 2건, 마감 임박 1건"
    assert build(new=2, deadline=0).subject == "[장학금 매칭] 새 장학금 2건"
    assert build(new=0, deadline=1).subject == "[장학금 매칭] 마감 임박 1건"

def test_empty_section_is_omitted():            # 새 장학금만 있으면 "마감이 다가온 관심 장학금" 없음
def test_item_lines():                          # "지원 가능", "확인 필요", "마감 3일 전", "2026-11-14 23:59", "상시", ".../scholarships/5"
def test_footer_has_unsubscribe_link():         # text와 html 모두에 https://app.example/unsubscribe?token=TOKEN
def test_html_escapes_name_and_subject_has_no_name():
    content = build_message([item(name='<script>x</script>\n주입')], base_url=BASE, unsubscribe_token="T")
    assert "<script>" not in content.html and "&lt;script&gt;" in content.html
    assert "\n" not in content.subject and "script" not in content.subject
def test_body_has_no_criteria_or_evidence():    # Item에 없는 값이 본문에 없음: "min_gpa", "소득" 문자열 없음
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/core/test_security.py tests/notifications/test_message.py -v` → FAIL
- [ ] **Step 3: 구현** (`security.py`는 기존 `_jwt_secret`, `JWT_ALGORITHM` 사용. `.env.example`에 `APP_BASE_URL=http://localhost:5173`과 설명 한 줄)
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS
- [ ] **Step 5: 커밋** — `T35: 수신 거부 토큰과 알림 메일 내용 작성 추가`

---

### Task 5: 메일 모듈 확장

**Files:**
- Modify: `app/core/mail.py`
- Test: `tests/core/test_mail.py` (신규)

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

SendFn = Callable[[OutgoingMail], None]     # 실패하면 MailError

def mail_configured() -> bool               # MAIL_MOCK=1 이거나 SMTP_USER·SMTP_PASSWORD가 모두 있음

@contextmanager
def mail_session() -> Iterator[SendFn]      # 접속·로그인 1회. 접속 실패는 MailError. MAIL_MOCK=1이면 아무것도 보내지 않는 SendFn
```

- `send_signup_code`는 `mail_session`을 쓰도록 바꾸고 동작은 그대로 둡니다.
- `MailError` 메시지에는 예외 종류만 넣습니다(기존 규칙). `html`이 있으면 `EmailMessage.add_alternative(html, subtype="html")`.

- [ ] **Step 1: 실패하는 테스트 작성** (`smtplib.SMTP`를 `monkeypatch`로 가짜 클래스로 교체)

```python
def test_session_logs_in_once_and_sends_each_mail(fake_smtp, smtp_env):
    with mail_session() as send:
        send(OutgoingMail("a@example.com", "s1", "t1"))
        send(OutgoingMail("b@example.com", "s2", "t2", html="<p>h</p>", headers={"List-Unsubscribe": "<u>"}))
    assert fake_smtp.logins == 1 and len(fake_smtp.sent) == 2
    assert fake_smtp.sent[1]["List-Unsubscribe"] == "<u>"
    assert fake_smtp.sent[1].get_body(("html",)) is not None

def test_one_failed_mail_raises_mail_error_without_address(fake_smtp, smtp_env):
    fake_smtp.fail_for = "a@example.com"
    with mail_session() as send, pytest.raises(MailError) as err:
        send(OutgoingMail("a@example.com", "s", "t"))
    assert "a@example.com" not in str(err.value)

def test_connect_failure_raises_mail_error(monkeypatch, smtp_env):   # SMTP() 생성자가 OSError

def test_mock_mode_never_connects(monkeypatch):
    monkeypatch.setenv("MAIL_MOCK", "1")     # SMTP 클래스가 호출되면 실패하도록 교체
    with mail_session() as send:
        send(OutgoingMail("a@example.com", "s", "t"))

def test_mail_configured(monkeypatch):       # 없음 False, MAIL_MOCK=1 True, SMTP_USER+SMTP_PASSWORD True, 하나만 있으면 False
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/core/test_mail.py -v` → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/core/test_mail.py tests/api/test_signup_code.py tests/api/test_auth.py -v` → PASS (가입 인증번호 테스트가 그대로 통과해야 함)
- [ ] **Step 5: 커밋** — `T35: 접속 한 번으로 여러 통 보내는 메일 세션 추가`

---

### Task 6: 알림 실행 작업

**Files:**
- Create: `app/notifications/jobs.py`
- Modify: `app/api/profile.py` (`_to_response` → `profile_from_row`로 이름 변경), `app/api/scholarships.py` (`_unread_attachments_exist` → `unread_attachments_exist`, import 정리)
- Test: `tests/notifications/conftest.py`, `tests/notifications/test_jobs.py`

**Interfaces:**
- Consumes: Task 2 모델, `select_items`/`Candidate`/`Recipient` (Task 3), `build_message`, `unsubscribe_url`, `create_unsubscribe_token` (Task 4), `mail_session`, `mail_configured`, `OutgoingMail`, `MailError` (Task 5), `app.api.profile.profile_from_row(row: UserProfile) -> Profile`, `app.api.scholarships.unread_attachments_exist()`
- Produces:

```python
NOTIFY_LOCK_KEY = 36_2026_12                # 수집(36_2026_11)과 다른 키
SEND_HOUR = 9
MAX_MAILS_PER_RUN = 450
RUNNING_STALE_AFTER = timedelta(hours=1)
DEADLINE_LOOKAHEAD = timedelta(days=4)

@dataclass
class NotifyOutcome:
    skipped: bool
    sent: int = 0
    failed: int = 0

def run_daily_notifications(
    *,
    force: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    clock: Callable[[], datetime] = now_kst,
    open_mail: Callable[[], AbstractContextManager[SendFn]] = mail_session,
) -> NotifyOutcome

def main(argv: list[str] | None = None) -> int     # `python -m app.notifications.jobs [--force]`
```

흐름(스펙 5.2):
1. `mail_configured()`가 False이거나 `app_base_url`이 없으면 경고 로그 한 줄을 남기고 `skipped=True`. 실행 행을 만들지 않습니다.
2. `force`가 아니고 `to_kst(now).hour < SEND_HOUR`이면 `skipped=True`.
3. 시작 잠금: `app/scheduler/jobs.py`의 `start_runs`와 같은 방식으로 한 트랜잭션에서 `pg_advisory_xact_lock(NOTIFY_LOCK_KEY)` → 오늘(`to_kst(now).date()`) 행 확인. `DONE`이거나 1시간 안에 시작한 `RUNNING`이면 `skipped=True`(`force`면 무시). 아니면 `RUNNING`으로 INSERT 또는 UPDATE하고 커밋.
4. 후보 조회(쿼리 1번): `Scholarship` JOIN `Notice` + `unread_attachments_exist()`. 조건은 `(created_at > now - 48h AND status IN (OPEN, ALWAYS))` OR `(status = OPEN AND apply_end BETWEEN now AND now + DEADLINE_LOOKAHEAD)`. `ScholarshipCriteria.model_validate`가 실패한 행은 `scholarship_id`만 로그에 남기고 뺍니다.
5. 후보가 없으면 `DONE`(0건)으로 끝냅니다.
6. 수신자 조회(쿼리 3번, 사용자 수와 무관): ① `User` JOIN `UserProfile` WHERE `privacy_agreed_at IS NOT NULL AND email_notifications` ② 후보 id에 대한 `UserScholarshipState` ③ 후보 id에 대한 `NotificationLog`. 프로필 복호화(`profile_from_row`)가 실패한 사용자는 `user_id`와 예외 종류만 로그에 남기고 뺍니다.
7. `open_mail()`을 한 번 열고 사용자마다: `select_items` → 비어 있으면 건너뜀 → `build_message` → `OutgoingMail(to=email, headers={"List-Unsubscribe": f"<{unsubscribe_url(...)}>"})` 발송 → 성공하면 항목마다 `NotificationLog`(`NEW_MATCH`의 `apply_end`는 NULL)를 넣고 커밋. `MailError`나 커밋 실패(탈퇴로 인한 FK 오류 포함)는 `rollback` 후 `failed += 1`, `user_id`만 로그에 남기고 다음 사용자.
8. `sent + failed`가 `MAX_MAILS_PER_RUN`에 닿으면 멈추고 경고 로그.
9. 실행 행을 `DONE`과 건수로 마감합니다. `open_mail()` 진입이 `MailError`면 `FAILED`로 마감하고 `NotifyOutcome(skipped=False)`를 반환합니다(예외를 밖으로 던지지 않음).

- [ ] **Step 1: fixture 작성** (`tests/notifications/conftest.py`)

`tests/scheduler/conftest.py`처럼 `migrated_engine`을 가져와 `session_factory`를 만들고, 테스트 앞뒤로 `TRUNCATE notification_logs, notification_runs, user_scholarship_states, user_profiles, users, scholarships, notices RESTART IDENTITY CASCADE`를 실행합니다. `autouse`로 `MAIL_MOCK=1`, `APP_BASE_URL=https://app.example`, `JWT_SECRET`, `FIELD_ENCRYPTION_KEY`(기존 프로필 테스트와 같은 방식의 값)를 넣습니다. 도우미: `add_user(factory, email, *, profile=PROFILE, consented=True, notifications=True) -> int`, `add_scholarship(...)`(`tests/api/test_scholarships.py`의 것에 `created_at` 인자 추가), `bookmark(factory, user_id, scholarship_id)`, 보낸 메일을 모으는 `outbox` fixture(`open_mail` 인자로 넘길 가짜 세션, `fail_for` 주소 집합 지원).

- [ ] **Step 2: 실패하는 테스트 작성** (`NOW = kst(2026, 11, 11, 9, 0)`, `clock=lambda: NOW`)

```python
def test_sends_one_mail_with_new_and_deadline_items(session_factory, outbox):
    # 새 장학금 1건(ELIGIBLE, created_at=NOW-1h) + 관심 장학금 1건(마감 11-14 23:59)
    outcome = run(session_factory, outbox)
    assert (outcome.skipped, outcome.sent, outcome.failed) == (False, 1, 0)
    assert outbox.mails[0].subject == "[장학금 매칭] 새 장학금 1건, 마감 임박 1건"
    assert "List-Unsubscribe" in outbox.mails[0].headers
    assert logs(session_factory) == {(uid, new_id, "NEW_MATCH"), (uid, bm_id, "DEADLINE_D3")}
    assert run_row(session_factory).status == "DONE"

def test_before_nine_is_skipped(...):            # clock=kst(2026,11,11,8,0) → skipped, 실행 행 없음
def test_second_call_same_day_is_skipped(...):   # 두 번째 호출 skipped, 메일 총 1통
def test_next_day_does_not_resend_new_match(...):# 다음 날 9시 실행 → 메일 0통
def test_failed_run_is_retried(...):             # 첫 호출: open_mail이 MailError → FAILED, 예외 없음. 두 번째 호출이 발송
def test_stale_running_row_is_retried(...):      # started_at = NOW - 2h인 RUNNING → 진행. NOW - 10분이면 skipped
def test_force_ignores_hour_and_done_row(...)
def test_recipients_filter(...):                 # 수신 끔, 동의 전, 프로필 없음 → 메일 없음
def test_user_with_nothing_to_send_gets_no_mail(...)
def test_one_failed_mail_does_not_stop_others(session_factory, outbox):
    outbox.fail_for = {"a@example.com"}
    outcome = run(...)
    assert (outcome.sent, outcome.failed) == (1, 1)
    assert a_id not in {u for u, _, _ in logs(session_factory)}      # 실패한 사용자는 기록 없음 → 다음 날 다시 시도
def test_mail_cap(monkeypatch, ...):             # MAX_MAILS_PER_RUN=2로 바꾸고 사용자 3명 → 2통
def test_skips_when_mail_not_configured(monkeypatch, ...):   # MAIL_MOCK 제거, SMTP 값 없음 → skipped, 실행 행 없음
def test_skips_without_app_base_url(monkeypatch, ...)
def test_query_count_does_not_grow_with_users(...):          # 사용자 1명과 5명의 SELECT 수가 같음 (tests/api/test_scholarships.py의 쿼리 수 세는 방식)

# Review Focus
def test_broken_profile_skips_only_that_user(...):           # 한 사용자의 enc_income_bracket을 b"garbage"로 → 다른 사용자는 수신
def test_invalid_criteria_skips_only_that_scholarship(...):  # criteria={"min_gpa": "높음"} 1건 + 정상 1건 → 정상 1건만 포함
def test_concurrent_start_runs_once(session_factory):        # start 함수를 두 번 연속 호출 → 두 번째는 False
def test_user_deleted_mid_run_does_not_stop_run(...):        # outbox가 첫 사용자 발송 시점에 그 사용자를 DELETE → failed 1, 다음 사용자는 sent
def test_logs_have_no_email_address(caplog, ...):            # 실패 로그에 "a@example.com" 없음
```

- [ ] **Step 3: 실패 확인** — `uv run pytest tests/notifications/test_jobs.py -v` → FAIL
- [ ] **Step 4: 이름 변경 + `jobs.py` 구현**

`profile.py`의 `_to_response`를 `profile_from_row`로 바꾸고 파일 안의 호출과 `scholarships.py`의 `from app.api.profile import _to_response as profile_from_row`를 고칩니다. `main`은 `configure_logging()` 후 결과를 한 줄로 출력하고 0을 반환합니다.

- [ ] **Step 5: 통과 확인** — `uv run pytest tests/notifications tests/api/test_scholarships.py tests/api/test_profile.py -v` → PASS
- [ ] **Step 6: 커밋** — `T35: 하루 한 번 알림 발송 작업 추가`

---

### Task 7: API와 수집 연결

**Files:**
- Create: `app/api/notifications.py`, `app/schemas/api/notifications.py`
- Modify: `app/api/auth.py`, `app/schemas/api/auth.py`, `app/api/internal.py`, `app/main.py`, `.github/workflows/deploy.yml`
- Test: `tests/api/test_notifications.py` (신규), `tests/api/test_auth.py`, `tests/api/test_api_contract.py`, `tests/scheduler/test_internal_api.py`, `tests/extraction/test_extractor.py`

**Interfaces:**
- Consumes: `read_unsubscribe_token`, `create_unsubscribe_token`, `run_daily_notifications`
- Produces:
  - `MeResponse.email_notifications: bool`
  - `PUT /auth/me/notifications`, 본문 `NotificationSettingRequest {email_notifications: bool}` (`extra="forbid"`), 응답 `MeResponse`, 401
  - `POST /notifications/unsubscribe`, 본문 `UnsubscribeRequest {token: str}` (`max_length=1024`, `extra="forbid"`), 성공 204, 토큰 오류 400 `"유효하지 않은 링크입니다"`. 사용자가 이미 탈퇴했어도 204(가입 여부를 드러내지 않음)
  - `app/main.py`의 라우터 목록에 `notifications` 추가

`internal.py`: `run_crawl()` 뒤에 `from app.notifications.jobs import run_daily_notifications`(함수 안 import)를 부릅니다. 여기서 난 예외는 모두 잡아 `log_event(logger, "알림 발송 실패", logging.ERROR, error=type(exc).__name__)`만 남기고 `CrawlResponse`는 그대로 반환합니다. 응답 스키마는 바꾸지 않습니다.

`deploy.yml`: `--set-env-vars`에 `|APP_BASE_URL=https://cbnu-scholarship.vercel.app`을 추가합니다(같은 줄의 `GOOGLE_REDIRECT_URI`와 같은 호스트).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/api/test_notifications.py  (tests/api/test_auth.py의 api, factory, signup 사용)
def test_me_has_email_notifications_on_by_default(api):
    signup(api); assert api.get("/auth/me").json()["email_notifications"] is True

def test_toggle_requires_login(api):
    assert api.put("/auth/me/notifications", json={"email_notifications": False}).status_code == 401

def test_toggle_off_and_on(api):
    signup(api)
    r = api.put("/auth/me/notifications", json={"email_notifications": False})
    assert r.status_code == 200 and r.json()["email_notifications"] is False
    assert api.get("/auth/me").json()["email_notifications"] is False
    assert api.put("/auth/me/notifications", json={"email_notifications": True}).json()["email_notifications"] is True

def test_unsubscribe_with_valid_token_needs_no_login(api, client):
    signup(api); user_id = api.get("/auth/me").json()["id"]
    r = client.post("/notifications/unsubscribe", json={"token": create_unsubscribe_token(user_id)})   # 쿠키 없는 클라이언트
    assert r.status_code == 204
    assert api.get("/auth/me").json()["email_notifications"] is False

@pytest.mark.parametrize("token", ["", "abc", "a.b.c"])
def test_unsubscribe_rejects_bad_token(client, token):          # 400

def test_unsubscribe_rejects_login_jwt(api, client):            # 쿠키의 access_token 값을 넣으면 400, 설정은 그대로 True
def test_unsubscribe_for_deleted_user_is_204(client):           # create_unsubscribe_token(999999) → 204
def test_unsubscribe_is_idempotent(...)                         # 두 번 호출 모두 204

# tests/scheduler/test_internal_api.py
def test_crawl_runs_notifications_after_crawl(client, calls, monkeypatch):
    seen = []; monkeypatch.setattr("app.notifications.jobs.run_daily_notifications", lambda **kw: seen.append(kw))
    assert client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN}).status_code == 200
    assert seen == [{}]

def test_notification_error_does_not_fail_crawl(client, calls, monkeypatch):
    def boom(**kw): raise RuntimeError("x")
    monkeypatch.setattr("app.notifications.jobs.run_daily_notifications", boom)
    r = client.post("/internal/crawl", headers={"X-Cron-Token": TOKEN})
    assert r.status_code == 200 and r.json()["runs"][0]["source_code"] == "SW_DEPT"

def test_wrong_token_does_not_run_notifications(...)            # 403이고 seen == []
```

`tests/api/test_api_contract.py`: `SPEC_ENDPOINTS`와 `IMPLEMENTED`에 `("put", "/auth/me/notifications")`, `("post", "/notifications/unsubscribe")` 추가. 기존 `test_valid_token_runs_crawl` 등 `calls` fixture를 쓰는 테스트가 실제 알림 작업을 타지 않도록 `calls` fixture에서 `run_daily_notifications`도 가짜로 바꿉니다.

`tests/extraction/test_extractor.py`: `FORBIDDEN_MODULES`에 `"app.notifications"` 추가.

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/api/test_notifications.py tests/scheduler/test_internal_api.py tests/api/test_api_contract.py -v` → FAIL
- [ ] **Step 3: 구현** (`auth.py`의 `_me`가 `email_notifications`를 채우게 함)
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/api tests/scheduler tests/extraction -v` → PASS
- [ ] **Step 5: 커밋** — `T35: 알림 수신 설정·수신 거부 API와 수집 뒤 알림 실행 연결`

---

### Task 8: 완료 기준 실행과 마무리

**Files:**
- Modify: `CLAUDE.md` (backend), `README.md`(환경변수 표가 있으면), `../docs/작업카드.md`

- [ ] **Step 1: backend `CLAUDE.md` 갱신**

구조 목록에 `notifications/  # select.py(대상 선정), message.py(메일 내용), jobs.py(하루 1회 발송, python -m app.notifications.jobs)` 추가. 규칙에 "알림" 항목 추가: 수집 요청 끝에서 실행, 하루 1회 잠금은 `pg_advisory_xact_lock` + `NOTIFICATION_RUNS`, 메일에 판정 근거·프로필 값 금지, 로그는 `user_id`만. 명령에 `uv run python -m app.notifications.jobs` (`--force`) 추가. 환경변수 목록에 `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_MOCK`(없으면), `APP_BASE_URL` 추가.

- [ ] **Step 2: 완료 기준 명령 실행** (결과를 그대로 사용자에게 보여 줍니다)

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run alembic upgrade head
MAIL_MOCK=1 APP_BASE_URL=http://localhost:5173 uv run python -m app.notifications.jobs --force
```

Expected: 린트 통과, 테스트 전부 PASS(건너뛴 테스트가 있으면 이유와 함께 보고), 마지막 명령은 오류 없이 발송 건수 한 줄 출력.

- [ ] **Step 3: 작업카드 T35를 `[x] 완료`로 변경, 커밋** — `T35: 알림 문서·명령 정리`

- [ ] **Step 4: 사용자에게 보고**

- 완료 기준 명령의 실제 출력
- frontend 영향: `/auth/me` 응답에 `email_notifications` 추가, 새 API 2개 → T36에서 `npm run gen:api`
- 사람이 할 일: Cloud Run에 `APP_BASE_URL`(배포 워크플로에 포함됨), 배포 뒤 본인 계정으로 메일 수신과 수신 거부 링크 확인, Cloud Scheduler 수집 작업의 시도 제한 시간이 수집 + 발송 시간보다 긴지 확인(스펙 9장)
- push와 PR 생성은 사용자가 요청하면 합니다.
