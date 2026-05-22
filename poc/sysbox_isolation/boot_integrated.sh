#!/usr/bin/env bash
# Boot SeaweedFS + a Sysbox sandbox in the same docker network, so the
# sandbox's entrypoint can FUSE-mount the SeaweedFS filer at /workspace
# (option B per decision_volume_driver.md).
#
# This is the M0-P2 PR3 integration smoke. PR4 will then run a real
# git clone + inner-dockerd compose up against the mount.

set -euo pipefail

cd "$(dirname "$0")"

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"
IMAGE="${SYSBOX_POC_IMAGE:-gapt/runtime:dev}"
NETWORK="gapt-poc-seaweed"

# Filer URL is reached from inside the sandbox container — service DNS
# on the shared compose network ("seaweedfs" is the service name).
FILER_URL="${GAPT_SEAWEED_FILER_URL:-http://seaweedfs:8888}"
PROJECT_ID="${GAPT_PROJECT_ID:-poc}"
WORKSPACE_ID="${GAPT_WORKSPACE_ID:-w1}"

log() { printf '[boot_integrated %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# 1. Stand up SeaweedFS (idempotent — re-uses any existing instance)
log "step 1/3: SeaweedFS"
docker compose -f seaweedfs.compose.yml up -d --wait --wait-timeout 60 >/dev/null

# Master healthcheck completes before filer/s3 are listening, so poll
# the filer port specifically before talking to it.
log "  waiting for filer port to listen ..."
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:18888/?type=json" 2>/dev/null; then
        log "  filer ready"
        break
    fi
    sleep 1
done

# 2. Pre-create the filer directory we'll mount, so weed mount doesn't
#    race on the first POST.
log "step 2/3: pre-creating filer path /projects/${PROJECT_ID}/workspaces/${WORKSPACE_ID}"
curl -fsS -X POST \
    "http://127.0.0.1:18888/projects/${PROJECT_ID}/workspaces/${WORKSPACE_ID}/.keep" \
    -F "file=@/dev/null;filename=.keep" -o /dev/null

# 3. Boot the Sysbox sandbox attached to the same network, with the env
#    that the entrypoint reads.
log "step 3/3: Sysbox sandbox with FUSE workspace"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d \
    --name "$NAME" \
    --runtime=sysbox-runc \
    --network "$NETWORK" \
    --device /dev/fuse:/dev/fuse \
    -e "GAPT_SEAWEED_FILER_URL=$FILER_URL" \
    -e "GAPT_PROJECT_ID=$PROJECT_ID" \
    -e "GAPT_WORKSPACE_ID=$WORKSPACE_ID" \
    "$IMAGE" \
    /usr/local/bin/gapt-entrypoint sleep infinity >/dev/null

# Wait for /workspace to be a real mountpoint (entrypoint launches weed
# in the background then polls itself for up to 15s).
log "waiting for /workspace mount ..."
for _ in $(seq 1 30); do
    if docker exec "$NAME" mountpoint -q /workspace; then
        log "✅ /workspace is mounted (filer=${FILER_URL})"
        docker exec "$NAME" df -hT /workspace
        exit 0
    fi
    sleep 1
done

log "❌ /workspace never became a mountpoint — entrypoint log:"
docker exec "$NAME" cat /var/log/weed-mount.log 2>&1 | tail -30
exit 1
