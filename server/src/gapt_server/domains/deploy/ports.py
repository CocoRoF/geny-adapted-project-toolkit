"""Host-port preflight for local compose deploys.

`docker compose up` fails with "Bind for 0.0.0.0:<port> failed: port
is already allocated" whenever the user's compose file publishes a
host port that something else already holds — another GAPT-managed
stack, a dev service, or any unrelated host process. GAPT owns the
infra picture (it can enumerate host listeners AND every docker-
published port), so instead of bouncing the failure to the operator
it reconciles before the `up`:

  1. Resolve the effective compose model (`docker compose config`)
     and collect every `published` host port.
  2. Build the occupied-port map: host LISTEN sockets + ports
     published by containers OUTSIDE this compose project (a
     re-deploy of the same stack must not self-conflict).
  3. Per the env's `target_options.ports_policy`:
       - "auto" (default): remap each conflicting publish to the
         nearest free port and emit a generated override file
         (`ports: !override [...]`) chained as the last `-f`.
       - "strict": fail fast with a structured error naming the
         holder — for operators who treat the host port as a
         contract.
       - "unpublish": strip ALL host publishing — the stack is
         reachable only through GAPT's Caddy routing (which dials
         the container over gapt-net and never needs a host port).

Pure logic lives here (parsers, planner, renderer) so it unit-tests
without docker; `LocalComposeTarget` drives the subprocess calls.

Why `!override`: compose merges `ports:` lists across `-f` files by
APPENDING — a plain override would publish both the old and the new
port. The `!override` YAML tag (compose ≥2.24) replaces the list
wholesale, which is also why the generated file re-states a
service's untouched entries alongside the remapped ones.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from gapt_server.domains.deploy.protocol import DeployTargetError

# Scan window for the "nearest free port" walk. Wide enough that a
# busy host still resolves, narrow enough to stay in the same
# numeric neighbourhood the operator expects.
_REMAP_SCAN_RANGE = 200

_VALID_POLICIES = ("auto", "strict", "unpublish")


@dataclass(frozen=True)
class PortPlan:
    """Outcome of the preflight planner.

    ``override_yaml`` is None when nothing needs to change (no
    conflicts under "auto", or no published ports at all). ``remaps``
    maps ``(service, requested_host_port) -> final_host_port`` for
    audit/log purposes."""

    override_yaml: str | None
    log_lines: list[str] = field(default_factory=list)
    remaps: dict[tuple[str, int], int] = field(default_factory=dict)


def _entry_published(entry: Any) -> int | None:
    """Pull the host (published) port out of one compose `ports`
    entry. Handles the long form (`{"published": 3000, ...}` — value
    may be int or str) and the short string forms ("3000:3000",
    "127.0.0.1:3000:3000/tcp", "3000"). Returns None for expose-only
    entries, ranges ("3000-3005") and anything unparseable — those
    are preserved verbatim and never remapped."""
    if isinstance(entry, dict):
        raw = entry.get("published")
        if raw is None:
            return None
        try:
            return int(str(raw))
        except ValueError:
            return None  # range or interpolation leftover
    if isinstance(entry, int):
        return None  # bare container port — no host publish
    if isinstance(entry, str):
        spec = entry.split("/", 1)[0]
        parts = spec.rsplit(":", 2)
        if len(parts) == 1:
            return None  # "3000" — container port only
        host_part = parts[-2]
        try:
            return int(host_part)
        except ValueError:
            return None  # "1.2.3.4" chunk of an ip:host:container or a range
    return None


def _entry_with_published(entry: Any, new_published: int) -> dict[str, Any]:
    """Rewrite one entry's host port, normalising to the long form so
    the override file is unambiguous regardless of the input shape."""
    if isinstance(entry, dict):
        out = dict(entry)
        out["published"] = str(new_published)
        return out
    # Short string form — split into pieces.
    spec = str(entry)
    proto = "tcp"
    if "/" in spec:
        spec, proto = spec.split("/", 1)
    parts = spec.rsplit(":", 2)
    target = parts[-1]
    out = {"target": int(target), "published": str(new_published), "protocol": proto}
    if len(parts) == 3:
        out["host_ip"] = parts[0]
    return out


def _entry_without_published(entry: Any) -> dict[str, Any] | str | int:
    """Strip the host publish from one entry ("unpublish" policy),
    keeping the container-side port visible for in-network use."""
    published = _entry_published(entry)
    if published is None:
        return entry
    if isinstance(entry, dict):
        out = {k: v for k, v in entry.items() if k not in ("published", "host_ip")}
        return out
    spec = str(entry)
    proto = ""
    if "/" in spec:
        spec, proto_part = spec.split("/", 1)
        proto = f"/{proto_part}"
    target = spec.rsplit(":", 1)[-1]
    return f"{target}{proto}"


def parse_compose_ports(config: dict[str, Any]) -> dict[str, list[Any]]:
    """`docker compose config` model → ``{service: [raw port entries]}``
    for services that declare any `ports`. Raw entries are kept
    as-is so the override renderer can re-state untouched ones
    faithfully."""
    out: dict[str, list[Any]] = {}
    services = config.get("services")
    if not isinstance(services, dict):
        return out
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        ports = svc.get("ports")
        if isinstance(ports, list) and ports:
            out[str(name)] = list(ports)
    return out


def parse_proc_net_listen_ports(*proc_texts: str) -> set[int]:
    """LISTEN-state local ports from `/proc/net/tcp` / `tcp6` content."""
    found: set[int] = set()
    for text in proc_texts:
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4 or parts[3].upper() != "0A":
                continue
            _, _, port_hex = parts[1].rpartition(":")
            try:
                found.add(int(port_hex, 16))
            except ValueError:
                continue
    return found


_PS_PORT_RE = re.compile(r"(?:[0-9.]+|\[[0-9a-fA-F:.]*\]|::):(\d+)->")


def parse_docker_published_ports(
    ps_output: str, *, exclude_project: str
) -> dict[int, str]:
    """`docker ps --format json` (NDJSON) → ``{host_port: holder}``.

    Rows belonging to ``exclude_project`` (compose label match) are
    skipped — re-deploying a stack onto the ports it already holds is
    the normal case, not a conflict. Holder names give the operator
    an actionable log line ("held by container X")."""
    occupied: dict[int, str] = {}
    for raw in ps_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        labels = str(row.get("Labels") or "")
        if f"com.docker.compose.project={exclude_project}" in labels.split(","):
            continue
        name = str(row.get("Names") or row.get("ID") or "?")
        for match in _PS_PORT_RE.finditer(str(row.get("Ports") or "")):
            try:
                occupied.setdefault(int(match.group(1)), name)
            except ValueError:
                continue
    return occupied


def docker_project_ports(ps_output: str, *, project: str) -> set[int]:
    """Host ports published by THIS compose project's own containers.

    Needed to clean the host-listener set: docker's userland proxy
    LISTENs on every published port, so a re-deploy would see its own
    previous run in `/proc/net/tcp` and "conflict" with itself. The
    docker-ps label match identifies which listeners are ours."""
    own: set[int] = set()
    for raw in ps_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        labels = str(row.get("Labels") or "")
        if f"com.docker.compose.project={project}" not in labels.split(","):
            continue
        for match in _PS_PORT_RE.finditer(str(row.get("Ports") or "")):
            try:
                own.add(int(match.group(1)))
            except ValueError:
                continue
    return own


def _allocate(wanted: int, taken: set[int]) -> int | None:
    """Nearest free port at or above ``wanted + 1`` within the scan
    window. None when the whole window is exhausted (caller falls
    back to an ephemeral publish)."""
    for candidate in range(wanted + 1, min(wanted + 1 + _REMAP_SCAN_RANGE, 65536)):
        if candidate not in taken:
            return candidate
    return None


def render_override_yaml(services_ports: dict[str, list[Any]]) -> str:
    """Render the override compose file. Entries are emitted as
    inline JSON — valid YAML, and json.dumps handles all quoting."""
    lines = [
        "# Generated by GAPT port preflight — do not edit; rewritten on",
        "# every deploy. `!override` replaces (not merges) the ports list.",
        "services:",
    ]
    for service in sorted(services_ports):
        lines.append(f"  {json.dumps(service)}:")
        entries = services_ports[service]
        if not entries:
            lines.append("    ports: !override []")
            continue
        lines.append("    ports: !override")
        for entry in entries:
            lines.append(f"      - {json.dumps(entry)}")
    return "\n".join(lines) + "\n"


def plan_port_overrides(
    *,
    services_ports: dict[str, list[Any]],
    occupied: dict[int, str],
    policy: str = "auto",
) -> PortPlan:
    """Decide what (if anything) to override. See module docstring
    for the three policies. Raises ``DeployTargetError`` with code
    ``deploy.port_conflict`` under "strict" when a conflict exists,
    and ``deploy.invalid_ports_policy`` for unknown policy values
    (fail-fast beats silently treating a typo as "auto")."""
    if policy not in _VALID_POLICIES:
        raise DeployTargetError(
            "deploy.invalid_ports_policy",
            f"ports_policy must be one of {_VALID_POLICIES}, got {policy!r}",
        )

    if policy == "unpublish":
        stripped = {
            svc: [_entry_without_published(e) for e in entries]
            for svc, entries in services_ports.items()
        }
        changed = {
            svc: entries
            for svc, entries in stripped.items()
            if entries != services_ports[svc]
        }
        if not changed:
            return PortPlan(override_yaml=None)
        return PortPlan(
            override_yaml=render_override_yaml(changed),
            log_lines=[
                "[gapt] ports_policy=unpublish — host port publishing stripped "
                f"for {', '.join(sorted(changed))}; the stack is reachable via "
                "the GAPT preview route (Caddy dials it over the docker network)."
            ],
        )

    # Conflict detection shared by auto + strict. `taken` grows as we
    # allocate so two remaps (or two services wanting the same port
    # inside one stack) can't land on the same replacement.
    taken: set[int] = set(occupied)
    remaps: dict[tuple[str, int], int] = {}
    new_lists: dict[str, list[Any]] = {}
    log_lines: list[str] = []
    for service in sorted(services_ports):
        entries = services_ports[service]
        rewritten: list[Any] = []
        service_changed = False
        for entry in entries:
            published = _entry_published(entry)
            if published is None:
                rewritten.append(entry)
                continue
            if published not in taken:
                taken.add(published)
                rewritten.append(entry)
                continue
            holder = occupied.get(published)
            holder_note = f" (held by {holder})" if holder else ""
            if policy == "strict":
                raise DeployTargetError(
                    "deploy.port_conflict",
                    (
                        f"host port {published} requested by service "
                        f"{service!r} is already in use{holder_note}. "
                        "Free the port, or set ports_policy=auto on this "
                        "environment to let GAPT remap it."
                    ),
                )
            replacement = _allocate(published, taken)
            if replacement is None:
                replacement = 0  # ephemeral — compose picks a free one
            else:
                taken.add(replacement)
            remaps[(service, published)] = replacement
            rewritten.append(_entry_with_published(entry, replacement))
            shown = str(replacement) if replacement else "an ephemeral port"
            log_lines.append(
                f"[gapt] host port {published} is already in use{holder_note} "
                f"→ {service} now publishes {shown} instead."
            )
            service_changed = True
        if service_changed:
            new_lists[service] = rewritten

    if not new_lists:
        return PortPlan(override_yaml=None)
    log_lines.append(
        "[gapt] preview routing is unaffected — Caddy reaches the stack "
        "over the docker network, not host ports."
    )
    return PortPlan(
        override_yaml=render_override_yaml(new_lists),
        log_lines=log_lines,
        remaps=remaps,
    )
