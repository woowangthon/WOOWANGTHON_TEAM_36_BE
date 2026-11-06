"""Google 로그인 (계획서 7.3). Authorization Code + PKCE를 서버에서 처리합니다.

흐름
1. `/auth/google/login`: `state`·`code_verifier`·이동 주소를 서명한 짧은 httpOnly 쿠키에 담고
   Google 로그인 화면으로 이동
2. `/auth/google/callback`: 쿠키의 `state`와 비교 → `code` + `code_verifier`로 토큰 교환 →
   ID 토큰의 서명·`aud`·`iss`·`exp`·`email_verified` 검증 → 계정 찾기·연결·생성 → JWT 쿠키 발급

- redirect_uri는 요청 호스트가 아니라 `GOOGLE_REDIRECT_URI`로 만듭니다(콜백도 Vercel 프록시를 거침).
- 로그인 후 이동 주소는 내부 경로(`/`로 시작)만 허용합니다(오픈 리다이렉트 방지).
- 두 경로 모두 본문 없이 화면으로 이동(302)합니다. 실패하면 로그인 화면에 `error=google`을 붙입니다.
- Google 토큰·코드·쿠키 값은 로그에 남기지 않습니다. 사용자는 `user_id`로만 기록합니다.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
import jwt
from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import log_event
from app.core.security import JWT_ALGORITHM, issue_session
from app.models import User
from app.parsers.dates import now_kst

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])

DB = Annotated[Session, Depends(get_db)]
REDIRECT = {302: {"description": "다른 주소로 이동"}}

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email profile"  # 민감하지 않은 기본 권한만 (7.3)

# 프론트 화면 경로 (7.1)
DEFAULT_NEXT = "/dashboard"
CONSENT_PATH = "/consent"
LOGIN_ERROR_PATH = "/login?error=google"

FLOW_COOKIE = "google_oauth"
FLOW_TTL = timedelta(minutes=10)
FLOW_AUDIENCE = "google-oauth-flow"


class GoogleAuthError(Exception):
    """토큰 교환·ID 토큰 검증 실패. 메시지에 토큰 값을 넣지 않습니다."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str


# --- Google과 통신 ----------------------------------------------------------------


class GoogleOAuth:
    """Google OAuth 클라이언트. 테스트에서는 `transport`·`jwks`를 바꿔 네트워크 없이 씁니다."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.BaseTransport | None = None,
        jwks: Any = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._transport = transport
        self._jwks = jwks

    def authorization_url(self, state: str, code_verifier: str) -> str:
        query = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPE,
            "state": state,
            "code_challenge": create_s256_code_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    def exchange_code(self, code: str, code_verifier: str) -> str:
        """코드를 ID 토큰으로 바꿉니다."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self._client_secret,
        }
        try:
            with httpx.Client(transport=self._transport, timeout=10.0) as client:
                response = client.post(TOKEN_URL, data=data)
        except httpx.HTTPError:
            raise GoogleAuthError("토큰 교환 요청 실패") from None
        if response.status_code != 200:
            raise GoogleAuthError(f"토큰 교환 실패: HTTP {response.status_code}")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise GoogleAuthError("응답에 ID 토큰이 없습니다")
        return id_token

    def verify_id_token(self, id_token: str) -> GoogleIdentity:
        """서명(Google 공개키)·`aud`·`iss`·`exp`·`email_verified`를 확인합니다."""
        try:
            key = self._signing_keys().get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=ISSUERS,
                options={"require": ["sub", "aud", "iss", "exp", "iat"]},
                leeway=30,
            )
        except (jwt.PyJWTError, httpx.HTTPError):
            raise GoogleAuthError("ID 토큰 검증 실패") from None
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str):
            raise GoogleAuthError("확인된 이메일이 아닙니다")
        return GoogleIdentity(sub=str(claims["sub"]), email=email.strip().lower())

    def _signing_keys(self) -> Any:
        return self._jwks if self._jwks is not None else _google_jwks()


@lru_cache
def _google_jwks() -> jwt.PyJWKClient:
    """Google 공개키 목록. 인스턴스 안에서 캐시합니다(첫 Google 로그인 때 만듦)."""
    return jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


def get_google_oauth() -> GoogleOAuth:
    settings = get_settings()
    secret = settings.google_client_secret
    if not (settings.google_client_id and secret and settings.google_redirect_uri):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google 로그인이 설정되지 않았습니다",
        )
    return GoogleOAuth(
        client_id=settings.google_client_id,
        client_secret=secret.get_secret_value(),
        redirect_uri=settings.google_redirect_uri,
    )


Google = Annotated[GoogleOAuth, Depends(get_google_oauth)]


# --- 이동 주소·흐름 쿠키 --------------------------------------------------------------


def safe_next(next_path: str | None) -> str:
    """내부 경로만 허용합니다. `//evil.com`, `/\\evil.com`, `https://...`는 기본 화면으로."""
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith(("//", "/\\"))
        or any(c in next_path for c in "\\\r\n\t")
        or len(next_path) > 512
    ):
        return DEFAULT_NEXT
    return next_path


