#!/usr/bin/env bash
# First-pass isolation checks against an already-booted Sysbox PoC
# container. The full I1~I9 matrix lands as automated pytest in PR5;
# this is the smoke test that any later development relies on.
#
# Checks executed:
#   B1. host /var/run/docker.sock is not mounted into the container
#   B2. inner dockerd reports a *different* Server Version than the host
#       (cheapest sanity that we're not just sharing the host daemon)
#   B3. the container's runtime is sysbox-runc (not vanilla runc)
#   B4. inner `docker ps` is empty initially — the sandbox is fresh
#   B5. expected tool inventory is present (git/gh/python/node/uv/docker
#       /toolkit-agent)
#
# Exit code: 0 on full pass, 1 on first failure.

set -uo pipefail

NAME="${SYSBOX_POC_NAME:-gapt-sysbox-poc}"
FAIL=0

ok()   { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }

exec_in() { docker exec "$NAME" bash -c "$1"; }

log() { printf '[check_basic %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if ! docker inspect "$NAME" >/dev/null 2>&1; then
    log "container $NAME not found — run boot_sysbox.sh first"
    exit 2
fi

log "B1. host /var/run/docker.sock must NOT be mounted"
mounts=$(docker inspect "$NAME" --format '{{range .Mounts}}{{.Source}}={{.Destination}};{{end}}')
if printf '%s' "$mounts" | grep -q '/var/run/docker.sock'; then
    fail "host docker.sock IS mounted (T6 violation). Mounts: $mounts"
else
    ok "no host docker.sock mount (Mounts=${mounts:-none})"
fi

log "B2. inner Server Version differs from host"
host_ver=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
inner_ver=$(exec_in 'docker version --format "{{.Server.Version}}" 2>/dev/null' || true)
if [[ -z "$inner_ver" ]]; then
    fail "inner dockerd not responding"
elif [[ "$host_ver" == "$inner_ver" ]]; then
    fail "inner dockerd reports same version as host ($host_ver) — suspicious"
else
    ok "host=$host_ver, inner=$inner_ver (distinct daemons)"
fi

log "B3. container runtime is sysbox-runc"
rt=$(docker inspect "$NAME" --format '{{.HostConfig.Runtime}}')
if [[ "$rt" == "sysbox-runc" ]]; then
    ok "runtime=$rt"
else
    fail "runtime=$rt (expected sysbox-runc)"
fi

log "B4. inner docker ps starts empty"
inner_ps=$(exec_in 'docker ps -a -q | wc -l')
if [[ "$inner_ps" == "0" ]]; then
    ok "inner has 0 containers (clean)"
else
    fail "inner has $inner_ps containers — leftover state?"
fi

log "B5. tool inventory inside the container"
for tool in 'git --version' 'gh --version' 'python3 --version' 'node --version' 'pnpm --version' 'uv --version' 'toolkit-agent version' 'docker --version'; do
    if out=$(exec_in "$tool" 2>&1 | head -1); then
        ok "$tool → $out"
    else
        fail "$tool not available"
    fi
done

echo
if [[ $FAIL -eq 0 ]]; then
    log "✅ all basic isolation checks passed"
    exit 0
else
    log "❌ at least one check failed"
    exit 1
fi
