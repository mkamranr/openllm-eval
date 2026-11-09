# syntax=docker/dockerfile:1
# Backend image: used by the api, worker, and migrate services.

ARG PYTHON_IMAGE=python:3.12.8-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.22

FROM ${UV_IMAGE} AS uv

# ---- build: resolve and install pinned dependencies from uv.lock ----
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime ----
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 openllm \
    && useradd --system --uid 10001 --gid openllm --home-dir /app --shell /usr/sbin/nologin openllm

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=openllm:openllm alembic.ini ./
COPY --chown=openllm:openllm app ./app

USER openllm
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
