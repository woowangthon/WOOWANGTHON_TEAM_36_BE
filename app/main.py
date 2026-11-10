"""FastAPI 앱 진입점.

빠른 시작 규칙(계획서 9.5): 시작 시 DB 연결이나 데이터 미리 불러오기를 하지 않습니다.
CORS는 설정하지 않습니다(Vercel 프록시로 같은 출처, 계획서 9.2).
"""

from fastapi import FastAPI

from app.api import admin, auth, google, health, internal, profile, scholarships
from app.core.errors import install_validation_errors
from app.core.logging import configure_logging, install_request_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Scholarship Matching API", version="0.1.0")
    install_request_logging(app)
    install_validation_errors(app)
    for module in (health, auth, google, profile, scholarships, admin, internal):
        app.include_router(module.router)
    return app


app = create_app()
