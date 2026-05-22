#!/usr/bin/env bash
# Boot the standalone SeaweedFS PoC instance on shifted ports
# (127.0.0.1:19333 / 18888 / 18333) so it does not collide with
# compose/docker-compose.dev.yml's SeaweedFS.

set -euo pipefail

cd "$(dirname "$0")"

log() { printf '[boot_seaweedfs %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "starting gapt-poc-seaweed stack ..."
docker compose -f seaweedfs.compose.yml up -d --wait --wait-timeout 120

log "master:"   ; curl -fsS http://127.0.0.1:19333/cluster/healthz && echo
log "filer:"    ; curl -fsS http://127.0.0.1:18888/ -o /dev/null -w 'HTTP %{http_code}\n'
log "s3 healthcheck (anonymous list — expects access-denied/ok):"
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:18333/ || true

log "✅ SeaweedFS PoC up at:"
echo "  master  http://127.0.0.1:19333"
echo "  filer   http://127.0.0.1:18888"
echo "  s3      http://127.0.0.1:18333  (accessKey=poc-key, secretKey=poc-secret)"
