#!/usr/bin/env bash
# gapt-entrypoint — boots inner dockerd then exec's the daemon (or user cmd).
#
# Sysbox runs this container's PID 1. We need dockerd running before the
# daemon answers /info, so userland tools (compose etc.) work end-to-end.
# This is safe DinD via Sysbox (no host docker socket mounted).

set -euo pipefail

log() { printf '[gapt-entrypoint %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

start_inner_dockerd() {
    if ! command -v dockerd >/dev/null 2>&1; then
        log "dockerd not installed — skipping inner Docker boot"
        return 0
    fi
    if pgrep -x dockerd >/dev/null 2>&1; then
        log "dockerd already running"
        return 0
    fi
    log "starting inner dockerd ..."
    nohup dockerd --host=unix:///var/run/docker.sock \
        > /var/log/dockerd.log 2>&1 &
    for i in {1..30}; do
        if docker info >/dev/null 2>&1; then
            log "inner dockerd is up (after ${i}s)"
            return 0
        fi
        sleep 1
    done
    log "WARN: inner dockerd did not become healthy in 30s — continuing anyway"
}

main() {
    start_inner_dockerd
    log "exec: $*"
    exec "$@"
}

main "$@"
