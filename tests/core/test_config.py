from app.core.config import LOCAL_DATABASE_URL, get_settings

ENV_NAMES = [
    "DATABASE_URL",
    "DATABASE_URL_DIRECT",
    "JWT_SECRET",
    "FIELD_ENCRYPTION_KEY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "AI_MOCK",
    "CRON_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "ADMIN_EMAILS",
]


def _clear_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir("/")  # .env 파일을 읽지 않게 합니다.


def test_defaults_without_env(monkeypatch):
    _clear_env(monkeypatch)
    settings = get_settings()
    assert settings.database_url == LOCAL_DATABASE_URL
    assert settings.migration_database_url == LOCAL_DATABASE_URL
    assert settings.ai_mock is False
    assert settings.admin_email_list == []


def test_reads_all_env_vars(monkeypatch):
    _clear_env(monkeypatch)
    values = {
        "DATABASE_URL": "postgresql+psycopg://u:p@db-pooler.example/app",
        "DATABASE_URL_DIRECT": "postgresql+psycopg://u:p@db.example/app",
        "JWT_SECRET": "jwt-test",
        "FIELD_ENCRYPTION_KEY": "key-test",
        "GEMINI_API_KEY": "gemini-test",
        "GEMINI_MODEL": "gemini-flash-test",
        "AI_MOCK": "1",
        "CRON_TOKEN": "cron-test",
        "GOOGLE_CLIENT_ID": "client-id",
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:5173/api/auth/google/callback",
        "ADMIN_EMAILS": " Admin@Example.com, b@example.com ,",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    s = get_settings()
    assert s.database_url == values["DATABASE_URL"]
    assert s.migration_database_url == values["DATABASE_URL_DIRECT"]
    assert s.jwt_secret.get_secret_value() == "jwt-test"
    assert s.field_encryption_key.get_secret_value() == "key-test"
    assert s.gemini_api_key.get_secret_value() == "gemini-test"
    assert s.gemini_model == "gemini-flash-test"
    assert s.ai_mock is True
    assert s.cron_token.get_secret_value() == "cron-test"
    assert s.google_client_id == "client-id"
    assert s.google_client_secret.get_secret_value() == "client-secret"
    assert s.google_redirect_uri == values["GOOGLE_REDIRECT_URI"]
    assert s.admin_email_list == ["admin@example.com", "b@example.com"]


def test_secrets_hidden_in_repr(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "do-not-print-me")
    assert "do-not-print-me" not in repr(get_settings())
