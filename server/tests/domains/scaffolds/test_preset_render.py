"""Phase N.2.3 — preset render snapshot tests.

Each preset's ``render(ctx)`` must:
  * return a non-empty dict[str, bytes] (except `empty`, which still
    returns README + .gitignore + LICENSE — that's NOT empty by file
    count, just empty by "no stack scaffold")
  * include README.md + .gitignore + LICENSE on every preset
  * include docker-compose.yml on every NON-empty preset (per Phase N
    plan §3 — empty deliberately has no compose)
  * be deterministic given the same RenderContext
  * have no Python f-string leakage (`{` / `}` placeholders left over)
"""

from __future__ import annotations

import re

import pytest

from gapt_server.domains.scaffolds.context import RenderContext
from gapt_server.domains.scaffolds.registry import all_presets, get_preset


def _ctx(**options: object) -> RenderContext:
    return RenderContext(
        project_name="Demo App",
        slug="demo-app",
        repo_name="demo-app",
        github_owner="alice",
        options=options,
    )


# Only flag identifiers we actually use as `.format()` slots — anything
# else (`{children}`, `{spread}`, JS destructuring) is legitimate output
# code, not a leak. Update this set when introducing a new slot.
_KNOWN_FORMAT_SLOTS = frozenset({
    "project_name",
    "slug",
    "repo_name",
    "github_owner",
    "primary_port",
    "db_name",
    "year",
    "database_section",
})


def _has_unrendered_placeholder(text: bytes) -> bool:
    """A leftover `{project_name}` etc. in scaffold output means an
    f-string template forgot a substitution. Common gotcha when the
    template has literal `{}` (e.g. JSON / CSS / TSX object literals
    or React `{children}`) and the operator forgot to escape with
    `{{...}}`.

    We only flag identifiers known to be format slots — JSX
    expressions like `{children}` are real code, not leaks.
    """
    for match in re.finditer(rb"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})", text):
        name = match.group(1).decode()
        if name in _KNOWN_FORMAT_SLOTS:
            return True
    return False


# ──────────────────────────────────────────────── every preset ──


@pytest.mark.parametrize("preset_id", [p.id for p in all_presets()])
def test_render_returns_readme_gitignore_license_at_minimum(preset_id: str) -> None:
    preset = get_preset(preset_id)
    # Use the schema defaults; covers the "operator clicked through"
    # quick path.
    cleaned = preset.validate_options({})
    files = preset.render(RenderContext(
        project_name="X",
        slug="x",
        repo_name="x",
        github_owner="alice",
        options=cleaned,
    ))
    assert "README.md" in files
    assert "LICENSE" in files
    assert ".gitignore" in files


@pytest.mark.parametrize("preset_id", [p.id for p in all_presets() if p.id != "empty"])
def test_non_empty_presets_ship_docker_compose(preset_id: str) -> None:
    preset = get_preset(preset_id)
    cleaned = preset.validate_options({})
    files = preset.render(RenderContext(
        project_name="X",
        slug="x",
        repo_name="x",
        github_owner="alice",
        options=cleaned,
    ))
    assert "docker-compose.yml" in files


def test_empty_preset_has_no_compose_or_dockerfile() -> None:
    """Phase N plan §3.1 — empty preset is intentionally no-stack.
    Operators bringing their own can add compose later."""
    preset = get_preset("empty")
    files = preset.render(_ctx())
    assert "docker-compose.yml" not in files
    assert not any(k == "Dockerfile" or k.endswith("/Dockerfile") for k in files)


@pytest.mark.parametrize("preset_id", [p.id for p in all_presets()])
def test_render_has_no_unrendered_placeholders(preset_id: str) -> None:
    """The f-string template / .format() boundary is fragile: a
    literal `{thing}` in a JSON/CSS/TS file body needs `{{thing}}` to
    survive `.format()`. This scans every output file for unescaped
    single-brace placeholders that look like Python identifiers — a
    smoking-gun for missed escape."""
    preset = get_preset(preset_id)
    cleaned = preset.validate_options({})
    files = preset.render(RenderContext(
        project_name="Sanity Check",
        slug="sanity-check",
        repo_name="sanity-check",
        github_owner="alice",
        options=cleaned,
    ))
    leaks: list[str] = []
    for path, content in files.items():
        if _has_unrendered_placeholder(content):
            # Surface the offending slot so the failure points at the leak.
            for m in re.finditer(rb"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})", content):
                if m.group(1).decode() in _KNOWN_FORMAT_SLOTS:
                    leaks.append(f"{path} → {m.group().decode()}")
                    break
    assert leaks == [], f"unrendered placeholders found: {leaks}"


