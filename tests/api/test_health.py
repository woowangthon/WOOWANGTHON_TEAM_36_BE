from app.core.db import get_engine


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_touch_db(client):
    """9.5: /health는 DB 연결 없이 200이고, 앱 시작 시에도 엔진을 만들지 않습니다."""
    client.get("/health")
    assert get_engine.cache_info().currsize == 0
