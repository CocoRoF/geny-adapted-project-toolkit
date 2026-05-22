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
    UV_LINK_MODE=copy

ARG UV_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
        | tar -xz -C /usr/local/bin --strip-components=1 uv-x86_64-unknown-linux-gnu/uv

WORKDIR /opt/gapt-server
COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python python${PYTHON_VERSION} /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv sync --frozen --no-install-project

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
WORKDIR /app
COPY --chown=gapt:gapt server/pyproject.toml ./
COPY --chown=gapt:gapt server/src ./src

USER gapt
EXPOSE 8088

CMD ["python", "-m", "uvicorn", "gapt_server.app:app", "--host", "0.0.0.0", "--port", "8088"]

LABEL org.opencontainers.image.title="gapt/server" \
      org.opencontainers.image.description="GAPT control plane (FastAPI)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/CocoRoF/geny-adapted-project-toolkit"
