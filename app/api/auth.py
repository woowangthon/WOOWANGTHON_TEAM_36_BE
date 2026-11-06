"""이메일 가입·로그인, 동의, 내 정보, 탈퇴 (계획서 7.2, 7.3, 4.2).

비밀번호·JWT·쿠키 값은 로그에 남기지 않고, 사용자는 `user_id`로만 기록합니다.
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_event
from app.core.security import (
    CurrentUser,
    check_login_rate,
    clear_auth_cookie,
    hash_password,
    is_admin,
    issue_session,
    verify_password,
)
from app.models import User, UserProfile
from app.parsers.dates import now_kst
from app.schemas.api.auth import ConsentRequest, LoginRequest, MeResponse, SignupRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[Session, Depends(get_db)]

UNAUTHORIZED = {401: {"description": "로그인이 필요함"}}
INVALID_CREDENTIALS = "이메일 또는 비밀번호가 올바르지 않습니다"
GOOGLE_ONLY = "Google 계정으로 가입한 이메일입니다. Google로 로그인하세요"


@lru_cache
def _dummy_hash() -> str:
    """없는 이메일도 비밀번호 확인만큼 시간이 걸리게 해서 응답 시간으로 가입 여부를 숨깁니다.

    앱 시작을 늦추지 않도록(9.5) 첫 로그인 때 만듭니다.
    """
    return hash_password("timing-equalizer-not-a-real-password")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _me(db: Session, user: User) -> MeResponse:
    has_profile = db.scalar(select(func.count()).where(UserProfile.user_id == user.id)) > 0
    return MeResponse(
        id=user.id,
        email=user.email,
        privacy_agreed=user.privacy_agreed_at is not None,
        sensitive_agreed=user.sensitive_agreed_at is not None,
        profile_completed=has_profile,
        is_admin=is_admin(user),
        google_linked=user.google_sub is not None,
        has_password=user.password_hash is not None,
    )


def _clear_special_flags(db: Session, user_id: int) -> None:
    """민감정보(특수자격) 값을 즉시 삭제합니다(4.2)."""
    db.execute(
        update(UserProfile).where(UserProfile.user_id == user_id).values(enc_special_flags=None)
    )


@router.post(
    "/signup",
    response_model=MeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "이미 가입된 이메일"}},
)
def signup(body: SignupRequest, response: Response, db: DB) -> MeResponse:
    """가입하고 JWT 쿠키를 발급합니다. 메일 발송이 없어 이메일 소유 확인은 하지 않습니다(7.3)."""
    email = normalize_email(body.email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다")
    user = User(email=email, password_hash=hash_password(body.password), token_version=0)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # 동시에 같은 이메일로 가입
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다"
        ) from None
    issue_session(response, user)
    log_event(logger, "가입", user_id=user.id)
    return _me(db, user)


@router.post(
    "/login",
    response_model=MeResponse,
    responses={
        400: {"description": "Google 전용 계정"},
        401: {"description": "이메일 또는 비밀번호 불일치"},
        429: {"description": "로그인 시도 제한 초과"},
    },
)
def login(body: LoginRequest, request: Request, response: Response, db: DB) -> MeResponse:
    """로그인하고 JWT 쿠키를 발급합니다. 시도 제한을 넘으면 429(7.3)."""
    email = normalize_email(body.email)
    check_login_rate(request, email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(body.password, _dummy_hash())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)
    if user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=GOOGLE_ONLY)
    if not verify_password(body.password, user.password_hash):
        log_event(logger, "로그인 실패", logging.WARNING, user_id=user.id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)
    issue_session(response, user)
    log_event(logger, "로그인", user_id=user.id)
    return _me(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    """JWT 쿠키를 지웁니다. 로그인하지 않았어도 204."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response


@router.post(
    "/consent",
    response_model=MeResponse,
    responses={**UNAUTHORIZED, 400: {"description": "개인정보 동의 없음"}},
)
def consent(body: ConsentRequest, user: CurrentUser, db: DB) -> MeResponse:
    """개인정보(필수)·민감정보(선택) 동의를 저장합니다(4.2).

    민감정보를 동의하지 않음으로 보내면 철회와 같게 처리해 특수자격 값을 지웁니다.
    """
    if not body.privacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="개인정보 수집·이용 동의가 필요합니다"
        )
    now = now_kst()
    user.privacy_agreed_at = user.privacy_agreed_at or now
    if body.sensitive:
        user.sensitive_agreed_at = user.sensitive_agreed_at or now
    else:
        user.sensitive_agreed_at = None
        _clear_special_flags(db, user.id)
    db.commit()
    log_event(logger, "동의 저장", user_id=user.id)
    return _me(db, user)


@router.delete("/consent/sensitive", status_code=status.HTTP_204_NO_CONTENT, responses=UNAUTHORIZED)
def withdraw_sensitive_consent(user: CurrentUser, db: DB) -> None:
    """민감정보 동의를 철회하고 특수자격 값을 즉시 지웁니다(4.2)."""
    user.sensitive_agreed_at = None
    _clear_special_flags(db, user.id)
    db.commit()
    log_event(logger, "민감정보 동의 철회", user_id=user.id)


@router.get("/me", response_model=MeResponse, responses=UNAUTHORIZED)
def me(user: CurrentUser, db: DB) -> MeResponse:
    """로그인 사용자 (동의 여부, 프로필 입력 여부, 관리자 여부 포함)."""
    return _me(db, user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, responses=UNAUTHORIZED)
def delete_me(user: CurrentUser, db: DB) -> Response:
    """회원 탈퇴: 계정·프로필·관심/신청완료 기록을 즉시 삭제합니다(4.2, FK CASCADE)."""
    user_id = user.id
    db.expunge(user)
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    log_event(logger, "회원 탈퇴", user_id=user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response
