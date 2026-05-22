#!/usr/bin/env bash
# Boot a single Sysbox-isolated container off gapt/runtime:dev and wait
# for the inner dockerd to come up. The container is then ready for the
# per-check scripts in this directory.
#
# Plan: docs/plan/m0/p2_isolation_seaweedfs.md cycle 3
# Progress: docs/progress/m0/p2_isolation_seaweedfs.md PR1

set -euo pipefail

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"
IMAGE="${SYSBOX_POC_IMAGE:-gapt/runtime:dev}"
WAIT_S="${SYSBOX_POC_WAIT_S:-30}"

log() { printf '[boot_sysbox %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if ! docker info >/dev/null 2>&1; then
    log "docker daemon unreachable (check docker group membership / dockerd)"
    exit 2
fi

if ! docker info --format '{{json .Runtimes}}' | grep -q sysbox-runc; then
    log "sysbox-runc runtime not registered with docker. Install sysbox-ce and re-run."
    exit 2
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    log "building $IMAGE from runtime/Dockerfile ..."
    REPO_ROOT="$(git rev-parse --show-toplevel)"
    docker build -f "$REPO_ROOT/runtime/Dockerfile" -t "$IMAGE" "$REPO_ROOT"
fi

log "removing any existing $NAME"
docker rm -f "$NAME" >/dev/null 2>&1 || true

log "booting $NAME (runtime=sysbox-runc, image=$IMAGE)"
# /dev/fuse is exposed unconditionally so that the basic boot script
# can later be reused with a SeaweedFS mount (PR3+). It's harmless when
# no GAPT_SEAWEED_FILER_URL is set — the entrypoint just skips weed.
docker run -d \
    --name "$NAME" \
    --runtime=sysbox-runc \
    --device /dev/fuse:/dev/fuse \
    "$IMAGE" \
    /usr/local/bin/gapt-entrypoint sleep infinity >/dev/null

log "waiting up to ${WAIT_S}s for inner dockerd ..."
for _ in $(seq 1 "$WAIT_S"); do
    if docker exec "$NAME" docker info >/dev/null 2>&1; then
        inner_ver=$(docker exec "$NAME" docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)
        log "inner dockerd is up (version $inner_ver)"
        exit 0
    fi
    sleep 1
done

log "inner dockerd did not become healthy within ${WAIT_S}s — dumping logs"
docker logs --tail=50 "$NAME"
exit 3
