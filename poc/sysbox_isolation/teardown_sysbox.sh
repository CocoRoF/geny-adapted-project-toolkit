#!/usr/bin/env bash
# Stop and remove the Sysbox PoC container. Volumes are not removed yet
# because PR3/PR4 will start mounting a SeaweedFS volume into /workspace
# — the teardown will be revisited there.
#
# Plan: docs/plan/m0/p2_isolation_seaweedfs.md cycle 3
# Progress: docs/progress/m0/p2_isolation_seaweedfs.md PR1

set -euo pipefail

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"

log() { printf '[teardown_sysbox %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    log "removing $NAME"
    docker rm -f "$NAME" >/dev/null
    log "removed"
else
    log "$NAME not present"
fi
