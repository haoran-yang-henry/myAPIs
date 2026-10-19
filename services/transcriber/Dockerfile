# syntax=docker/dockerfile:1
# Build from the repo root so the workspace (uv.lock, libs/) is in the context:
#   docker build -f services/transcriber/Dockerfile .
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY . .
# Installs only this service and its dependencies (non-editable), not the whole workspace.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package transcriber

FROM python:${PYTHON_VERSION}-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# Fixed uid: the Cloud Storage mount in deploy.env is owned by uid/gid 1000.
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app:app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/data
USER app
# Cloud Run sets PORT; request logs come from myapis_common, so uvicorn's are off.
CMD ["sh", "-c", "exec uvicorn transcriber.main:app --host 0.0.0.0 --port $PORT --no-access-log"]
