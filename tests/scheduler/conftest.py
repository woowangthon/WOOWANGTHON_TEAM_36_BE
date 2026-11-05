"""수집 작업 테스트용 fixture. DB는 tests/models/conftest.py의 테스트 DB를 같이 씁니다."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.models.conftest import alembic_config, db_url, migrated_engine  # noqa: F401


@pytest.fixture
def session_factory(migrated_engine):  # noqa: F811
    """테스트마다 수집 관련 테이블을 비웁니다(수집 작업은 직접 커밋하므로 롤백으로 못 지움)."""
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE user_scholarship_states, scholarships, notices, crawl_runs "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield sessionmaker(bind=migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE user_scholarship_states, scholarships, notices, crawl_runs "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(autouse=True)
def _mock_ai(monkeypatch):
    monkeypatch.setenv("AI_MOCK", "1")
