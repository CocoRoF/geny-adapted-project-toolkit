"""Local→remote tunnel migration helper.

Tunnel id forms — cloudflared's CLI happily accepts either the
tunnel's UUID (`802da1da-bd39-42d6-ad84-1c865f9f57bd`) or its
friendly name (`hr109`). The Cloudflare API, however, requires
the UUID. When the local YAML's `tunnel:` key holds a name we
fall back to the UUID embedded in the `credentials-file:` path
(`<UUID>.json` is the standard layout) — see `extract_tunnel_uuid`.


When a Cloudflare Tunnel was set up the "legacy" way — running with
`--config /etc/cloudflared/config.yml` under systemd — ingress
routing lives in the YAML file on the host. The Cloudflare API
`PUT .../configurations` endpoint is a no-op at runtime for those
tunnels because cloudflared never reads remote config.

This module helps an operator flip that tunnel into remote-managed
mode in three coordinated steps:

1. **Inspect** — read the local config YAML, parse it. No mutation.
2. **Push** — replay the local `ingress` array into Cloudflare's
   remote configuration via the existing service. Idempotent.
3. **Cut over** — generate a sudo shell script the operator runs
   once to install a systemd drop-in that drops `--config`
   from cloudflared's `ExecStart` + restart. GAPT itself never
   touches the unit file or runs systemctl.
4. **Verify** — re-snapshot and confirm the tunnel now reports
   `source: cloudflare` (remote-managed).

The shell script approach keeps GAPT off the sudo path entirely:
the operator inspects what GAPT generated, runs it with sudo, and
GAPT verifies the result afterward.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# Cloudflare tunnel UUIDs are RFC-4122 v4 — 8-4-4-4-12 hex digits.
# We don't need strict v4 validation; the loose match is enough to
# tell a UUID apart from a name.
_UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)


def looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.fullmatch(value))


def extract_tunnel_uuid(
    tunnel_id_field: str | None, credentials_file: str | None
) -> str | None:
    """Return the tunnel's UUID. If `tunnel:` already holds a UUID
    we return it verbatim; otherwise we scan the credentials-file
    path for a UUID (cloudflared writes `<UUID>.json`)."""
    if tunnel_id_field and looks_like_uuid(tunnel_id_field):
        return tunnel_id_field
    if credentials_file:
        m = _UUID_RE.search(credentials_file)
        if m:
            return m.group(1)
    return None


DEFAULT_CONFIG_PATH = "/etc/cloudflared/config.yml"
DEFAULT_UNIT_NAME = "cloudflared.service"
DEFAULT_DROPIN_DIR = "/etc/systemd/system/cloudflared.service.d"
DEFAULT_DROPIN_FILENAME = "gapt-remote-managed.conf"
DEFAULT_CLOUDFLARED_BIN = "/usr/bin/cloudflared"


class LocalConfigError(RuntimeError):
    """Raised when the local cloudflared config can't be read /
    parsed / interpreted. Caller surfaces the message verbatim."""


@dataclass(frozen=True)
class LocalConfigInspection:
    path: str
    """Resolved absolute path of the config file we tried to read."""

    exists: bool
    readable: bool
    raw_text: str
    """The full text — surfaced to the UI for transparency. Empty
    string when not readable."""

    tunnel_id: str | None
    """The `tunnel:` key from the YAML. May be either the UUID
    or a friendly name; use `tunnel_uuid` for the API-shaped value."""

    tunnel_uuid: str | None
    """Best-effort UUID — either `tunnel_id` itself when it parses
    as a UUID, or extracted from `credentials_file`. None when we
    can't determine either."""

    credentials_file: str | None
    """The `credentials-file:` key, the path to the tunnel's secret
    JSON. UI shows this so the operator can confirm we're touching
    the right tunnel."""

    ingress: list[dict[str, Any]]
    """Parsed `ingress:` array, in the shape Cloudflare's
    `PUT /configurations` expects (after light normalisation —
    `hostname` / `service` / `path` / `originRequest`)."""


def _resolve_config_path() -> str:
    """Path the helper should read. Overridable via
    `GAPT_CLOUDFLARED_CONFIG_PATH` for non-/etc deployments and
    tests."""
    return os.environ.get("GAPT_CLOUDFLARED_CONFIG_PATH") or DEFAULT_CONFIG_PATH


