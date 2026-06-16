"""In-container port reconciliation for dev services.

The prod deploy path has a host-port preflight (`deploy/ports.py`):
before `docker compose up` it enumerates occupied ports and remaps
the stack onto free ones, because Caddy dials the *container* and the
host port is fungible. Dev services had no equivalent, so a restart
hit the wall that prod never sees:

  `next dev -p 3000` (port pinned in the user's package.json) is a
  singleton per (workspace, label). When GAPT restarts — or crashes,
  or the stop() pgid-kill misses a child that broke out of the group
  — the *previous* run's process can survive inside the long-lived
  `gapt-ws-<wid>` container still bound to 3000. Turbopack is the
  common offender: `next dev --turbo` spawns a detached `next-server`
  that no longer carries the `GAPT_SVC=` marker, so neither stop()
  nor recover() can find it by marker. The next start then dies with
  `EADDRINUSE: address already in use :::3000` and the user is stuck.

The dev-correct answer is NOT "remap to another port" (the user's
command has the port hardcoded — it would ignore `$PORT`, and the
zombie would leak forever). It's "free the port the service is about
to claim by reaping whatever stale process holds it." That is what
`ServiceRegistry._reconcile_port` does using the primitives here.

This module is pure logic (a port parser + a dash-safe kill-script
renderer) so it unit-tests without docker; the registry drives the
`docker exec` calls. Mirrors the `ports.py` / `local.py` split.
"""

from __future__ import annotations

import re

# Policy values, mirrored into Settings. "free" (default) reaps the
# stale holder so the restart succeeds; "strict" refuses to touch a
# foreign holder and surfaces the conflict; "off" disables the
# port-free step entirely (the marker reap still runs — that's always
# our own leftover and always safe).
VALID_PORT_POLICIES = ("free", "strict", "off")

# Explicit port flags a dev command may carry. Anchored on a word
# boundary so `--port` doesn't also match `--portal`, and `-p` doesn't
# match `-print`. Value is 2-5 digits (10..65535-ish — sub-10 ports
# are never dev servers and matching them invites false positives on
# `-p 1` style typos).
_CMD_PORT_FLAG_RE = re.compile(r"(?:^|\s)(?:--port|--listen|-p|-P)(?:[=\s]+)(\d{2,5})(?=\s|$|/)")
# Inline `PORT=3000 next dev` env assignment in the command itself.
_CMD_INLINE_PORT_RE = re.compile(r"(?:^|\s)PORT=(\d{2,5})(?=\s|$)")
# `python -m http.server 8080` — positional port.
_CMD_HTTP_SERVER_RE = re.compile(r"http\.server\s+(\d{2,5})(?=\s|$)")
# `php -S 0.0.0.0:8000` — host:port positional.
_CMD_PHP_SERVE_RE = re.compile(r"-S\s+\S*?:(\d{2,5})(?=\s|$)")

# Log-derived hints, in priority order. The echoed command line
# (`> next dev --turbo -p 3000`) is the most reliable, then an
# EADDRINUSE crash line (which names the contested port), then a
# generic "listening on addr:port" banner. Used only to recover the
# port number when the live command string doesn't carry it (the port
# lives in package.json) — see ServiceRegistry.start.
_LOG_EADDRINUSE_RE = re.compile(
    r"(?:EADDRINUSE|address already in use|already allocated)\D{0,40}?(\d{2,5})(?=\D|$)",
    re.IGNORECASE,
)
_LOG_LISTEN_RE = re.compile(
    r"(?:"
    r"https?://[\w.-]+:|localhost:|0\.0\.0\.0:|127\.0\.0\.1:|\[::\]:|on port |listening on :"
    r")(\d{2,5})\b",
    re.IGNORECASE,
)


