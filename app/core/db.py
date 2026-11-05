"""DB 엔진·세션 (계획서 9.4, 9.5).

빠른 시작 규칙: import 시점이나 앱 시작 시에는 엔진을 만들지 않고 DB에 연결하지 않습니다.
엔진은 첫 요청에서 get_engine()이 처음 불릴 때 만들어집니다.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    # Neon은 약 5분 쉬면 기존 연결을 끊으므로 pool_pre_ping을 켭니다(9.4).
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 의존성: 요청마다 세션을 열고 끝나면 닫습니다."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
