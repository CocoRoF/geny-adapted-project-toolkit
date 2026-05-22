# syntax=docker/dockerfile:1.7
# gapt/runtime — base image for per-project sandbox containers.
#
# Boundary: this image runs *inside* a Sysbox container per workspace.
# Inner dockerd boots within (Sysbox safe DinD); host docker socket is
# NEVER mounted. See docs/06_isolation_and_runtime.md.
#
# Base: Ubuntu 24.04 LTS (noble) — Python 3.12 is the system default,
# avoiding the Debian bookworm 3.11 ceiling. LTS through 2029.

ARG PYTHON_VERSION=3.12
ARG NODE_MAJOR=22
ARG UV_VERSION=0.4.30

# ---------- stage: base ----------
FROM ubuntu:24.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git git-lfs \
        less vim-tiny jq \
        build-essential pkg-config \
        gnupg lsb-release \
        procps psmisc \
        iproute2 iptables \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

# gh CLI (GitHub)
RUN install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Docker CE (for inner dockerd + docker CLI inside Sysbox)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Python (slim ships without it)
ARG PYTHON_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python${PYTHON_VERSION} /usr/local/bin/python3 \
    && ln -sf /usr/bin/python${PYTHON_VERSION} /usr/local/bin/python

# uv (Python package manager — replaces pip/poetry; fast & deterministic)
ARG UV_VERSION
RUN curl -fsSL https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
        | tar -xz -C /usr/local/bin --strip-components=1 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx

# Node 22 + pnpm + yarn
ARG NODE_MAJOR
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable \
    && corepack prepare pnpm@latest --activate \
    && corepack prepare yarn@stable --activate

# ---------- stage: daemon build ----------
# Ubuntu 24.04 enforces PEP 668 (externally-managed-environment), so
# install the daemon into its own venv at /opt/gapt-runtime-venv and
# carry that whole tree into the final stage.
FROM base AS daemon-build

WORKDIR /opt/gapt-runtime
COPY runtime/pyproject.toml runtime/README.md /opt/gapt-runtime/
COPY runtime/src /opt/gapt-runtime/src
RUN uv venv /opt/gapt-runtime-venv \
    && uv pip install --python /opt/gapt-runtime-venv/bin/python --no-cache /opt/gapt-runtime

# ---------- stage: final ----------
FROM base AS final

COPY --from=daemon-build /opt/gapt-runtime-venv /opt/gapt-runtime-venv
RUN ln -s /opt/gapt-runtime-venv/bin/toolkit-agent /usr/local/bin/toolkit-agent
COPY runtime/scripts/entrypoint.sh /usr/local/bin/gapt-entrypoint
RUN chmod +x /usr/local/bin/gapt-entrypoint

# claude CLI is mounted from the host or installed by the host orchestrator
# (sandbox-controller injects it; not bundled in this image to keep auth
# user-driven). See docs/04_llm_agent_layer.md §4.5.2.

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/gapt-entrypoint"]
CMD ["toolkit-agent", "serve"]

LABEL org.opencontainers.image.title="gapt/runtime" \
      org.opencontainers.image.description="GAPT per-project sandbox runtime (Sysbox-friendly)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/CocoRoF/geny-adapted-project-toolkit"
