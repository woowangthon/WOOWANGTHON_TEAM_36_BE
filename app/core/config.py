"""환경변수 설정 (계획서 9.6).

비밀값은 SecretStr로 받아 repr·로그에 값이 드러나지 않게 합니다.
비밀값이 실제로 필요한 기능(인증, 암호화, Gemini 호출 등)은 해당 카드에서 값이 있는지 확인합니다.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://scholarship:scholarship@localhost:5432/scholarship"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB (9.4): 앱은 pooled, 마이그레이션은 direct 연결
    database_url: str = LOCAL_DATABASE_URL
    database_url_direct: str | None = None

    # 인증·암호화
    jwt_secret: SecretStr | None = None
    field_encryption_key: SecretStr | None = None

    # AI 추출
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    ai_mock: bool = False  # 로컬 전용: 1이면 Gemini 대신 목 응답

    # 정기 수집 (9.3)
    cron_token: SecretStr | None = None

    # Google 로그인 (7.3)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None
    admin_emails: str = ""

    @property
    def migration_database_url(self) -> str:
        """Alembic이 쓸 연결 문자열. direct가 없으면(로컬) 앱과 같은 주소를 씁니다."""
        return self.database_url_direct or self.database_url

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
