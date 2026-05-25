# gapt-workspace — per-workspace sandbox image.
#
# Every GAPT workspace gets its own long-lived container running this
# image (one container per workspace, name `gapt-ws-<id>`). The image
# bakes in the tools the workspace's user — and the agent CLI running
# inside it — need most often:
#
#   * bash + standard userspace (apt, curl, git, ...)
#   * Node 20 + npm  (lots of dev stacks)
#   * Python 3 + pip + venv
#   * The Claude Code CLI (`claude`), so agent sessions can `docker
#     exec` straight into the container instead of running on the host.
#
# Sized for "good enough to start coding on a fresh worktree" — not a
# full kitchen sink. Heavier stacks (CUDA, Java, Rust, ...) should
# layer on top in a project-specific Dockerfile and override the image
# via `GAPT_WORKSPACE_SANDBOX_IMAGE=<your-image>`.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # Dev-server convention — most Node frameworks (vite, next dev,
    # webpack-dev-server, ...) pick this up and bind 0.0.0.0 instead
    # of 127.0.0.1. Required for Caddy on `gapt-net` to reach the
    # service via docker DNS. Users can still override per-process.
    HOST=0.0.0.0 \
    HOSTNAME=0.0.0.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gnupg \
        less \
        locales \
        nano \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
        tini \
        unzip \
        vim-tiny \
        wget \
 && locale-gen en_US.UTF-8 \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g --no-audit --no-fund \
        @anthropic-ai/claude-code \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Sanity — fail the build if claude isn't reachable.
RUN claude --version || (echo "claude CLI not installed correctly" && exit 1)

WORKDIR /workspace

# GAPT's WorkspaceSandbox overrides the CMD at `docker run` time
# (`sh -c 'tail -f /dev/null'`). This default is here so a manual
# `docker run gapt-workspace:latest` still produces a sensible shell.
CMD ["/bin/bash", "-l"]
