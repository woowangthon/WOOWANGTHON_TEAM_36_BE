"""이메일 가입·로그인, 동의, 내 정보, 탈퇴 (계획서 7.2, 7.3, 4.2).

비밀번호·JWT·쿠키 값은 로그에 남기지 않고, 사용자는 `user_id`로만 기록합니다.
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_event
from app.core.mail import MailError, send_signup_code
from app.core.security import (
    SIGNUP_CODE_TTL,
    SIGNUP_TOKEN_TTL,
    CurrentUser,
    ExpiredTokenError,
    InvalidTokenError,
    check_login_rate,
    check_password_change_rate,
    check_signup_code_rate,
    check_signup_verify_rate,
    clear_auth_cookie,
    create_signup_code_token,
    create_signup_token,
    hash_password,
    is_admin,
    issue_session,
    new_signup_code,
    read_signup_code_token,
    read_signup_token,
    signup_code_matches,
    verify_password,
)
from app.models import User, UserProfile
from app.parsers.dates import now_kst
from app.schemas.api.auth import (
    ConsentRequest,
    LoginRequest,
    MeResponse,
    NotificationSettingRequest,
    PasswordChangeRequest,
    SignupCodeRequest,
    SignupCodeResponse,
    SignupRequest,
    SignupVerifyRequest,
    SignupVerifyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[Session, Depends(get_db)]

UNAUTHORIZED = {401: {"description": "로그인이 필요함"}}
INVALID_CREDENTIALS = "이메일 또는 비밀번호가 올바르지 않습니다"
GOOGLE_ONLY = "Google 계정으로 가입한 이메일입니다. Google로 로그인하세요"
ALREADY_SIGNED_UP = "이미 가입된 이메일입니다"
CODE_MISMATCH = "인증번호가 맞지 않아요"
CODE_EXPIRED = "인증번호가 만료됐어요. 다시 받아 주세요"
SIGNUP_EXPIRED = "인증 시간이 지났어요. 이메일 인증을 다시 해 주세요"
VERIFY_AGAIN = "이메일 인증을 처음부터 다시 해 주세요"
NO_PASSWORD = "Google 계정으로 로그인하는 계정이에요"
WRONG_CURRENT_PASSWORD = "현재 비밀번호가 맞지 않아요"
SAME_PASSWORD = "지금과 다른 비밀번호를 입력하세요"
MAIL_FAILED = "메일을 보내지 못했어요. 잠시 후 다시 시도하세요"
RESEND_AFTER_SECONDS = 60


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


@lru_cache
def _dummy_hash() -> str:
    """없는 이메일도 비밀번호 확인만큼 시간이 걸리게 해서 응답 시간으로 가입 여부를 숨깁니다.

    앱 시작을 늦추지 않도록(9.5) 첫 로그인 때 만듭니다.
    """
    return hash_password("timing-equalizer-not-a-real-password")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _me(db: Session, user: User) -> MeResponse:
    profile = db.get(UserProfile, user.id)  # 조회 1번 (예전 count 쿼리 대신)
    return MeResponse(
        id=user.id,
        email=user.email,
        privacy_agreed=user.privacy_agreed_at is not None,
        sensitive_agreed=user.sensitive_agreed_at is not None,
        profile_completed=profile is not None,
        is_admin=is_admin(user),
        google_linked=user.google_sub is not None,
        has_password=user.password_hash is not None,
        nickname=profile.name if profile else None,
        completed_semesters=profile.completed_semesters if profile else None,
        is_transfer=profile.is_transfer if profile else None,
        gpa=profile.gpa if profile else None,
        email_notifications=user.email_notifications,
    )


def _clear_special_flags(db: Session, user_id: int) -> None:
    """민감정보(특수자격) 값을 즉시 삭제합니다(4.2)."""
    db.execute(
        update(UserProfile).where(UserProfile.user_id == user_id).values(enc_special_flags=None)
    )


@router.post(
    "/signup/code",
    response_model=SignupCodeResponse,
    responses={
        409: {"description": "이미 가입된 이메일"},
        429: {"description": "발송 제한 초과"},
        502: {"description": "메일 발송 실패"},
    },
)
def signup_code(body: SignupCodeRequest, request: Request, db: DB) -> SignupCodeResponse:
    """가입 1단계: 6자리 인증번호를 메일로 보냅니다(7.3). 번호는 저장하지 않습니다."""
    email = normalize_email(body.email)
    check_signup_code_rate(request, email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SIGNED_UP)
    code = new_signup_code()
    try:
        send_signup_code(email, code)
    except MailError as exc:
        # 주소·번호 없이 실패 종류만 남깁니다(4.2).
        log_event(logger, "인증 메일 발송 실패", logging.ERROR, reason=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=MAIL_FAILED) from None
    log_event(logger, "인증 메일 발송")
    return SignupCodeResponse(
        code_token=create_signup_code_token(email, code),
        expires_in=int(SIGNUP_CODE_TTL.total_seconds()),
        resend_after=RESEND_AFTER_SECONDS,
    )


@router.post(
    "/signup/verify",
    response_model=SignupVerifyResponse,
    responses={
        400: {"description": "인증번호 불일치·만료"},
        429: {"description": "확인 시도 제한 초과"},
    },
)
def signup_verify(body: SignupVerifyRequest) -> SignupVerifyResponse:
    """가입 2단계: 번호가 맞으면 30분 동안 가입할 수 있는 `signup_token`을 줍니다(7.3)."""
    try:
        email = read_signup_code_token(body.code_token)
    except ExpiredTokenError:
        raise _bad_request(CODE_EXPIRED) from None
    except InvalidTokenError:
        raise _bad_request(VERIFY_AGAIN) from None
    check_signup_verify_rate(email)
    if not signup_code_matches(body.code_token, body.code):
        raise _bad_request(CODE_MISMATCH)
    return SignupVerifyResponse(
        signup_token=create_signup_token(email),
        expires_in=int(SIGNUP_TOKEN_TTL.total_seconds()),
    )


@router.post(
    "/signup",
    response_model=MeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "인증이 만료됐거나 유효하지 않음"},
        409: {"description": "이미 가입된 이메일"},
    },
)
def signup(body: SignupRequest, response: Response, db: DB) -> MeResponse:
    """가입 3단계: 인증번호로 확인한 이메일로 계정을 만들고 JWT 쿠키를 발급합니다(7.3)."""
    try:
        email = read_signup_token(body.signup_token)
    except ExpiredTokenError:
        raise _bad_request(SIGNUP_EXPIRED) from None
    except InvalidTokenError:
        raise _bad_request(VERIFY_AGAIN) from None
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SIGNED_UP)
    user = User(email=email, password_hash=hash_password(body.password), token_version=0)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # 동시에 같은 이메일로 가입
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SIGNED_UP
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


@router.put("/me/notifications", response_model=MeResponse, responses=UNAUTHORIZED)
def set_notifications(body: NotificationSettingRequest, user: CurrentUser, db: DB) -> MeResponse:
    """장학금 알림 메일 수신을 켜거나 끕니다(7.4). 동의 전에도 끌 수 있습니다."""
    user.email_notifications = body.email_notifications
    db.commit()
    return _me(db, user)


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **UNAUTHORIZED,
        400: {"description": "비밀번호 없는 계정, 현재 비밀번호 불일치, 같은 비밀번호"},
        429: {"description": "변경 시도 제한 초과"},
    },
)
def change_password(body: PasswordChangeRequest, user: CurrentUser, db: DB) -> Response:
    """비밀번호를 바꿉니다(T37). `token_version`을 올려 다른 기기의 로그인을 끊고,
    이 기기에는 새 쿠키를 줘서 로그인을 유지합니다(7.3).
    """
    check_password_change_rate(user.id)
    if user.password_hash is None:
        raise _bad_request(NO_PASSWORD)
    if not verify_password(body.current_password, user.password_hash):
        log_event(logger, "비밀번호 변경 실패", logging.WARNING, user_id=user.id)
        raise _bad_request(WRONG_CURRENT_PASSWORD)
    if body.new_password == body.current_password:
        raise _bad_request(SAME_PASSWORD)
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    issue_session(response, user)
    log_event(logger, "비밀번호 변경", user_id=user.id)
    return response


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