def _clamp_port(raw: str | int | None) -> int | None:
    """Parse to a valid 1-65535 port or None."""
    if raw is None:
        return None
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def parse_intended_port(
    cmd: str,
    env: dict[str, str] | None = None,
    declared_port: int | None = None,
) -> int | None:
    """Best-effort guess at the port ``cmd`` will bind.

    Precedence mirrors what the process actually does at runtime:
    an explicit ``-p``/``--port`` flag overrides everything (it beats
    ``$PORT`` for next/vite/http-server), then an inline ``PORT=`` in
    the command, then positional forms, then the effective ``$PORT``
    (the start-form's declared port is plumbed in as ``PORT`` unless
    the caller's ``env`` already pins one, so ``env`` wins there too).

    Returns None when nothing parseable is present — the caller then
    falls back to the previous run's log (the port may be hidden in a
    package.json script) and, failing that, skips the port-free step.
    """
    m = _CMD_PORT_FLAG_RE.search(cmd)
    if m:
        return _clamp_port(m.group(1))
    m = _CMD_INLINE_PORT_RE.search(cmd)
    if m:
        return _clamp_port(m.group(1))
    for rx in (_CMD_HTTP_SERVER_RE, _CMD_PHP_SERVE_RE):
        m = rx.search(cmd)
        if m:
            return _clamp_port(m.group(1))
    if env and "PORT" in env:
        from_env = _clamp_port(env["PORT"])
        if from_env is not None:
            return from_env
    return _clamp_port(declared_port)


def extract_log_port_hint(text: str) -> int | None:
    """Recover a port number from a previous run's captured log.

    Handles the very case in the bug report: the service command is
    ``npm run dev`` (no port on the GAPT-visible command line) but the
    log echoes ``> next dev --turbo -p 3000`` and then the
    ``EADDRINUSE … :::3000`` crash. Either tells us which port the
    zombie is squatting so the reconcile can free it.
    """
    if not text:
        return None
    # The echoed command line carries the real flag.
    via_cmd = parse_intended_port(text)
    if via_cmd is not None:
        return via_cmd
    m = _LOG_EADDRINUSE_RE.search(text)
    if m:
        port = _clamp_port(m.group(1))
        if port is not None:
            return port
    # A "listening on addr:port" banner — take the LAST one (a server
    # that drifted 5173 -> 5174 prints the live port last).
    matches = list(_LOG_LISTEN_RE.finditer(text))
    if matches:
        return _clamp_port(matches[-1].group(1))
    return None


def free_listener_pgid_script(port: int, signal: str) -> str:
    """Render a dash-safe ``sh -c`` one-liner that signals every
    process holding a LISTEN socket on ``port`` inside the container.

    Pure ``/proc`` — no dependency on ``fuser``/``ss``/``lsof`` (slim
    dev images ship none of them). The walk:

      1. From ``/proc/net/tcp`` + ``/proc/net/tcp6`` collect the inode
         of every TCP socket in state ``0A`` (LISTEN) whose local port
         matches (the port is the hex after the ``:`` in the local
         address column — same encoding for v4 and v6, since the v6
         address blob has no colons).
      2. For each pid, scan ``/proc/<pid>/fd`` for a symlink to
         ``socket:[<inode>]``. A match means that pid owns the
         listener.
      3. Signal both the pid's process *group* (so ``npm`` →
         ``node next`` → ``next-server`` all go down together) and the
         pid itself (belt-and-braces if the pgid parse came back
         empty). The pgid is read from ``/proc/<pid>/stat`` robustly:
         we strip everything up to the last ``) `` first, because a
         process ``comm`` can contain spaces/parens and would
         otherwise shift the column index.

    ``signal`` is ``TERM`` (graceful) or ``KILL`` (force). pgid 0/1 is
    skipped so init is never signalled. The trailing ``true`` keeps
    the exit status clean for the idempotent second pass.

    ``port`` is an int (caller-validated) so it interpolates safely —
    no user string reaches the shell here.
    """
    hexport = f"{port:04X}"
    return (
        'inodes=$(awk \'$4=="0A"{n=split($2,a,":"); '
        f'if(a[n]=="{hexport}") print $10}}\' '
        "/proc/net/tcp /proc/net/tcp6 2>/dev/null | sort -u); "
        '[ -z "$inodes" ] && exit 0; '
        "for pid in $(ls /proc 2>/dev/null); do "
        'case "$pid" in *[!0-9]*) continue ;; esac; '
        'for fd in /proc/"$pid"/fd/*; do '
        'tgt=$(readlink "$fd" 2>/dev/null) || continue; '
        "for ino in $inodes; do "
        'if [ "$tgt" = "socket:[$ino]" ]; then '
        'pgid=$(awk \'{s=$0; sub(/.*\\) /,"",s); split(s,f," "); print f[3]}\' '
        '/proc/"$pid"/stat 2>/dev/null); '
        '[ -n "$pgid" ] && [ "$pgid" -gt 1 ] && '
        f'kill -{signal} -"$pgid" 2>/dev/null; '
        f'kill -{signal} "$pid" 2>/dev/null; '
        "break; "
        "fi; "
        "done; "
        "done; "
        "done; "
        "true"
    )
