# 빠른 시작 규칙(계획서 9.5): python:3.12-slim + 운영 의존성만 설치
# 이미지 저장소 무료 용량(0.5GB, 9.5)을 아끼려고 uv는 빌드 단계에만 둡니다.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN useradd --create-home --uid 1000 appuser
COPY --from=build /app/.venv /app/.venv
COPY app ./app

USER appuser

# Cloud Run은 PORT(기본 8080)를 넘겨 줍니다. 로컬 기본값은 8000(계획서 8.3).
# 접근 로그는 쿼리 문자열을 남기므로 끄고 앱의 요청 로그 미들웨어를 씁니다.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
