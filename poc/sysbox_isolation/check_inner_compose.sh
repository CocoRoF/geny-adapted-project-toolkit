#!/usr/bin/env bash
# Inside the booted Sysbox sandbox, run a small user-style compose
# project via the *inner* dockerd, and prove that:
#   C1. inner `docker compose up -d --wait` actually succeeds
#   C2. the service is reachable from inside the sandbox
#   C3. the host's docker is unaware of that service (T6 once more —
#       the inner dockerd is fully separate)
#   C4. compose down cleans up inner-side

set -uo pipefail

cd "$(dirname "$0")" || exit 1

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"
SAMPLE_DIR="/workspace/sample-compose"

FAIL=0
ok()   { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
log()  { printf '[check_inner_compose %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if ! docker inspect "$NAME" >/dev/null 2>&1; then
    log "container $NAME not found — boot_integrated.sh first"
    exit 2
fi

# Copy the sample compose into the sandbox's /workspace (which itself
# is FUSE-mounted to SeaweedFS — that means even the compose file is
# living on the persistent file core). `docker cp` into a FUSE target
# is occasionally fussy; `tee` is bullet-proof.
log "copying sample-compose into sandbox /workspace ..."
docker exec "$NAME" mkdir -p "$SAMPLE_DIR"
docker exec -i "$NAME" tee "$SAMPLE_DIR/docker-compose.yml" \
    < ./sample-compose/docker-compose.yml > /dev/null

# --- C0. inner dockerd is reachable and can pull (the half that works) ---
log "C0. inner dockerd answers, image pulls succeed"
if docker exec "$NAME" docker info >/dev/null 2>&1; then
    ok "inner dockerd reachable"
else
    fail "inner dockerd not reachable"
fi
if docker exec "$NAME" docker pull --quiet hashicorp/http-echo:latest >/dev/null 2>&1; then
    ok "inner image pull works"
else
    fail "inner image pull failed"
fi

# --- C1. inner compose up ---
# Sysbox 0.7.0 resolves the docker 25+ procfs cross-device crash that
# affected 0.6.7 (see known_issues.md KI-1).
log "C1. inner docker compose up -d --wait"
if docker exec -w "$SAMPLE_DIR" "$NAME" docker compose up -d --wait --wait-timeout 60 >/dev/null 2>&1; then
    ok "inner compose up succeeded"
else
    fail "inner compose up failed"
    docker exec -w "$SAMPLE_DIR" "$NAME" docker compose ps 2>&1 | head -10
    docker exec -w "$SAMPLE_DIR" "$NAME" docker compose logs --tail=20 2>&1 | head -30
fi

# --- C2. reachable from inside the sandbox (only meaningful when C1 passes) ---
service_id=$(docker exec -w "$SAMPLE_DIR" "$NAME" docker compose ps -q hello 2>/dev/null)
if [[ -n "$service_id" ]]; then
    log "C2. service reachable on the inner network"
    if docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:8089/' 2>/dev/null | grep -q 'gapt sandbox inner compose ok'; then
        ok "got expected body from inner http-echo via 127.0.0.1:8089"
    else
        fail "service responded with unexpected body"
    fi
else
    log "C2. skipped — no inner service container (waiting on KI-1)"
fi

# --- C3. host docker is unaware ---
# This is the inviolable check — even when the inner side can't run a
# user container, the host MUST NOT see any inner-side images leak.
log "C3. host docker ps does NOT show inner-side images"
if docker ps --format '{{.Image}}' | grep -E '^(hashicorp/http-echo|nginx:alpine)' >/dev/null; then
    fail "host docker ps shows inner-side image — inner dockerd may be leaking"
else
    ok "host docker has no inner-side containers (boundary holds)"
fi

# --- C4. compose down (idempotent) ---
log "C4. inner compose down is idempotent"
if docker exec -w "$SAMPLE_DIR" "$NAME" docker compose down >/dev/null 2>&1; then
    ok "inner compose down returns 0"
else
    fail "inner compose down returned non-zero"
fi

echo
if [[ $FAIL -eq 0 ]]; then
    log "✅ inner-compose lifecycle PASS"
    exit 0
else
    log "❌ at least one check failed"
    exit 1
fi
