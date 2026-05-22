#!/usr/bin/env bash
# gapt-entrypoint — boots inner dockerd, mounts SeaweedFS at /workspace
# if requested, then exec's the daemon (or user cmd).
#
# Sysbox runs this container's PID 1. We need both
#   (a) inner dockerd up so userland tools (compose, build) work, and
#   (b) /workspace pointing at SeaweedFS so any state written persists
#       across container lifetimes,
# before the daemon starts answering RPCs. This is safe DinD via Sysbox
# (no host docker socket mounted) plus option B from
# poc/sysbox_isolation/decision_volume_driver.md.

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
    # Storage driver: Sysbox's recommended path is shiftfs + overlay2,
    # but that requires the sysbox-managed mount setup which only kicks
    # in for systemd-launched dockerd. Our bare-spawn path falls back
    # to the user-namespace-friendly `fuse-overlayfs` snapshotter when
    # available, else plain `vfs` (slow but always works).
    local storage_args=()
    if command -v fuse-overlayfs >/dev/null 2>&1; then
        storage_args=(--storage-driver=fuse-overlayfs)
        log "  using fuse-overlayfs storage driver"
    else
        storage_args=(--storage-driver=vfs)
        log "  using vfs storage driver (fuse-overlayfs not installed)"
    fi
    nohup dockerd "${storage_args[@]}" \
        --host=unix:///var/run/docker.sock \
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

mount_seaweedfs_workspace() {
    if [[ -z "${GAPT_SEAWEED_FILER_URL:-}" ]]; then
        log "GAPT_SEAWEED_FILER_URL unset — /workspace stays as the container default"
        return 0
    fi
    if ! command -v weed >/dev/null 2>&1; then
        log "weed not installed in this image — cannot mount SeaweedFS"
        return 1
    fi

    local filer_path="${GAPT_SEAWEED_PATH:-/projects/${GAPT_PROJECT_ID:-default}/workspaces/${GAPT_WORKSPACE_ID:-default}}"
    # `weed mount -filer` takes host:port, NOT a URL. Strip any
    # scheme prefix our callers might have passed.
    local filer_hostport="${GAPT_SEAWEED_FILER_URL#http://}"
    filer_hostport="${filer_hostport#https://}"
    filer_hostport="${filer_hostport%/}"

    mkdir -p /workspace

    if mountpoint -q /workspace; then
        log "/workspace already mounted — skipping"
        return 0
    fi

    log "mounting SeaweedFS ${filer_hostport}${filer_path} → /workspace ..."
    nohup weed mount \
        -filer="$filer_hostport" \
        -dir=/workspace \
        -filer.path="$filer_path" \
        > /var/log/weed-mount.log 2>&1 &

    for i in {1..30}; do
        if mountpoint -q /workspace; then
            log "SeaweedFS mounted at /workspace (after ${i}s)"
            return 0
        fi
        sleep 0.5
    done
    log "WARN: SeaweedFS mount did not appear in 15s — continuing anyway"
    log "  weed-mount.log tail:"
    tail -20 /var/log/weed-mount.log 2>&1 | sed 's/^/    /'
    return 1
}

main() {
    start_inner_dockerd
    mount_seaweedfs_workspace || true   # missing mount is logged, not fatal
    log "exec: $*"
    exec "$@"
}

main "$@"
