#!/usr/bin/env bash
# Integration checks against a sandbox booted by boot_integrated.sh:
#   I1. /workspace inside the sandbox is a real mountpoint and reports
#       FUSE as its filesystem type.
#   I2. Files written into /workspace from the sandbox are visible via
#       the host-side Filer HTTP API (= the data actually went to
#       SeaweedFS, not just to a tmpfs).
#   I3. Persistence across sandbox restart — kill the container, boot
#       it again with the same env, the file is still there.
#   I4. git clone of a small public repo into /workspace works and the
#       resulting working tree lists correctly via the host Filer.
#
# All checks read-only against the SeaweedFS instance booted by
# boot_seaweedfs.sh / boot_integrated.sh.

set -uo pipefail

cd "$(dirname "$0")" || exit 1

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"
PROJECT_ID="${GAPT_PROJECT_ID:-poc}"
WORKSPACE_ID="${GAPT_WORKSPACE_ID:-w1}"
FILER_HOST=http://127.0.0.1:18888
FILER_PATH="/projects/${PROJECT_ID}/workspaces/${WORKSPACE_ID}"

FAIL=0
ok()   { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
log()  { printf '[check_integration %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if ! docker inspect "$NAME" >/dev/null 2>&1; then
    log "container $NAME not found — boot_integrated.sh first"
    exit 2
fi

# --- I1. /workspace is a real FUSE mount ---
log "I1. /workspace is a real FUSE mount inside the sandbox"
if docker exec "$NAME" mountpoint -q /workspace; then
    fstype=$(docker exec "$NAME" stat -fc %T /workspace)
    if [[ "$fstype" == fuse* ]] || [[ "$fstype" == "fuseblk" ]]; then
        ok "/workspace is mounted (fs=$fstype)"
    else
        fail "/workspace mounted but fs=$fstype (expected fuse*)"
    fi
else
    fail "/workspace is not a mountpoint"
fi

# --- I2. write inside → visible on host-side Filer ---
log "I2. write inside /workspace → visible via host Filer HTTP"
sandbox_marker="i2-$(date +%s).txt"
docker exec "$NAME" bash -c "echo 'inside-sandbox' > /workspace/$sandbox_marker"
sleep 1   # allow filer index to settle
if curl -fsS "${FILER_HOST}${FILER_PATH}/${sandbox_marker}" | grep -q 'inside-sandbox'; then
    ok "host Filer reads back what the sandbox wrote"
else
    fail "host Filer did not return the sandbox's write"
fi

# --- I3. persistence across container restart ---
log "I3. persistence across sandbox restart"
persist_marker="i3-$(date +%s).txt"
docker exec "$NAME" bash -c "echo 'persist me' > /workspace/$persist_marker"
docker restart "$NAME" >/dev/null
log "  waiting for /workspace to remount after restart ..."
for _ in $(seq 1 30); do
    if docker exec "$NAME" mountpoint -q /workspace 2>/dev/null; then break; fi
    sleep 1
done
if docker exec "$NAME" cat "/workspace/$persist_marker" 2>/dev/null | grep -q 'persist me'; then
    ok "file survives sandbox restart"
else
    fail "file did not survive restart"
fi

# --- I4. git clone into /workspace ---
log "I4. git clone of a small public repo into /workspace"
docker exec "$NAME" bash -c 'rm -rf /workspace/hello-world && git clone --depth 1 https://github.com/octocat/Hello-World.git /workspace/hello-world' >/dev/null 2>&1
if docker exec "$NAME" test -f /workspace/hello-world/README; then
    inside_count=$(docker exec "$NAME" find /workspace/hello-world -type f | wc -l)
    ok "git clone produced $inside_count files inside the sandbox"

    # And the host Filer can list at least one of those files
    if curl -fsS -H 'Accept: application/json' "${FILER_HOST}${FILER_PATH}/hello-world/?type=json" | grep -q README; then
        ok "host Filer lists /hello-world (Filer-side proof of write)"
    else
        fail "host Filer cannot see /hello-world"
    fi
else
    fail "git clone did not produce README"
fi

echo
if [[ $FAIL -eq 0 ]]; then
    log "✅ all integration checks passed"
    exit 0
else
    log "❌ at least one check failed"
    exit 1
fi
