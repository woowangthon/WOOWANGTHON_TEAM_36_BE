"""인증 core (계획서 7.3 세션·로그인 시도 제한·관리자, 9.2 쿠키).

- 비밀번호: Argon2(`pwdlib`)
- 세션: JWT(HS256, 7일, `sub`=user_id, `ver`=token_version)를 httpOnly 쿠키로만 전달
- 의존성: 현재 사용자(요청마다 DB `token_version` 비교) → 동의 확인 → 관리자 확인
- 로그인 시도 제한: IP + 이메일 분당 5회, 이메일만 분당 10회 (인스턴스 메모리)
- 가입 인증번호: `code_token`·`signup_token`(용도별 JWT)과 발송·확인 시도 제한 (7.3)

비밀번호·JWT·쿠키·인증번호 값은 로그에 남기지 않습니다(4.2). 사용자는 `user_id`로만 기록합니다.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import User
from app.parsers.dates import now_kst

# --- 비밀번호 ---------------------------------------------------------------

_password_hash = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Google 전용 계정(`password_hash` NULL)이면 항상 False."""
    if not password_hash:
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:  # 손상된 해시 문자열
        return False


# --- JWT -------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)


class InvalidTokenError(Exception):
    """서명·만료·형식 오류. 메시지에 토큰 값을 넣지 않습니다."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    token_version: int


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if secret is None:
        raise RuntimeError("JWT_SECRET이 설정되지 않았습니다")
    return secret.get_secret_value()


def create_access_token(user_id: int, token_version: int, *, now: datetime | None = None) -> str:
    issued = now or now_kst()
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": int(issued.timestamp()),
        "exp": int((issued + TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, now: datetime | None = None) -> TokenClaims:
    options = {"require": ["sub", "ver", "exp"]}
    try:
        if now is None:
            payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM], options=options)
        else:  # 테스트용: 만료 판정을 정해진 시각으로
            payload = jwt.decode(
                token,
                _jwt_secret(),
                algorithms=[JWT_ALGORITHM],
                options={**options, "verify_exp": False},
            )
            if payload["exp"] <= now.timestamp():
                raise jwt.ExpiredSignatureError
        return TokenClaims(user_id=int(payload["sub"]), token_version=int(payload["ver"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise InvalidTokenError("유효하지 않은 토큰") from None


# --- 쿠키 (9.2: Vercel rewrites로 같은 출처, CORS 없음) ----------------------------

AUTH_COOKIE = "access_token"


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=int(TOKEN_TTL.total_seconds()),
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE, path="/", httponly=True, secure=True, samesite="lax")


def issue_session(response: Response, user: User) -> None:
    """로그인 성공 시 JWT를 만들어 쿠키로 내려 줍니다."""
    set_auth_cookie(response, create_access_token(user.id, user.token_version))


# --- 현재 사용자 의존성 ---------------------------------------------------------


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다")


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    """쿠키의 JWT를 검증하고, DB의 `token_version`과 다르면 401(비밀번호 무효화·탈퇴 대비)."""
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        raise _unauthorized()
    try:
        claims = decode_access_token(token)
    except InvalidTokenError:
        raise _unauthorized() from None
    user = db.get(User, claims.user_id)
    if user is None or user.token_version != claims.token_version:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_consented_user(user: CurrentUser) -> User:
    """개인정보 동의 전이면 프로필·장학금 API에서 403(7.3)."""
    if user.privacy_agreed_at is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="동의가 필요합니다")
    return user


ConsentedUser = Annotated[User, Depends(get_consented_user)]


def is_admin(user: User) -> bool:
    """`ADMIN_EMAILS`에 있고 Google로 확인된(`google_sub` 있음) 계정만 관리자(7.3)."""
    return bool(user.google_sub) and user.email.lower() in get_settings().admin_email_list


def get_admin_user(user: CurrentUser) -> User:
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="관리자만 접근할 수 있습니다"
        )
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


# --- 로그인 시도 제한 (7.3) -------------------------------------------------------

LOGIN_WINDOW_SECONDS = 60.0
LOGIN_LIMIT_PER_IP_EMAIL = 5
LOGIN_LIMIT_PER_EMAIL = 10


def client_ip(request: Request) -> str:
    """Vercel이 넣는 `X-Forwarded-For`의 첫 값. 없으면 직접 연결한 주소."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


