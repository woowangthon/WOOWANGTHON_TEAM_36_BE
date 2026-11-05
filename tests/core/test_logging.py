import json
import logging

from app.core.logging import JsonFormatter


def _request_records(caplog):
    return [r for r in caplog.records if r.name == "app.request"]


def test_request_log_has_required_fields(client, caplog):
    caplog.set_level(logging.INFO, logger="app.request")
    client.get("/health")

    (record,) = _request_records(caplog)
    assert record.fields["method"] == "GET"
    assert record.fields["path"] == "/health"
    assert record.fields["status"] == 200
    assert isinstance(record.fields["duration_ms"], float)


def test_request_log_excludes_query_cookies_and_headers(client, caplog):
    """4.2 로그 금지 항목: 쿼리 문자열·쿠키·인증 헤더 값을 남기지 않습니다."""
    caplog.set_level(logging.INFO, logger="app.request")
    client.cookies.set("access_token", "jwt-cookie-value")
    client.get(
        "/health?code=oauth-code-value&state=state-value",
        headers={"Authorization": "Bearer header-token-value"},
    )

    (record,) = _request_records(caplog)
    assert set(record.fields) == {"method", "path", "status", "duration_ms"}
    line = JsonFormatter().format(record)
    for secret in ("oauth-code-value", "state-value", "jwt-cookie-value", "header-token-value"):
        assert secret not in line


def test_unknown_path_logged_with_status(client, caplog):
    caplog.set_level(logging.INFO, logger="app.request")
    client.get("/no-such-path")
    (record,) = _request_records(caplog)
    assert record.fields["status"] == 404


def test_json_formatter_outputs_one_json_line_in_seoul_time():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.fields = {"user_id": 42}
    data = json.loads(JsonFormatter().format(record))
    assert data["severity"] == "INFO"
    assert data["message"] == "hello world"
    assert data["user_id"] == 42
    assert data["time"].endswith("+09:00")
