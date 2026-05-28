#!/usr/bin/env bash
# GAPT dev server lifecycle helper.
#
# Why this script exists: running the dev uvicorn directly via a
# user shell (or via Claude's background-task tool) makes the server
# a child of that shell. The moment that parent dies — Claude session
# ends, terminal closes, laptop sleeps — uvicorn dies too and
# cloudflared starts returning 502 to any external URL.
#
# This wrapper severs that parent link with `nohup ... & disown`,
# tracks the pid via a file under /tmp, and gives the operator
# start / stop / restart / status / logs subcommands. The server
# survives Claude session exit; only an explicit `server.sh stop`
# (or `kill -9`) brings it down.
#
# Use this for HOST-MODE dev (uvicorn directly on the host machine).
# Production / hands-off setups should use:
#   - `compose/docker-compose.dev.yml`'s server service (see B.H.1
#     plan: server moved into compose with restart=unless-stopped)
#   - or the systemd unit at `compose/systemd/gapt-server.service`
set -euo pipefail

# Resolve paths relative to this script regardless of CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_DIR="$REPO_ROOT/server"

PIDFILE="${GAPT_SERVER_PIDFILE:-/tmp/gapt-server.pid}"
LOGFILE="${GAPT_SERVER_LOGFILE:-/tmp/gapt-server.log}"
PORT="${GAPT_SERVER_PORT:-38001}"

# Default env vars — overridable from caller's environment. These
# match the dev compose setup (Postgres on host:5432, Caddy admin
# on host:32019, preview domain `gapt.hrletsgo.me`).
export GAPT_POSTGRES_DSN="${GAPT_POSTGRES_DSN:-postgresql+psycopg://gapt:gapt_dev_only@localhost:5432/gapt}"
export GAPT_ENV="${GAPT_ENV:-dev}"
export GAPT_LOG_FORMAT="${GAPT_LOG_FORMAT:-console}"
export GAPT_CADDY_ADMIN_URL="${GAPT_CADDY_ADMIN_URL:-http://127.0.0.1:32019}"
export GAPT_CADDY_PREVIEW_DOMAIN="${GAPT_CADDY_PREVIEW_DOMAIN:-gapt.hrletsgo.me}"

# ─────────────────────────────────────────── helpers ──

# Treat any process whose `ps` command-line matches `uvicorn
# gapt_server.app` as ours, regardless of the pidfile content —
# protects against stale pidfiles after crashes.
running_pids() {
    pgrep -f "uvicorn gapt_server.app" 2>/dev/null || true
}

is_running() {
    [ -n "$(running_pids)" ]
}

wait_until_ready() {
    # Poll /health for up to 30s. Returns 0 on success.
    local i=0
    while [ "$i" -lt 30 ]; do
        if curl -sf -o /dev/null "http://localhost:${PORT}/health" 2>/dev/null; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

# ─────────────────────────────────────── subcommands ──

start() {
    if is_running; then
        echo "already running (pid $(running_pids | tr '\n' ' '))"
        return 0
    fi
    cd "$SERVER_DIR"
    # `nohup ... & disown` — detach from this shell + the shell that
    # invoked us. The new process becomes a child of init (PID 1)
    # via the daemon dance, so even if Claude or the user's
    # terminal exits, the server stays alive.
    nohup uv run uvicorn gapt_server.app:app \
        --host 0.0.0.0 --port "$PORT" \
        --proxy-headers --forwarded-allow-ips='*' \
        > "$LOGFILE" 2>&1 < /dev/null &
    local pid=$!
    disown "$pid"
    echo "$pid" > "$PIDFILE"
    echo "started pid=$pid  log=$LOGFILE"
    if wait_until_ready; then
        echo "ready: http://localhost:${PORT}/health → 200"
    else
        echo "WARN: did not respond on /health within 30s — check $LOGFILE" >&2
        return 1
    fi
}

stop() {
    local pids
    pids="$(running_pids)"
    if [ -z "$pids" ]; then
        echo "not running"
        rm -f "$PIDFILE"
        return 0
    fi
    echo "stopping: $pids"
    # SIGTERM first for graceful shutdown; SIGKILL after 5 s if any
    # process is still alive.
    echo "$pids" | xargs -r kill -TERM 2>/dev/null || true
    local i=0
    while [ "$i" -lt 5 ] && [ -n "$(running_pids)" ]; do
        sleep 1
        i=$((i + 1))
    done
    if [ -n "$(running_pids)" ]; then
        echo "force-killing: $(running_pids)"
        running_pids | xargs -r kill -KILL 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
    echo "stopped"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if is_running; then
        local pids
        pids="$(running_pids)"
        echo "running: pid=$pids"
        echo "health: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:${PORT}/health || echo 000)"
        echo "log: $LOGFILE"
    else
        echo "not running"
        rm -f "$PIDFILE"
    fi
}

logs() {
    # Default to following; pass `-n` to just dump the last N lines.
    if [ "${1:-}" = "-n" ]; then
        tail -n "${2:-100}" "$LOGFILE"
    else
        tail -f "$LOGFILE"
    fi
}

# ─────────────────────────────────────────── dispatch ──

cmd="${1:-status}"
case "$cmd" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    logs)    shift; logs "$@" ;;
    *)
        echo "usage: $0 {start|stop|restart|status|logs [-n N]}" >&2
        exit 2
        ;;
esac
