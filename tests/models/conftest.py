"""DB가 필요한 테스트용 fixture.

compose의 postgres(localhost:5432)에 테스트 전용 DB(scholarship_test)를 만들어 씁니다.
다른 DB를 쓰려면 TEST_DATABASE_URL을 지정합니다. DB에 연결할 수 없으면 이유를 남기고 건너뜁니다
(`docker compose up -d postgres` 후 다시 실행).
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEST_URL = "postgresql+psycopg://scholarship:scholarship@localhost:5432/scholarship_test"


def _test_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_url()
    try:
        _ensure_database(url)
    except Exception as exc:  # 연결 실패: 원인을 보여 주고 건너뜀
        pytest.skip(
            f"테스트 DB에 연결할 수 없습니다({type(exc).__name__}). compose postgres를 켜세요."
        )
    return url


@pytest.fixture(scope="session")
def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False  # pytest 로그 설정을 덮어쓰지 않음
    return cfg


@pytest.fixture(scope="session")
def migrated_engine(db_url: str, alembic_config: Config):
    """빈 DB에서 upgrade head까지 적용한 엔진. 끝나면 base로 되돌립니다."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    engine = create_engine(db_url)
    yield engine
    engine.dispose()
    command.downgrade(alembic_config, "base")


@pytest.fixture
def conn(migrated_engine):
    """테스트마다 트랜잭션을 열고 끝나면 롤백해 데이터를 남기지 않습니다."""
    with migrated_engine.connect() as connection:
        trans = connection.begin()
        yield connection
        trans.rollback()