@pytest.mark.parametrize("preset_id", [p.id for p in all_presets()])
def test_render_is_deterministic(preset_id: str) -> None:
    """Same ctx → same files. Catches accidental nondeterminism
    (e.g. random ids, timestamps with seconds resolution) that would
    make the GitHub commit hash thrash on retries."""
    preset = get_preset(preset_id)
    cleaned = preset.validate_options({})
    ctx = RenderContext(
        project_name="Demo",
        slug="demo",
        repo_name="demo",
        github_owner="alice",
        options=cleaned,
    )
    a = preset.render(ctx)
    b = preset.render(ctx)
    assert a == b


# ──────────────────────────── per-preset structure assertions ──


def test_fullstack_no_db_renders_3_services() -> None:
    preset = get_preset("fullstack_fastapi_nextjs")
    files = preset.render(_ctx(primary_port=80, database="none"))
    compose = files["docker-compose.yml"].decode()
    assert "backend:" in compose
    assert "frontend:" in compose
    assert "nginx:" in compose
    assert "postgres:" not in compose
    assert "backend/app/main.py" in files
    assert "frontend/app/page.tsx" in files
    assert "nginx/nginx.conf" in files


def test_fullstack_with_postgres_adds_db_service_and_alembic() -> None:
    preset = get_preset("fullstack_fastapi_nextjs")
    files = preset.render(_ctx(primary_port=80, database="postgres"))
    compose = files["docker-compose.yml"].decode()
    assert "postgres:" in compose
    assert "POSTGRES_DB" in compose
    assert "backend/alembic.ini" in files
    assert "backend/alembic/env.py" in files
    assert "backend/app/db.py" in files
    main_py = files["backend/app/main.py"].decode()
    assert "asyncpg" in main_py
    assert "init_pool" in main_py


def test_fullstack_primary_port_threads_through_to_compose() -> None:
    preset = get_preset("fullstack_fastapi_nextjs")
    files = preset.render(_ctx(primary_port=8080, database="none"))
    compose = files["docker-compose.yml"].decode()
    assert '"8080:80"' in compose


def test_backend_fastapi_compose_uses_chosen_port_and_db_name() -> None:
    preset = get_preset("backend_fastapi")
    files = preset.render(_ctx(primary_port=9000, db_name="mydb"))
    compose = files["docker-compose.yml"].decode()
    assert '"9000:8000"' in compose
    assert "POSTGRES_DB: mydb" in compose
    db_py = files["app/db.py"].decode()
    assert "/mydb" in db_py


def test_frontend_with_tailwind_includes_config_files() -> None:
    preset = get_preset("frontend_nextjs")
    files = preset.render(_ctx(primary_port=3000, with_tailwind=True))
    assert "tailwind.config.ts" in files
    assert "postcss.config.mjs" in files
    assert "app/globals.css" in files
    pkg = files["package.json"].decode()
    assert "tailwindcss" in pkg


def test_frontend_without_tailwind_skips_config_files() -> None:
    preset = get_preset("frontend_nextjs")
    files = preset.render(_ctx(primary_port=3000, with_tailwind=False))
    assert "tailwind.config.ts" not in files
    assert "postcss.config.mjs" not in files
    assert "app/globals.css" not in files
    pkg = files["package.json"].decode()
    assert "tailwindcss" not in pkg


def test_static_vite_dockerfile_is_multi_stage() -> None:
    preset = get_preset("static_vite")
    files = preset.render(_ctx(primary_port=80))
    dockerfile = files["Dockerfile"].decode()
    assert "FROM node:20-alpine AS build" in dockerfile
    assert "FROM nginx:1.27-alpine AS runner" in dockerfile
    assert "COPY --from=build" in dockerfile


def test_empty_preset_renders_readme_with_owner_and_repo() -> None:
    preset = get_preset("empty")
    files = preset.render(_ctx())
    readme = files["README.md"].decode()
    assert "alice/demo-app" in readme
    license_text = files["LICENSE"].decode()
    assert "Copyright" in license_text
    assert "alice" in license_text
