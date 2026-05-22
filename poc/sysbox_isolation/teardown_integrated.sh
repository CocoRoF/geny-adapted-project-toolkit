#!/usr/bin/env bash
# Tear down both the Sysbox sandbox and the SeaweedFS PoC.

set -euo pipefail

cd "$(dirname "$0")"

WIPE=0
for arg in "$@"; do
    case "$arg" in
        --wipe|-v) WIPE=1 ;;
    esac
done

log() { printf '[teardown_integrated %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "removing Sysbox sandbox"
docker rm -f "${SYSBOX_POC_NAME:-gapt-sysbox-poc}" >/dev/null 2>&1 || true

log "stopping SeaweedFS"
if [[ $WIPE -eq 1 ]]; then
    docker compose -f seaweedfs.compose.yml down -v
else
    docker compose -f seaweedfs.compose.yml down
fi
