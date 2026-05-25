"""Node.js / package.json detector.

Identifies the framework (Next.js / Vite / Express / plain Node)
from package.json dependencies and surfaces the dev command + port
from the `scripts` block. For monorepos with multiple package.jsons,
we pick the most-specific one closest to a `dev`-shaped script.

Output is mainly `dev_*` fields and a `kind` hint — compose detector
fills `prod_*` separately. When the project has BOTH a compose file
and a frontend package.json (the common hr_blog2.0 layout), the
merger keeps compose's `prod_*` and Node's `dev_*`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gapt_server.domains.introspection._types import (
    ProjectIntrospection,
    ProjectKind,
)


# Framework signatures — first dep that matches wins.
_FRAMEWORK_DEPS: list[tuple[str, ProjectKind, int]] = [
    # (dep_name, kind, default_dev_port)
    ("next", ProjectKind.NEXTJS, 3000),
    ("nuxt", ProjectKind.VITE, 3000),
    ("vite", ProjectKind.VITE, 5173),
    ("@sveltejs/kit", ProjectKind.VITE, 5173),
    ("express", ProjectKind.EXPRESS, 3000),
    ("fastify", ProjectKind.EXPRESS, 3000),
]

# Likely monorepo locations for the user-facing package.json.
# Searched in priority order — the first dir whose package.json
# parses + has framework deps wins. Each entry is then walked one
# more level (`<entry>/<anything>`) to catch projects that nest
# their Next config under `src/` like hr_blog2.0 does.
_PACKAGE_JSON_BASES = (
    ".",
    "frontend",
    "web",
    "apps/web",
    "apps/frontend",
    "packages/web",
    "packages/frontend",
    "client",
    "ui",
)
# Dirs we never descend into when searching — they're heavy and
# never the user-facing root.
_SEARCH_PRUNE = frozenset({"node_modules", ".next", "dist", "build", ".git", ".turbo"})


def detect_node(root: Path) -> ProjectIntrospection:
    """Find the most relevant package.json and parse it. Returns
    UNKNOWN/no-fields if no package.json is present anywhere we
    look."""
    pkg_path = _find_package_json(root)
    if pkg_path is None:
        return ProjectIntrospection()
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ProjectIntrospection(
            notes=[f"node: failed to parse {pkg_path.name}: {exc}"]
        )

    all_deps = _all_deps(pkg)
    kind, default_port = _classify(all_deps)
    dev_cmd, dev_port_from_script = _parse_dev_script(pkg.get("scripts", {}))
    dev_port = dev_port_from_script or default_port

    # Express + Fastify aren't basePath-aware — only Next/Nuxt/Vite
    # have the build-time prefix concept.
    needs_basepath = kind in {ProjectKind.NEXTJS, ProjectKind.VITE}
    basepath_file = None
    if kind == ProjectKind.NEXTJS:
        # next.config.* lives in the package root (sibling of
        # package.json), commonly under `src/next.config.ts` for
        # apps that put their Next config inside the src tree.
        for candidate in ("next.config.ts", "next.config.js", "src/next.config.ts"):
            cfg = pkg_path.parent / candidate
            if cfg.is_file():
                basepath_file = str(cfg.relative_to(root))
                break
    elif kind == ProjectKind.VITE:
        for candidate in ("vite.config.ts", "vite.config.js"):
            cfg = pkg_path.parent / candidate
            if cfg.is_file():
                basepath_file = str(cfg.relative_to(root))
                break

    cwd_rel = str(pkg_path.parent.relative_to(root))
    if cwd_rel == ".":
        cwd_rel = None  # type: ignore[assignment]

    notes = [f"node: {pkg_path.relative_to(root)} → kind={kind.value}"]
    if dev_cmd:
        notes.append(f"dev command: `{dev_cmd}`" + (f" (port {dev_port})" if dev_port else ""))
    if needs_basepath and basepath_file:
        notes.append(f"basePath-capable framework — config at {basepath_file}")

    test_cmd = _parse_test_script(pkg.get("scripts", {}))
    if test_cmd:
        notes.append(f"test command: `{test_cmd}`")

    return ProjectIntrospection(
        kind=kind,
        dev_command=dev_cmd,
        dev_port=dev_port,
        dev_cwd=cwd_rel,
        test_command=test_cmd,
        needs_basepath=needs_basepath,
        basepath_config_file=basepath_file,
        confidence=0.7 if dev_cmd else 0.4,
        notes=notes,
        sources=["package.json"],
    )


def _find_package_json(root: Path) -> Path | None:
    """Probe each base path for `package.json`; if missing, descend
    one level into its subdirs (still skipping `node_modules` etc.).
    Stops at the first hit so monorepos with multiple packages just
    pick the one closest to the entry point we'd expect.

    Walking is bounded — bases + one sub-level — so this stays O(N)
    in the number of top-level dirs even on huge repos."""
    root_resolved = root.resolve()
    for rel in _PACKAGE_JSON_BASES:
        base = (root / rel).resolve()
        try:
            base.relative_to(root_resolved)
        except ValueError:
            continue
        if not base.is_dir():
            continue
        direct = base / "package.json"
        if direct.is_file():
            return direct
        # One-level descent — catches `frontend/src/package.json`,
        # `apps/web/app/package.json` style nesting.
        for sub in sorted(base.iterdir()):
            if not sub.is_dir() or sub.name in _SEARCH_PRUNE or sub.name.startswith("."):
                continue
            nested = sub / "package.json"
            if nested.is_file():
                return nested
    return None


def _all_deps(pkg: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = pkg.get(key) or {}
        if isinstance(block, dict):
            for k, v in block.items():
                if isinstance(k, str):
                    out[k] = str(v) if v is not None else ""
    return out


def _classify(deps: dict[str, str]) -> tuple[ProjectKind, int | None]:
    for dep_name, kind, default_port in _FRAMEWORK_DEPS:
        if dep_name in deps:
            return kind, default_port
    if deps:
        return ProjectKind.EXPRESS, None  # bare Node app — generic Express-class
    return ProjectKind.UNKNOWN, None


_DEV_SCRIPT_NAMES = ("dev", "start:dev", "serve", "start")


def _parse_dev_script(scripts: Any) -> tuple[str | None, int | None]:
    """Find the dev-shaped script and extract a port if present.

    Order: `dev` > `start:dev` > `serve` > `start`. The port hint
    comes from common flags (`-p 3000`, `--port 3000`,
    `PORT=3000 ...`) — best-effort.
    """
    if not isinstance(scripts, dict):
        return None, None
    chosen_name: str | None = None
    chosen_cmd: str | None = None
    for name in _DEV_SCRIPT_NAMES:
        cmd = scripts.get(name)
        if isinstance(cmd, str) and cmd.strip():
            chosen_name = name
            chosen_cmd = cmd.strip()
            break
    if chosen_cmd is None:
        return None, None
    full = f"npm run {chosen_name}"
    port = _extract_port(chosen_cmd)
    return full, port


def _parse_test_script(scripts: Any) -> str | None:
    """Return `npm test` style command for the project's test suite.

    Looks for `test`, `test:unit`, or `vitest`-equivalent. Many
    packages don't ship a `test` script; we return None in that case
    rather than guessing — the user can hand-fill if needed.
    """
    if not isinstance(scripts, dict):
        return None
    for name in ("test", "test:unit", "test:run", "vitest"):
        cmd = scripts.get(name)
        if isinstance(cmd, str) and cmd.strip():
            return f"npm run {name}" if name != "vitest" else "npm run vitest"
    return None


_PORT_PATTERNS = (
    re.compile(r"(?:^|\s)(?:-p|--port)[=\s]+(\d{2,5})\b"),
    re.compile(r"\bPORT[=\s](\d{2,5})\b"),
    re.compile(r"(?::|--port=)(\d{2,5})\b"),
)


def _extract_port(cmd: str) -> int | None:
    for pat in _PORT_PATTERNS:
        m = pat.search(cmd)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None