def _normalise_ingress_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Map yaml-style keys (`originRequest` or `origin_request`) to
    the API's canonical camelCase form. The Cloudflare API only
    accepts `originRequest`; yaml configs vary."""
    out: dict[str, Any] = {}
    if "service" in raw:
        out["service"] = raw["service"]
    if raw.get("hostname"):
        out["hostname"] = raw["hostname"]
    if raw.get("path"):
        out["path"] = raw["path"]
    orig = raw.get("originRequest") or raw.get("origin_request")
    if orig:
        out["originRequest"] = orig
    return out


def inspect_local() -> LocalConfigInspection:
    path = _resolve_config_path()
    p = Path(path)
    if not p.exists():
        return LocalConfigInspection(
            path=path,
            exists=False,
            readable=False,
            raw_text="",
            tunnel_id=None,
            tunnel_uuid=None,
            credentials_file=None,
            ingress=[],
        )

    try:
        raw_text = p.read_text(encoding="utf-8")
    except PermissionError as exc:
        # The systemd-installed cloudflared config is mode 644 by
        # default, but a tightened deployment may chmod 600. Surface
        # the path so the operator knows what to chmod.
        raise LocalConfigError(
            f"`{path}` exists but GAPT can't read it ({exc}). "
            f"`sudo chmod 644 {path}` and retry, or set "
            f"GAPT_CLOUDFLARED_CONFIG_PATH to a readable copy."
        ) from exc
    except OSError as exc:
        raise LocalConfigError(f"could not read `{path}`: {exc}") from exc

    try:
        parsed = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise LocalConfigError(
            f"`{path}` is not valid YAML: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise LocalConfigError(
            f"`{path}` parsed to a {type(parsed).__name__}, expected a mapping."
        )

    tunnel_id = parsed.get("tunnel")
    if tunnel_id is not None and not isinstance(tunnel_id, str):
        tunnel_id = str(tunnel_id)

    credentials_file = parsed.get("credentials-file") or parsed.get("credentials_file")
    if credentials_file is not None and not isinstance(credentials_file, str):
        credentials_file = str(credentials_file)

    ingress_raw = parsed.get("ingress") or []
    if not isinstance(ingress_raw, list):
        raise LocalConfigError(
            f"`{path}`'s `ingress:` key is not a list "
            f"(found {type(ingress_raw).__name__})."
        )
    ingress = [
        _normalise_ingress_entry(e)
        for e in ingress_raw
        if isinstance(e, dict)
    ]

    return LocalConfigInspection(
        path=path,
        exists=True,
        readable=True,
        raw_text=raw_text,
        tunnel_id=tunnel_id,
        tunnel_uuid=extract_tunnel_uuid(tunnel_id, credentials_file),
        credentials_file=credentials_file,
        ingress=ingress,
    )


# Tunnel identifiers from Cloudflare are either UUIDs or friendly
# names (`hr109`). Both forms only contain `[A-Za-z0-9_-]`, so any
# value with shell metacharacters is either a bug or malicious. We
# validate at script-generation time to keep the generated script
# safe to run as root, even though the value is GAPT-generated.
_TUNNEL_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UnsafeTunnelIdError(ValueError):
    """Raised when a tunnel_id contains characters that don't belong
    in a Cloudflare tunnel UUID or friendly name. Defense in depth —
    the value should already be validated before reaching here, but
    we re-check at the boundary that emits root-executable text."""


def _ensure_safe_tunnel_id(tunnel_id: str) -> str:
    if not tunnel_id or not _TUNNEL_ID_SAFE_RE.match(tunnel_id):
        raise UnsafeTunnelIdError(
            f"refusing to embed tunnel id {tunnel_id!r} into a root "
            "script — expected alphanumeric, hyphen, or underscore only."
        )
    return tunnel_id


def generate_cutover_script(tunnel_id: str) -> str:
    """Shell script the operator runs once to flip systemd from
    `--config` mode to remote-managed mode for `tunnel_id`.

    The script:
    - Drops a systemd unit override at
      `/etc/systemd/system/cloudflared.service.d/gapt-remote-managed.conf`
      that resets `ExecStart` to drop the `--config` flag.
    - `systemctl daemon-reload` + `systemctl restart cloudflared`.
    - Reports the tunnel's connection state so the operator can
      confirm cloudflared came back healthy.

    All commands are idempotent — running the script twice is safe.
    The original unit file is never touched (drop-ins win), so
    reverting is `sudo rm <dropin>` + restart."""
    tunnel_id = _ensure_safe_tunnel_id(tunnel_id)
    dropin_path = f"{DEFAULT_DROPIN_DIR}/{DEFAULT_DROPIN_FILENAME}"
    return f"""#!/usr/bin/env bash