def _flow_secret() -> str:
    secret = get_settings().jwt_secret
    if secret is None:
        raise RuntimeError("JWT_SECRET이 설정되지 않았습니다")
    return secret.get_secret_value()


def _encode_flow(state: str, code_verifier: str, next_path: str) -> str:
    """흐름 값을 서명해 쿠키에 담습니다. 브라우저에서 바꾸면 콜백에서 거부됩니다."""
    now = now_kst()
    payload = {
        "aud": FLOW_AUDIENCE,
        "state": state,
        "cv": code_verifier,
        "next": next_path,
        "iat": int(now.timestamp()),
        "exp": int((now + FLOW_TTL).timestamp()),
    }
    return jwt.encode(payload, _flow_secret(), algorithm=JWT_ALGORITHM)


def _decode_flow(value: str) -> dict[str, str] | None:
    try:
        payload = jwt.decode(
            value, _flow_secret(), algorithms=[JWT_ALGORITHM], audience=FLOW_AUDIENCE
        )
    except jwt.PyJWTError:
        return None
    if not all(isinstance(payload.get(k), str) for k in ("state", "cv", "next")):
        return None
    return payload


def _clear_flow_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(FLOW_COOKIE, path="/", httponly=True, secure=True, samesite="lax")


def _fail(reason: str) -> RedirectResponse:
    log_event(logger, "Google 로그인 실패", logging.WARNING, reason=reason)
    response = RedirectResponse(LOGIN_ERROR_PATH, status_code=status.HTTP_302_FOUND)
    _clear_flow_cookie(response)
    return response


# --- 계정 찾기·연결·생성 (7.3) -------------------------------------------------------


def find_or_link_user(db: Session, identity: GoogleIdentity) -> User | None:
    """1. 같은 `google_sub` → 그 계정
    2. 같은 이메일 → 연결. 기존 비밀번호를 무효화하고 `token_version`을 올려 기존 세션을 끊음
    3. 둘 다 없으면 새 계정(`password_hash = NULL`)

    `email_verified`는 `verify_id_token`에서 이미 확인했습니다. 동시 가입 충돌이면 None.
    """
    user = db.scalar(select(User).where(User.google_sub == identity.sub))
    if user is not None:
        log_event(logger, "Google 로그인", user_id=user.id)
        return user

    user = db.scalar(select(User).where(User.email == identity.email))
    if user is not None:
        if user.google_sub is not None:  # 같은 이메일이 다른 Google 계정에 이미 연결됨
            return None
        user.google_sub = identity.sub
        user.password_hash = None
        user.token_version += 1
        db.commit()
        log_event(logger, "Google 계정 연결", user_id=user.id)
        return user

    user = User(email=identity.email, google_sub=identity.sub, password_hash=None, token_version=0)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    log_event(logger, "Google 가입", user_id=user.id)
    return user


# --- 엔드포인트 -------------------------------------------------------------------


@router.get(
    "/login",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses={**REDIRECT, 503: {"description": "Google 로그인 미설정"}},
)
def google_login(google: Google, next: str | None = None) -> RedirectResponse:
    """state·PKCE를 만들고 Google 로그인 화면으로 이동합니다. next는 내부 경로만 허용."""
    state = generate_token(32)
    code_verifier = generate_token(64)
    response = RedirectResponse(
        google.authorization_url(state, code_verifier), status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        FLOW_COOKIE,
        _encode_flow(state, code_verifier, safe_next(next)),
        max_age=int(FLOW_TTL.total_seconds()),
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",  # Google에서 돌아오는 최상위 GET 이동에는 쿠키가 실림
    )
    return response


@router.get(
    "/callback",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses={**REDIRECT, 503: {"description": "Google 로그인 미설정"}},
)
def google_callback(
    request: Request,
    google: Google,
    db: DB,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """코드 교환 → ID 토큰 검증 → JWT 쿠키 발급 후 화면으로 이동합니다.

    동의 전이면 동의 화면, 아니면 로그인 전에 요청한 내부 경로(기본 대시보드)로 갑니다.
    """
    flow = _decode_flow(request.cookies.get(FLOW_COOKIE, ""))
    if flow is None:
        return _fail("흐름 쿠키 없음")
    if not state or not secrets.compare_digest(state, flow["state"]):
        return _fail("state 불일치")
    if not code:  # 사용자가 Google 동의를 취소함(error=access_denied)
        return _fail("코드 없음")

    try:
        identity = google.verify_id_token(google.exchange_code(code, flow["cv"]))
    except GoogleAuthError as exc:
        return _fail(str(exc))

    user = find_or_link_user(db, identity)
    if user is None:
        return _fail("계정 충돌")

    target = CONSENT_PATH if user.privacy_agreed_at is None else safe_next(flow["next"])
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    _clear_flow_cookie(response)
    issue_session(response, user)
    return response