class LoginRateLimiter:
    """키별로 최근 1분 시도 시각을 기억하는 슬라이딩 윈도. 시도는 성공·실패와 관계없이 셉니다.

    인스턴스 메모리 기준이며(최대 인스턴스 2개라 충분, 7.3), 스레드에서 안전합니다.
    """

    def __init__(
        self,
        *,
        per_ip_email: int = LOGIN_LIMIT_PER_IP_EMAIL,
        per_email: int = LOGIN_LIMIT_PER_EMAIL,
        window: float = LOGIN_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = {"ip_email": per_ip_email, "email": per_email}
        self._window = window
        self._clock = clock
        self._hits: dict[tuple[str, ...], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, ip: str, email: str) -> None:
        """시도 1회를 기록합니다. 두 제한 중 하나라도 넘으면 429."""
        email = email.strip().lower()
        keys = {"ip_email": ("ip_email", ip, email), "email": ("email", email)}
        with self._lock:
            now = self._clock()
            self._prune(now)
            for kind, key in keys.items():
                hits = self._hits.get(key)
                if hits is not None and len(hits) >= self._limits[kind]:
                    retry_after = max(1, int(hits[0] + self._window - now) + 1)
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요",
                        headers={"Retry-After": str(retry_after)},
                    )
            for key in keys.values():
                self._hits.setdefault(key, deque()).append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]


login_limiter = LoginRateLimiter()


def check_login_rate(request: Request, email: str) -> None:
    """`/auth/login`에서 비밀번호 확인 전에 부릅니다(T19)."""
    login_limiter.hit(client_ip(request), email)


# --- 가입 인증번호 (7.3) ----------------------------------------------------------
# 번호와 대기 중인 가입은 저장하지 않습니다.
# 서명한 토큰을 화면이 들고 있다가 다음 단계에 돌려줍니다.
# 토큰마다 `purpose`가 달라 로그인 JWT·`code_token`·`signup_token`을 서로 바꿔 쓸 수 없습니다.

SIGNUP_CODE_TTL = timedelta(minutes=10)
SIGNUP_TOKEN_TTL = timedelta(minutes=30)
SIGNUP_CODE_PURPOSE = "signup_code"
SIGNUP_PURPOSE = "signup"
MOCK_SIGNUP_CODE = "000000"


class ExpiredTokenError(InvalidTokenError):
    """만료된 가입 토큰. 화면에 "다시 받아 주세요"를 보여 주려고 따로 구분합니다."""


def new_signup_code() -> str:
    """6자리 숫자. `MAIL_MOCK=1`(로컬 전용)이면 000000."""
    if get_settings().mail_mock:
        return MOCK_SIGNUP_CODE
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_mac(email: str, code: str) -> str:
    """서버 비밀키로 섞은 값이라, 토큰을 열어 봐도 번호를 거꾸로 알아낼 수 없습니다."""
    message = f"{SIGNUP_CODE_PURPOSE}:{email}:{code}".encode()
    return hmac.new(_jwt_secret().encode(), message, hashlib.sha256).hexdigest()


def _encode_purpose(purpose: str, email: str, ttl: timedelta, now: datetime | None, **extra) -> str:
    issued = now or now_kst()
    payload = {
        "purpose": purpose,
        "email": email,
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
        **extra,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def _decode_purpose(token: str, purpose: str, now: datetime | None) -> dict:
    options = {"require": ["purpose", "email", "exp"], "verify_exp": False}
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM], options=options)
    except jwt.PyJWTError:
        raise InvalidTokenError("유효하지 않은 토큰") from None
    if payload.get("purpose") != purpose or not isinstance(payload.get("email"), str):
        raise InvalidTokenError("유효하지 않은 토큰")
    current = (now or now_kst()).timestamp()
    if not isinstance(payload["exp"], int | float) or payload["exp"] <= current:
        raise ExpiredTokenError("만료된 토큰")
    return payload


def create_signup_code_token(email: str, code: str, *, now: datetime | None = None) -> str:
    return _encode_purpose(
        SIGNUP_CODE_PURPOSE, email, SIGNUP_CODE_TTL, now, mac=_code_mac(email, code)
    )


