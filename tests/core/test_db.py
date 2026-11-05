from app.core.db import get_engine


def test_engine_uses_psycopg_and_pre_ping(monkeypatch):
    """9.4: psycopg 3 드라이버, pool_pre_ping. 엔진 생성만으로는 DB에 연결하지 않습니다."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    engine = get_engine()
    assert engine.dialect.driver == "psycopg"
    assert engine.pool._pre_ping is True
