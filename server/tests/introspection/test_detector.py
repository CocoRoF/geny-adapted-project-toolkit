"""Project detector — verify the introspection output across the
realistic project shapes GAPT users actually onboard.

Fixtures build tiny worktrees in `tmp_path` so the assertions stay
fast and deterministic — no network, no docker, no real
`docker-compose ps` calls. The detectors are pure file readers."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from gapt_server.domains.introspection import ProjectKind, detect


# ─── helpers ──────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _pkg(scripts: dict[str, str], deps: dict[str, str]) -> str:
    return json.dumps({"name": "fixture", "scripts": scripts, "dependencies": deps})


# ─── basic cases ──────────────────────────────────────────────────────


def test_empty_worktree_returns_unknown(tmp_path: Path) -> None:
    result = detect(tmp_path)
    assert result.kind == ProjectKind.UNKNOWN
    assert result.confidence == 0.0
    assert result.dev_command is None
    assert result.prod_compose_path is None


def test_missing_worktree_returns_unknown(tmp_path: Path) -> None:
    result = detect(tmp_path / "does-not-exist")
    assert result.kind == ProjectKind.UNKNOWN
    assert "not found" in " ".join(result.notes)


# ─── Node ─────────────────────────────────────────────────────────────


def test_nextjs_root_package(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        _pkg(
            {"dev": "next dev -p 3030", "build": "next build"},
            {"next": "15.0.0", "react": "19.0.0"},
        ),
    )
    _write(tmp_path / "next.config.ts", "export default {};")
    result = detect(tmp_path)
    assert result.kind == ProjectKind.NEXTJS
    assert result.dev_command == "npm run dev"
    # Port from the `-p 3030` flag overrides the default 3000.
    assert result.dev_port == 3030
    assert result.dev_cwd is None  # root
    assert result.needs_basepath is True
    assert result.basepath_config_file == "next.config.ts"


def test_vite_with_default_port(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        _pkg({"dev": "vite"}, {"vite": "^5.0.0"}),
    )
    _write(tmp_path / "vite.config.ts", "")
    result = detect(tmp_path)
    assert result.kind == ProjectKind.VITE
    assert result.dev_port == 5173  # framework default
    assert result.needs_basepath is True
    assert result.basepath_config_file == "vite.config.ts"


def test_express_root(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        _pkg({"start:dev": "nodemon server.js"}, {"express": "^4"}),
    )
    result = detect(tmp_path)
    assert result.kind == ProjectKind.EXPRESS
    assert result.dev_command == "npm run start:dev"
    # Express has no notion of basePath.
    assert result.needs_basepath is False


def test_monorepo_finds_frontend_under_src(tmp_path: Path) -> None:
    """hr_blog2.0 shape: `frontend/src/package.json` rather than the
    canonical `frontend/package.json`. Detector descends one level
    under each base path so this is still found."""
    _write(
        tmp_path / "frontend" / "src" / "package.json",
        _pkg({"dev": "next dev"}, {"next": "15.0.0"}),
    )
    _write(tmp_path / "frontend" / "src" / "next.config.ts", "export default {};")
    result = detect(tmp_path)
    assert result.kind == ProjectKind.NEXTJS
    assert result.dev_cwd == "frontend/src"
    assert result.basepath_config_file == "frontend/src/next.config.ts"


# ─── Python ───────────────────────────────────────────────────────────


def test_fastapi_via_pyproject(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        """\
        [project]
        name = "api"
        dependencies = ["fastapi", "uvicorn"]
        """,
    )
    result = detect(tmp_path)
    assert result.kind == ProjectKind.FASTAPI
    assert "uvicorn" in (result.dev_command or "")
    assert result.dev_port == 8000


def test_django_via_requirements(tmp_path: Path) -> None:
    _write(tmp_path / "requirements.txt", "django==5.0\npsycopg2-binary\n")
    result = detect(tmp_path)
    assert result.kind == ProjectKind.DJANGO
    assert "manage.py" in (result.dev_command or "")


def test_python_in_backend_subdir(tmp_path: Path) -> None:
    _write(
        tmp_path / "backend" / "pyproject.toml",
        '[project]\nname = "api"\ndependencies = ["fastapi"]\n',
    )
    result = detect(tmp_path)
    assert result.kind == ProjectKind.FASTAPI
    assert result.dev_cwd == "backend"


# ─── Compose ──────────────────────────────────────────────────────────


def test_compose_picks_named_frontend(tmp_path: Path) -> None:
    """When multiple services expose port 3000, the exact-name
    `frontend` should beat `edit2me-frontend`."""
    _write(
        tmp_path / "docker-compose.prod.yml",
        """\
        services:
          db:
            image: postgres:16
            expose: ["5432"]
          backend:
            build: ./backend
            expose: ["8000"]
          frontend:
            build: ./frontend
            expose: ["3000"]
          edit2me-frontend:
            build: ./edit2me
            expose: ["3000"]
        """,
    )
    result = detect(tmp_path)
    assert result.has_compose is True
    assert result.prod_compose_path == "docker-compose.prod.yml"
    assert result.prod_primary_service == "frontend"
    assert result.prod_primary_port == 3000
    assert result.prod_build_required is True


def test_compose_falls_back_to_only_service(tmp_path: Path) -> None:
    """Single-service compose: pick it even without name match."""
    _write(
        tmp_path / "docker-compose.yml",
        """\
        services:
          api:
            image: myapi
            ports: ["9090:9090"]
        """,
    )
    result = detect(tmp_path)
    assert result.prod_primary_service == "api"
    assert result.prod_primary_port == 9090


def test_compose_lists_secondary_stacks(tmp_path: Path) -> None:
    _write(
        tmp_path / "docker-compose.yml",
        """\
        services:
          db:
            image: postgres:16
          cache:
            image: redis:7
          web:
            build: .
            expose: ["3000"]
        """,
    )
    result = detect(tmp_path)
    stacks = result.secondary_stacks
    assert any("postgres" in s for s in stacks)
    assert any("redis" in s for s in stacks)


# ─── env discovery ────────────────────────────────────────────────────


def test_env_files_and_examples_found(tmp_path: Path) -> None:
    _write(tmp_path / "backend" / ".env", "SECRET=x")
    _write(tmp_path / "backend" / ".env.example", "SECRET=")
    _write(tmp_path / ".env", "ROOT=1")
    _write(tmp_path / "docker-compose.yml", "services:\n  web:\n    image: nginx\n    expose: ['80']\n")
    result = detect(tmp_path)
    assert ".env" in result.env_files
    assert "backend/.env" in result.env_files
    assert "backend/.env.example" in result.env_examples


# ─── multi-detector merge ─────────────────────────────────────────────


def test_compose_plus_nextjs_merges_fields(tmp_path: Path) -> None:
    """hr_blog2.0-shaped repo: compose handles prod, package.json
    handles dev. Both contribute non-overlapping fields and the
    merger keeps each detector's findings without clobbering."""
    _write(
        tmp_path / "docker-compose.prod.yml",
        """\
        services:
          backend:
            build: ./backend
            expose: ["8000"]
          frontend:
            build: ./frontend
            expose: ["3000"]
        """,
    )
    _write(
        tmp_path / "frontend" / "src" / "package.json",
        _pkg({"dev": "next dev"}, {"next": "15"}),
    )
    _write(tmp_path / "frontend" / "src" / "next.config.ts", "export default {};")
    result = detect(tmp_path)
    # Compose contributes prod_*, has_compose, build_required.
    assert result.has_compose is True
    assert result.prod_primary_service == "frontend"
    assert result.prod_primary_port == 3000
    assert result.prod_build_required is True
    # Node contributes kind, dev_*, basePath.
    assert result.kind == ProjectKind.NEXTJS
    assert result.dev_command == "npm run dev"
    assert result.needs_basepath is True
    # Sources list reflects both.
    assert "compose" in result.sources
    assert "package.json" in result.sources


