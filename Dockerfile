FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM python:3.12-slim AS python-dependencies

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/backend

RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend

RUN useradd --create-home --uid 10001 appuser
COPY --from=python-dependencies /opt/venv /opt/venv
COPY --chown=appuser:appuser backend/app ./app
COPY --from=frontend-build --chown=appuser:appuser /frontend/dist ./app/static

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
