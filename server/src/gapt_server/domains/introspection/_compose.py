"""Compose-file detector.

Walks the worktree root for `docker-compose*.yml` files, parses
each, and picks a primary service for the prod environment. The
heuristic: a service that publishes a port AND looks like a "web"
tier (port 3000/80/8080, name containing `front`/`web`/`nginx`,
or has a `build:` directive pointing at a frontend dir) wins.

Multi-file projects (the user shipped both a `.prod.yml` and a
`.dev.yml`) — we prefer the `.prod.yml` for prod_compose_path and
treat its base file as a secondary path that compose chains via
`-f`. If the user has only one file we just take that.

Why we trust compose over package.json for prod: compose is what the
user explicitly wrote down as "this is how the whole thing fits
together." Inferring from package.json is a guess; reading compose
is just transcribing what's there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gapt_server.domains.introspection._types import ProjectIntrospection


_COMPOSE_NAMES = (
    # In priority order; first match wins for prod_compose_path.
    "docker-compose.prod.yml",
    "docker-compose.production.yml",
    "compose.prod.yml",
    "compose.production.yml",
    "docker-compose.yml",
    "compose.yml",
    "docker-compose.yaml",
    "compose.yaml",
)

# Heuristic ports that mark a service as "the user-facing web tier".
# Order = preference when multiple services match.
_WEB_PORTS = (3000, 80, 8080, 5173, 4173, 5000, 8000)

# Service names that strongly suggest "web tier" (substring match).
_WEB_NAME_HINTS = ("frontend", "web", "ui", "app", "nginx", "next", "vite")
# Exact-match boost — a service literally named "frontend" beats one
# named "edit2me-frontend" or "admin-frontend" because the canonical
# convention is the unprefixed name. Compose files that follow this
# convention give us the right answer without the user touching
# `primary_service`. Order is preference when multiple exact matches
# (rare).
_WEB_EXACT_NAMES = ("frontend", "web", "app", "ui", "client", "site")


def detect_compose(root: Path) -> ProjectIntrospection:
    """Scan root for compose files; return a partial introspection.
    Empty/unmatched scans return UNKNOWN with confidence 0."""
    files = _find_compose_files(root)
    if not files:
        return ProjectIntrospection()
    primary_file = files[0]
    extra_files = files[1:]

    try:
        doc = _safe_load(primary_file)
    except Exception as exc:  # noqa: BLE001
        return ProjectIntrospection(
            notes=[f"compose: failed to parse {primary_file.name}: {exc}"]
        )

    services = doc.get("services", {}) if isinstance(doc, dict) else {}
    if not isinstance(services, dict) or not services:
        return ProjectIntrospection(
            notes=[f"compose: no services in {primary_file.name}"]
        )

    primary_service, primary_port = _pick_primary_service(services)
    build_required = any(_has_build(s) for s in services.values() if isinstance(s, dict))
    secondaries = _classify_services(services)

    notes = [
        f"compose: {primary_file.name} ({len(services)} services)",
    ]
    if primary_service:
        port_str = f":{primary_port}" if primary_port else " (no port detected)"
        notes.append(f"primary service → {primary_service}{port_str}")
    if extra_files:
        notes.append(
            "additional compose files: " + ", ".join(p.name for p in extra_files)
        )
    if build_required:
        notes.append("at least one service has `build:` — first deploy needs --build")

    return ProjectIntrospection(
        # Leave `kind` UNKNOWN — compose mode is orthogonal to the
        # framework choice (a compose project can be Next.js +
        # FastAPI + Postgres). Node/Python detectors fill in `kind`
        # from package.json / pyproject; we just flag has_compose.
        has_compose=True,
        secondary_stacks=secondaries,
        prod_compose_path=str(primary_file.relative_to(root)),
        prod_compose_paths=[str(p.relative_to(root)) for p in files],
        prod_primary_service=primary_service,
        prod_primary_port=primary_port,
        prod_build_required=build_required,
        env_files=_discover_env_files(root),
        env_examples=_discover_env_examples(root),
        confidence=0.9 if primary_service else 0.6,
        notes=notes,
        sources=["compose"],
    )


def _find_compose_files(root: Path) -> list[Path]:
    """Return compose files at the worktree root in priority order.
    We don't recurse: nested compose files are almost always for
    sub-services that aren't the project entrypoint."""
    seen: set[Path] = set()
    out: list[Path] = []
    for name in _COMPOSE_NAMES:
        p = root / name
        if p.is_file() and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _safe_load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _pick_primary_service(
    services: dict[str, Any],
) -> tuple[str | None, int | None]:
    """Apply the web-tier heuristic. Returns (service_name, port) or
    (None, None) when nothing looks like a web tier.

    Priority:
      1. Service name hint (frontend/web/ui/...) AND has a published
         or exposed port that's in `_WEB_PORTS`.
      2. Any service with a port in `_WEB_PORTS` (port preference
         order).
      3. First service with ANY port — last-resort.
      4. None — caller leaves it for the user.
    """
    candidates: list[tuple[int, str, int]] = []  # (priority_rank, name, port)
    for name, cfg in services.items():
        if not isinstance(cfg, dict):
            continue
        ports = _service_ports(cfg)
        if not ports:
            continue
        nlower = name.lower()
        name_exact = nlower in _WEB_EXACT_NAMES
        name_hit = name_exact or any(hint in nlower for hint in _WEB_NAME_HINTS)
        for port in ports:
            web_priority = _WEB_PORTS.index(port) if port in _WEB_PORTS else None
            # Ranks: lower = higher priority. Bands:
            #   0..9   exact-name match + recognised port
            #   10..19 prefix-name hint + recognised port
            #   20..29 recognised port, no name hint
            #   50     name hint but unknown port
            #   100    no signal — last resort
            if name_exact and web_priority is not None:
                rank = 0 + web_priority
            elif name_hit and web_priority is not None:
                rank = 10 + web_priority
            elif web_priority is not None:
                rank = 20 + web_priority
            elif name_hit:
                rank = 50
            else:
                rank = 100
            candidates.append((rank, name, port))
    if not candidates:
        return None, None
    candidates.sort()
    _, best_name, best_port = candidates[0]
    return best_name, best_port


