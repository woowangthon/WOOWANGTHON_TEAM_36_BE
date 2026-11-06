import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.db import get_engine, get_sessionmaker
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """테스트는 개발자의 `.env`와 셸 환경변수를 읽지 않습니다.

    로컬 `.env`에 실제 Google·Gemini 비밀값이 있어도 테스트 결과가 달라지거나 실제 서비스를
    호출하지 않게 하기 위함입니다. 값이 필요한 테스트는 `monkeypatch.setenv`로 직접 넣습니다.
    테스트 DB는 `TEST_DATABASE_URL`로 따로 지정합니다(tests/models/conftest.py).
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(autouse=True)
def _clear_caches(_isolated_settings):
    """테스트마다 환경변수를 새로 읽도록 설정·엔진 캐시를 비웁니다."""
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    yield
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