def read_signup_code_token(token: str, *, now: datetime | None = None) -> str:
    """서명·용도·만료를 확인하고 이메일을 돌려줍니다. 번호 확인은 `signup_code_matches`."""
    payload = _decode_purpose(token, SIGNUP_CODE_PURPOSE, now)
    if not isinstance(payload.get("mac"), str):
        raise InvalidTokenError("유효하지 않은 토큰")
    return payload["email"]


def signup_code_matches(token: str, code: str, *, now: datetime | None = None) -> bool:
    payload = _decode_purpose(token, SIGNUP_CODE_PURPOSE, now)
    return hmac.compare_digest(str(payload.get("mac", "")), _code_mac(payload["email"], code))


def create_signup_token(email: str, *, now: datetime | None = None) -> str:
    return _encode_purpose(SIGNUP_PURPOSE, email, SIGNUP_TOKEN_TTL, now)


def read_signup_token(token: str, *, now: datetime | None = None) -> str:
    """확인된 이메일. 가입 API는 본문의 이메일이 아니라 이 값으로 계정을 만듭니다."""
    return _decode_purpose(token, SIGNUP_PURPOSE, now)["email"]


# --- 알림 수신 거부 토큰 (7.4) -----------------------------------------------------
#
# 알림 메일의 수신 거부 링크에 넣습니다. 로그인 없이 알림만 끌 수 있고 다른 일은 할 수 없습니다.
# 오래된 메일의 링크도 동작해야 하므로 만료를 두지 않습니다. `purpose`가 달라 로그인 JWT·가입
# 토큰과 서로 바꿔 쓸 수 없습니다(로그인 JWT 검증은 `ver`·`exp`가 없는 이 토큰을 거절합니다).

UNSUBSCRIBE_PURPOSE = "unsubscribe"


def create_unsubscribe_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "purpose": UNSUBSCRIBE_PURPOSE}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def read_unsubscribe_token(token: str) -> int:
    """서명과 용도를 확인하고 user_id를 돌려줍니다. 틀리면 `InvalidTokenError`."""
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "purpose"], "verify_exp": False},
        )
        if payload["purpose"] != UNSUBSCRIBE_PURPOSE:
            raise InvalidTokenError("유효하지 않은 토큰")
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise InvalidTokenError("유효하지 않은 토큰") from None


class WindowRateLimiter:
    """규칙마다 (최대 횟수, 시간 창 초)를 두는 슬라이딩 윈도. `LoginRateLimiter`와 같은 방식."""

    def __init__(
        self,
        rules: dict[str, tuple[int, float]],
        detail: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rules = rules
        self._detail = detail
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, **keys: str) -> None:
        """규칙 이름별 키(예: `email=...`)로 1회 기록. 하나라도 넘으면 429."""
        with self._lock:
            now = self._clock()
            for rule, value in keys.items():
                limit, window = self._rules[rule]
                hits = self._hits.get((rule, value))
                if hits is None:
                    continue
                while hits and hits[0] <= now - window:
                    hits.popleft()
                if len(hits) >= limit:
                    retry_after = max(1, int(hits[0] + window - now) + 1)
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=self._detail,
                        headers={"Retry-After": str(retry_after)},
                    )
            for rule, value in keys.items():
                self._hits.setdefault((rule, value), deque()).append(now)
            self._prune(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            window = self._rules[key[0]][1]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if not hits:
                del self._hits[key]


# 발송: 같은 이메일 1분 1회·1시간 5회, 같은 IP 1시간 20회 (메일 폭탄 방지)
signup_code_limiter = WindowRateLimiter(
    {"email_minute": (1, 60.0), "email_hour": (5, 3600.0), "ip_hour": (20, 3600.0)},
    "인증번호를 너무 자주 요청했어요. 잠시 후 다시 시도하세요",
)
# 확인: 같은 이메일 분당 5회. 번호 유효 10분 동안 최대 50번이라 100만 가지를 맞힐 수 없습니다.
signup_verify_limiter = WindowRateLimiter(
    {"email": (5, 60.0)},
    "인증번호 확인을 너무 많이 시도했어요. 잠시 후 다시 시도하세요",
)


def check_signup_code_rate(request: Request, email: str) -> None:
    signup_code_limiter.hit(email_minute=email, email_hour=email, ip_hour=client_ip(request))


def check_signup_verify_rate(email: str) -> None:
    signup_verify_limiter.hit(email=email)
