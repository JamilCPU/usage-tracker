# Build context: the repository root (migrations/ lives there): docker build -f web/Dockerfile .
FROM node:22-alpine AS frontend
WORKDIR /src/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend ./
# vite.config.ts writes the bundle to ../src/usage_web/static
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY web/pyproject.toml web/uv.lock ./
COPY web/src ./src
COPY --from=frontend /src/web/src/usage_web/static ./src/usage_web/static
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
COPY migrations /app/migrations
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MIGRATIONS_DIR=/app/migrations
USER 10001
WORKDIR /app
EXPOSE 8080
CMD ["usage-web", "serve"]
