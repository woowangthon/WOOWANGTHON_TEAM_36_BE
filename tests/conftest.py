import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_caches():
    """테스트마다 환경변수를 새로 읽도록 설정·엔진 캐시를 비웁니다."""
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    yield
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
