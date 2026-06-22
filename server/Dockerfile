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
        git \
        curl \
        bash \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 1000 gapt

# Claude Code CLI — the LLM-backends auth modal runs `claude auth login`
# / `claude auth status` IN this container (the device-code flow the
# operator drives from the UI). The native installer drops a
# self-contained binary; copy it to a world-exec path so the non-root
# `gapt` user can run it, then drop the installer's per-user tree. (The
# agent itself runs claude INSIDE the workspace sandbox — this binary
# is only for the server-side auth flow + health check.)
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
    && chmod 0755 /usr/local/bin/claude \
    && rm -rf /root/.local \
    && /usr/local/bin/claude --version

COPY --from=deps /opt/venv /opt/venv
# Keep the project at the same path the deps stage's editable install
# resolved against, so the venv's .pth still points to a real src tree.
COPY --from=deps --chown=gapt:gapt /opt/gapt-server /opt/gapt-server
# Docker CLI — GAPT drives workspace sandboxes by shelling out to
# `docker exec`/`docker run` against the host-mounted socket (the SDK
# alone doesn't cover the subprocess paths in sandbox_runner /
# workspace_sandbox). Just the client; the daemon is the host's.
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
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
