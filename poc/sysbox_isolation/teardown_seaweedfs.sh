#!/usr/bin/env bash
# Stop the PoC SeaweedFS stack. Pass --wipe to also drop the volume so
# the next boot starts from an empty data store.

set -euo pipefail

cd "$(dirname "$0")"

WIPE=0
for arg in "$@"; do
    case "$arg" in
        --wipe|-v) WIPE=1 ;;
    esac
done

log() { printf '[teardown_seaweedfs %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if [[ $WIPE -eq 1 ]]; then
    log "stopping + removing volumes"
    docker compose -f seaweedfs.compose.yml down -v
else
    log "stopping (volume kept — pass --wipe to drop)"
    docker compose -f seaweedfs.compose.yml down
fi
