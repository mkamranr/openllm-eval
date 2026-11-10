# syntax=docker/dockerfile:1
# Backend image: used by the api, worker, and migrate services.

ARG PYTHON_IMAGE=python:3.12.8-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.22

FROM ${UV_IMAGE} AS uv

# ---- build: resolve and install pinned dependencies from uv.lock ----
FROM ${PYTHON_IMAGE} AS build
# Windows single sign-on (Kerberos) needs the gssapi package, which is compiled against MIT
# krb5 here. Build with --build-arg WITH_KERBEROS=false (or WITH_KERBEROS=false in .env) to skip
# it; password sign-in with Active Directory works either way.
ARG WITH_KERBEROS=true
RUN if [ "$WITH_KERBEROS" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libkrb5-dev \
      && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_KERBEROS" = "true" ]; then EXTRA="--extra kerberos"; else EXTRA=""; fi; \
    uv sync --frozen --no-dev --no-install-project $EXTRA

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_KERBEROS" = "true" ]; then EXTRA="--extra kerberos"; else EXTRA=""; fi; \
    uv sync --frozen --no-dev --no-editable $EXTRA

# ---- runtime ----
FROM ${PYTHON_IMAGE} AS runtime
ARG WITH_KERBEROS=true
RUN if [ "$WITH_KERBEROS" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgssapi-krb5-2 \
      && rm -rf /var/lib/apt/lists/*; \
    fi
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
