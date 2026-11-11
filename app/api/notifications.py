"""알림 메일 수신 거부 (계획서 7.2, 7.4).

메일 하단의 링크는 프론트 `/unsubscribe?token=...` 화면으로 가고, 그 화면의 버튼이 이 API를
부릅니다. 메일 보안 검사기가 링크를 미리 열어도 설정이 바뀌지 않도록 GET으로는 끄지 않습니다.

로그인 없이 동작합니다. 토큰은 `JWT_SECRET`으로 서명돼 있고 알림을 끄는 데만 쓸 수 있습니다
(`core/security.py`). 토큰은 로그에 남기지 않습니다.
로그인한 사용자의 켜기·끄기는 `PUT /auth/me/notifications`입니다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_event
from app.core.security import InvalidTokenError, read_unsubscribe_token
from app.models import User
from app.schemas.api.notifications import UnsubscribeRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

DB = Annotated[Session, Depends(get_db)]
INVALID_LINK = "유효하지 않은 링크입니다"


@router.post(
    "/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"description": "토큰이 올바르지 않음"}},
)
def unsubscribe(body: UnsubscribeRequest, db: DB) -> Response:
    """알림 메일 수신을 끕니다. 이미 껐거나 탈퇴한 계정이어도 204입니다(가입 여부를 숨김)."""
    try:
        user_id = read_unsubscribe_token(body.token)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK) from None
    db.execute(update(User).where(User.id == user_id).values(email_notifications=False))
    db.commit()
    log_event(logger, "알림 수신 거부", user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
