"""구조화(JSON) 로그와 요청 로그 미들웨어 (계획서 4.2, 9.8).

로그 금지 항목: 프로필 값, 비밀번호, JWT, 쿠키, Google 토큰, 암호화 키.
- 요청 로그에는 method, path, status, duration_ms만 남깁니다.
- 쿼리 문자열(OAuth code·state 등이 들어갈 수 있음), 헤더, 쿠키, 본문은 남기지 않습니다.
- 사용자는 user_id로만 기록합니다. 추가 필드는 log_event()의 fields로 명시해서 넘깁니다.
"""

import json
import logging
import sys
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

SEOUL = ZoneInfo("Asia/Seoul")

request_logger = logging.getLogger("app.request")


class JsonFormatter(logging.Formatter):
    """Cloud Logging이 읽는 한 줄 JSON 형식 (severity, message, time + fields)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "time": datetime.fromtimestamp(record.created, tz=SEOUL).isoformat(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            # 예외 메시지에 민감값을 넣지 않는 것은 호출하는 쪽 규칙입니다(backend CLAUDE.md).
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn 기본 로그도 같은 JSON 형식으로 내보냅니다.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # uvicorn 접근 로그는 쿼리 문자열까지 남기므로 끄고, 아래 미들웨어 로그로 대신합니다.
    logging.getLogger("uvicorn.access").disabled = True


def log_event(logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any):
    logger.log(level, message, extra={"fields": fields})


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging(request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            log_event(
                request_logger,
                "request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