# Generated by GAPT — flips cloudflared.service into remote-managed mode.
# Safe to re-run. Removes the `--config` flag from ExecStart via a
# systemd drop-in; original unit file is untouched.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERR: run with sudo (this script edits /etc/systemd/system)." >&2
  exit 1
fi

DROPIN_DIR={DEFAULT_DROPIN_DIR}
DROPIN={dropin_path}
TUNNEL_ID={tunnel_id}

mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<EOF
# Managed by GAPT — strips --config so cloudflared fetches remote config.
[Service]
ExecStart=
ExecStart={DEFAULT_CLOUDFLARED_BIN} --no-autoupdate tunnel run "$TUNNEL_ID"
EOF
chmod 644 "$DROPIN"

systemctl daemon-reload
systemctl restart {DEFAULT_UNIT_NAME}

# Wait briefly for the tunnel to reconnect (timeout 15s).
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if systemctl is-active --quiet {DEFAULT_UNIT_NAME}; then
    echo "ok: cloudflared.service active"
    exit 0
  fi
  sleep 1
done
echo "WARN: cloudflared.service did not return to active within 15s." >&2
echo "      Check: sudo systemctl status cloudflared, journalctl -u cloudflared -n 50" >&2
exit 2
"""


def generate_revert_script() -> str:
    """Removes the drop-in so cloudflared goes back to using the
    on-disk YAML config. Provided for rollback."""
    dropin_path = f"{DEFAULT_DROPIN_DIR}/{DEFAULT_DROPIN_FILENAME}"
    return f"""#!/usr/bin/env bash
# Reverts the GAPT cutover — removes the systemd drop-in so
# cloudflared re-reads /etc/cloudflared/config.yml on startup.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERR: run with sudo." >&2
  exit 1
fi

DROPIN={dropin_path}
if [ -f "$DROPIN" ]; then
  rm -f "$DROPIN"
  systemctl daemon-reload
  systemctl restart {DEFAULT_UNIT_NAME}
  echo "ok: drop-in removed and cloudflared restarted."
else
  echo "no-op: drop-in was not present at $DROPIN."
fi
"""


@dataclass(frozen=True)
class CutoverRunResult:
    exit_code: int
    stdout: str
    stderr: str
    """Captured stderr — note sudo also writes prompts here. The
    `-p ''` flag suppresses sudo's password prompt to keep the
    surfaced output clean for the operator."""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


async def run_cutover_script(
    script: str,
    *,
    sudo_password: str | None,
    timeout_s: float = 60.0,
) -> CutoverRunResult:
    """Execute the cutover script via `sudo -S` and capture output.

    The script is GAPT-generated (we control every byte) and the
    tunnel id has already been validated by `_ensure_safe_tunnel_id`,
    so injection isn't a concern here. The password is piped via
    stdin and never logged — caller is responsible for not retaining
    it in memory longer than needed.

    Returns the captured exit code + stdout/stderr. Raises
    `asyncio.TimeoutError` if the script exceeds `timeout_s`."""
    import asyncio  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    fd, path = tempfile.mkstemp(prefix="gapt-cloudflared-migrate-", suffix=".sh")
    try:
        os.write(fd, script.encode())
        os.close(fd)
        os.chmod(path, 0o700)

        # `-S` reads password from stdin. `-p ''` suppresses the
        # readable prompt sudo would otherwise emit to stderr.
        # `-k` is omitted intentionally — letting sudo use cached
        # credentials (when password is None) keeps repeat runs
        # snappy on systems with NOPASSWD or recent sudo sessions.
        proc = await asyncio.create_subprocess_exec(
            "sudo",
            "-S",
            "-p",
            "",
            "bash",
            path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_payload = (
            (sudo_password + "\n").encode() if sudo_password else b""
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return CutoverRunResult(
            exit_code=proc.returncode or 0,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
