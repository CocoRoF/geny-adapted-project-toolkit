# GAPT control plane image.
# Lives alongside the service; build with the repo root as context:
#   docker build -f server/Dockerfile -t gapt/server:dev .

# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.4.30

# ---------- stage: deps ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

ARG UV_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
        | tar -xz -C /usr/local/bin --strip-components=1 uv-x86_64-unknown-linux-gnu/uv

WORKDIR /opt/gapt-server
# Deps first for layer-cache friendliness; then the project itself.
COPY server/pyproject.toml server/uv.lock server/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY server/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- stage: runtime ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 1000 gapt

COPY --from=deps /opt/venv /opt/venv
# Keep the project at the same path the deps stage's editable install
# resolved against, so the venv's .pth still points to a real src tree.
COPY --from=deps --chown=gapt:gapt /opt/gapt-server /opt/gapt-server
WORKDIR /opt/gapt-server

# Migration assets live at the server root (not under src/), so copy
# them explicitly. The entrypoint runs `alembic upgrade head` against
# GAPT_POSTGRES_DSN before the server starts — a fresh deploy
# self-migrates instead of needing a manual upgrade step.
COPY --chown=gapt:gapt server/alembic.ini ./alembic.ini
COPY --chown=gapt:gapt server/migrations ./migrations
COPY --chmod=0755 server/docker-entrypoint.sh /usr/local/bin/gapt-entrypoint.sh

USER gapt
EXPOSE 8088

ENTRYPOINT ["/usr/local/bin/gapt-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "gapt_server.app:app", "--host", "0.0.0.0", "--port", "8088"]

LABEL org.opencontainers.image.title="gapt/server" \
      org.opencontainers.image.description="GAPT control plane (FastAPI)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/CocoRoF/geny-adapted-project-toolkit"