def _service_ports(svc: dict[str, Any]) -> list[int]:
    """Extract ports declared on a service via `expose:` or `ports:`.
    `ports:` entries can be shaped like:
      "3000", "3000:3000", "127.0.0.1:3000:3000", {target: 3000, ...}
    We care about the *container-side* port (target) because that's
    what Caddy on gapt-net reaches."""
    out: list[int] = []
    for raw in svc.get("expose", []) or []:
        try:
            out.append(int(str(raw).split("/")[0]))
        except ValueError:
            continue
    for raw in svc.get("ports", []) or []:
        if isinstance(raw, dict):
            target = raw.get("target")
            if isinstance(target, int):
                out.append(target)
            elif isinstance(target, str):
                try:
                    out.append(int(target))
                except ValueError:
                    continue
        elif isinstance(raw, str):
            # "127.0.0.1:HOST:CONTAINER" → CONTAINER
            parts = raw.split(":")
            tail = parts[-1].split("/")[0]
            try:
                out.append(int(tail))
            except ValueError:
                continue
        elif isinstance(raw, int):
            out.append(raw)
    return out


def _has_build(svc: dict[str, Any]) -> bool:
    return "build" in svc


def _classify_services(services: dict[str, Any]) -> list[str]:
    """Heuristic labels for secondary services — surfaces in the
    UI's "What I found" so the user sees the full topology at a
    glance. Pure annotation; doesn't drive any auto-config."""
    out: list[str] = []
    for name, cfg in services.items():
        if not isinstance(cfg, dict):
            continue
        image = str(cfg.get("image", "")).lower()
        if "postgres" in image or "postgres" in name.lower():
            out.append(f"postgres ({name})")
        elif "redis" in image or "redis" in name.lower():
            out.append(f"redis ({name})")
        elif "minio" in image or "minio" in name.lower():
            out.append(f"minio ({name})")
        elif "nginx" in image or "nginx" in name.lower():
            out.append(f"nginx ({name})")
        elif cfg.get("build"):
            out.append(f"build:{name}")
    return out


def _discover_env_files(root: Path) -> list[str]:
    """Find real `.env` files (not examples). Looks at root + one
    level deep — enough to catch `backend/.env`, `frontend/.env`."""
    candidates: list[Path] = []
    candidates.extend(root.glob(".env"))
    candidates.extend(root.glob(".env.*"))
    for sub in root.iterdir():
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        candidates.extend(sub.glob(".env"))
    return [
        str(p.relative_to(root))
        for p in candidates
        if not p.name.endswith((".example", ".template", ".sample"))
    ]


def _discover_env_examples(root: Path) -> list[str]:
    """Same as `_discover_env_files` but for templates. The auto-
    config layer offers to seed `.env` from `.env.example` siblings."""
    candidates: list[Path] = []
    for suffix in (".env.example", ".env.template", ".env.sample"):
        candidates.extend(root.glob(suffix))
        for sub in root.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            candidates.extend(sub.glob(suffix))
    return [str(p.relative_to(root)) for p in candidates]
