"""Python project detector — FastAPI / Django / Flask.

Reads `pyproject.toml`, `requirements*.txt`, and `setup.py` to
classify the framework + suggest a dev command. Like the Node
detector, this mainly contributes `dev_*` and `kind`. Compose
detection handles prod.

The pyproject.toml parser handles both PEP 621 (`project.dependencies`)
and Poetry (`tool.poetry.dependencies`) layouts. We don't try to
resolve `dependency-groups` or `optional-dependencies` deeply —
top-level scan is enough to identify the framework.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from gapt_server.domains.introspection._types import (
    ProjectIntrospection,
    ProjectKind,
)

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — server runs 3.12+
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]


_PYTHON_SEARCH_PATHS = (".", "backend", "server", "api", "app")

# Framework signatures: (import-name fragment, kind, default port,
# default dev command). The default dev command assumes standard
# layout; the user can edit before applying.
_FRAMEWORK_HINTS: list[tuple[str, ProjectKind, int, str]] = [
    ("fastapi", ProjectKind.FASTAPI, 8000, "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"),
    ("django", ProjectKind.DJANGO, 8000, "python manage.py runserver 0.0.0.0:8000"),
    ("flask", ProjectKind.FLASK, 5000, "flask run --host 0.0.0.0 --port 5000"),
]


def detect_python(root: Path) -> ProjectIntrospection:
    """Probe likely Python project roots; return UNKNOWN if no
    Python project files are reachable."""
    found = _find_python_root(root)
    if found is None:
        return ProjectIntrospection()
    project_dir, deps, source = found

    kind, default_port, default_cmd = _classify(deps)
    if kind == ProjectKind.UNKNOWN:
        return ProjectIntrospection(
            notes=[f"python: {source.name} found but no known framework dep"],
        )

    cwd_rel = str(project_dir.relative_to(root))
    if cwd_rel == ".":
        cwd_rel = None  # type: ignore[assignment]

    notes = [
        f"python: {source.relative_to(root)} → kind={kind.value}",
        f"dev command (suggested): `{default_cmd}`",
    ]

    # pytest is the de facto standard. Django ships its own runner
    # via `manage.py test`. Either way: when the framework dep is
    # in `deps` we set the obvious command. The user edits later.
    test_cmd: str | None = None
    if "pytest" in deps:
        test_cmd = "pytest"
    elif kind == ProjectKind.DJANGO:
        test_cmd = "python manage.py test"
    elif kind == ProjectKind.FLASK or kind == ProjectKind.FASTAPI:
        # FastAPI / Flask projects without pytest usually still
        # have it; default to pytest and let it fail loudly if not.
        test_cmd = "pytest"
    if test_cmd:
        notes.append(f"test command (suggested): `{test_cmd}`")

    # Pick an installer based on which file we found. pyproject.toml
    # → `pip install -e .` (editable so dev edits don't need
    # reinstall). requirements*.txt → `pip install -r requirements.txt`.
    # setup.py → same as pyproject. Each is idempotent on rerun
    # (pip detects "already satisfied").
    install_cmd: str | None = None
    src_name = source.name
    if src_name == "pyproject.toml" or src_name == "setup.py":
        install_cmd = "pip install -e ."
    elif src_name.startswith("requirements"):
        install_cmd = f"pip install -r {src_name}"
    if install_cmd:
        notes.append(f"install command: `{install_cmd}`")

    return ProjectIntrospection(
        kind=kind,
        dev_command=default_cmd,
        dev_port=default_port,
        dev_cwd=cwd_rel,
        install_command=install_cmd,
        test_command=test_cmd,
        confidence=0.6,
        notes=notes,
        sources=[source.name],
    )


def _find_python_root(root: Path) -> tuple[Path, set[str], Path] | None:
    """Return (project_dir, dep_names, source_file) for the first
    likely Python project we find. Looks at root + common subdirs.
    The `dep_names` set is lowercased package names (no versions).

    Priority: pyproject.toml > requirements*.txt > setup.py. First
    match wins.
    """
    for rel in _PYTHON_SEARCH_PATHS:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if not candidate.is_dir():
            continue
        pyproj = candidate / "pyproject.toml"
        if pyproj.is_file():
            deps = _parse_pyproject(pyproj)
            if deps:
                return candidate, deps, pyproj
        for req_name in ("requirements.txt", "requirements/base.txt", "requirements/prod.txt"):
            req = candidate / req_name
            if req.is_file():
                deps = _parse_requirements(req)
                if deps:
                    return candidate, deps, req
        setup = candidate / "setup.py"
        if setup.is_file():
            deps = _parse_setup_py(setup)
            return candidate, deps, setup
    return None


def _parse_pyproject(path: Path) -> set[str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — malformed pyproject; bail
        return set()
    names: set[str] = set()
    # PEP 621
    project = data.get("project", {})
    if isinstance(project, dict):
        names.update(_dep_names(project.get("dependencies") or []))
        opt = project.get("optional-dependencies") or {}
        if isinstance(opt, dict):
            for v in opt.values():
                names.update(_dep_names(v))
    # Poetry
    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
    if isinstance(poetry, dict):
        poetry_deps = poetry.get("dependencies") or {}
        if isinstance(poetry_deps, dict):
            names.update(k.lower() for k in poetry_deps.keys() if isinstance(k, str))
    return names


def _parse_requirements(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            # `package==1.2`, `package[extra]>=1`, `package`
            m = re.match(r"([A-Za-z0-9_\-.]+)", line)
            if m:
                names.add(m.group(1).lower())
    except OSError:
        return set()
    return names


def _parse_setup_py(path: Path) -> set[str]:
    """Coarse extract of install_requires names via regex — we
    don't execute setup.py. Misses dynamic lists; that's fine —
    setup.py shops with dynamic deps are vanishingly rare in
    framework-of-record projects."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    names: set[str] = set()
    for m in re.finditer(r"['\"]([A-Za-z0-9_\-.]+)\s*(?:[<>=]|$)", text):
        names.add(m.group(1).lower())
    return names


def _dep_names(deps: Any) -> set[str]:
    """Normalize PEP 508 strings to lowercased package names."""
    if not isinstance(deps, list):
        return set()
    out: set[str] = set()
    for dep in deps:
        if not isinstance(dep, str):
            continue
        m = re.match(r"([A-Za-z0-9_\-.]+)", dep.strip())
        if m:
            out.add(m.group(1).lower())
    return out


def _classify(deps: set[str]) -> tuple[ProjectKind, int | None, str]:
    for name, kind, port, cmd in _FRAMEWORK_HINTS:
        if name in deps:
            return kind, port, cmd
    return ProjectKind.UNKNOWN, None, ""
