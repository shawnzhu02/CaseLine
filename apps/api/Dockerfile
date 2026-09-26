# CaseLine API / outbox worker image. Build context: apps/api
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY caseline ./caseline
RUN pip install . && useradd --create-home --uid 10001 caseline
COPY alembic.ini ./
COPY alembic ./alembic

USER caseline
EXPOSE 8000
# Render sets $PORT. Migrations run as the pre-deploy command (see render.yaml), not at container start.
CMD ["sh", "-c", "uvicorn caseline.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
