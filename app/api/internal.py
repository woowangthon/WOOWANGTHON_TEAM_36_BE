"""정기 수집 (계획서 9.3). Cloud Scheduler 전용, X-Cron-Token이 다르면 403.

수집을 마치면 같은 요청 안에서 하루 한 번 알림 메일을 보냅니다(7.4).
"""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import log_event
from app.schemas.api.admin import CrawlResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


FORBIDDEN = {403: {"description": "X-Cron-Token 불일치"}}


@router.post("/crawl", response_model=CrawlResponse, responses=FORBIDDEN)
def crawl(x_cron_token: Annotated[str | None, Header()] = None) -> CrawlResponse:
    """수집 → 추출 → 저장 → 마감 상태 갱신을 요청 안에서 끝까지 실행합니다.

    `BackgroundTasks`를 쓰지 않습니다(9.3). 다른 실행이 진행 중이면 skipped=true로 바로 끝납니다.
    """
    expected = get_settings().cron_token
    if (
        expected is None
        or x_cron_token is None
        or not secrets.compare_digest(x_cron_token.encode(), expected.get_secret_value().encode())
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    from app.scheduler.jobs import run_crawl  # 수집 의존성은 요청 때 불러옴(9.5)

    outcome = run_crawl()
    _notify()
    return CrawlResponse(skipped=outcome.skipped, runs=outcome.runs)


def _notify() -> None:
    """서울 9시 이후이고 오늘 아직 보내지 않았으면 알림 메일을 보냅니다(7.4, 9.3).

    수집이 건너뛰어졌어도 실행합니다. 알림이 실패해도 수집 응답은 실패로 만들지 않고,
    예외 종류만 로그에 남깁니다(메시지에 주소가 들어갈 수 있음). 다음 시각 수집이 다시 시도합니다.
    """
    from app.notifications.jobs import run_daily_notifications  # 요청 때 불러옴(9.5)

    try:
        run_daily_notifications()
    except Exception as error:
        log_event(logger, "알림 발송 작업 실패", logging.ERROR, error=type(error).__name__)