# ─── resilience ───────────────────────────────────────────────────────


def test_malformed_compose_is_swallowed(tmp_path: Path) -> None:
    _write(tmp_path / "docker-compose.yml", ":: not yaml ::")
    _write(tmp_path / "package.json", _pkg({"dev": "next dev"}, {"next": "15"}))
    result = detect(tmp_path)
    # Node still detects; compose error lands in notes.
    assert result.kind == ProjectKind.NEXTJS
    assert any("failed to parse" in n for n in result.notes)


@pytest.mark.parametrize(
    "script,expected_port",
    [
        ("next dev -p 3030", 3030),
        ("next dev --port 4001", 4001),
        ("PORT=5555 vite", 5555),
        ("vite --port=6000", 6000),
        ("nodemon server.js", None),  # no port flag
    ],
)
def test_port_extraction_from_dev_script(
    tmp_path: Path, script: str, expected_port: int | None
) -> None:
    deps = {"next": "15"} if "next" in script else {"vite": "5"} if "vite" in script else {"express": "4"}
    _write(tmp_path / "package.json", _pkg({"dev": script}, deps))
    result = detect(tmp_path)
    if expected_port is not None:
        assert result.dev_port == expected_port
    # When the script doesn't carry a port flag and the framework
    # has a known default, the default is what surfaces.
