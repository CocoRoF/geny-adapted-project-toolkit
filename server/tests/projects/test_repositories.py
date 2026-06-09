"""ProjectRepository service — list, add, archive, primary lookup."""

from __future__ import annotations

import os

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gapt_server.db import enums
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.projects.repositories import (
    RepositoryCreate,
    RepositoryError,
    add,
    archive,
    list_for_project,
    primary_for_project,
)


def _require_dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn


def _reset_and_upgrade(sync_dsn: str) -> None:
    """Drop public schema, re-apply head — gives every test a clean
    DB so subpath uniqueness checks across tests don't bleed into
    each other."""
    import subprocess
    from pathlib import Path

    with psycopg.connect(sync_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    env = os.environ.copy()
    env["GAPT_POSTGRES_DSN"] = sync_dsn
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
    )


@pytest_asyncio.fixture
async def db():
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_dsn)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        # Seed a project + its auto-migrated repo row. The Phase N.4
        # alembic migration only runs the SELECT-INTO once at upgrade
        # time, so projects created AFTER head don't pick up the row
        # implicitly — the Phase N.4 ProjectService.create() path
        # (Milestone B) will be the one that inserts both rows
        # together. Here we mirror that pair manually for tests.
        from sqlalchemy import text
        await session.execute(
            text(
                "INSERT INTO projects (id, slug, display_name, git_remote_url, "
                "git_provider, default_compose_paths) VALUES (:id, :slug, :name, "
                ":url, 'github', '{}')"
            ),
            {
                "id": "01KTEST00000000000000000PR",
                "slug": "demo",
                "name": "Demo",
                "url": "https://example.com/demo.git",
            },
        )
        await session.execute(
            text(
                "INSERT INTO project_repositories ("
                "id, project_id, subpath, display_name, git_remote_url, "
                "git_provider, default_compose_paths, sort_order) "
                "VALUES (:id, :pid, '', :name, :url, 'github', '{}', 0)"
            ),
            {
                "id": new_ulid(),
                "pid": "01KTEST00000000000000000PR",
                "name": "Demo",
                "url": "https://example.com/demo.git",
            },
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_migration_creates_one_row_per_project(db) -> None:
    """The schema migration runs ``INSERT INTO project_repositories
    SELECT ... FROM projects`` for every existing project, creating
    one row at ``subpath=''`` carrying the legacy bundle. Confirms
    the down-migration path stays clean too."""
    rows = await list_for_project(db, project_id="01KTEST00000000000000000PR")
    assert len(rows) == 1
    only = rows[0]
    assert only.subpath == ""
    assert only.display_name == "Demo"
    assert only.git_remote_url == "https://example.com/demo.git"
    assert only.git_provider == enums.GitProvider.GITHUB
    assert only.sort_order == 0


@pytest.mark.asyncio
async def test_add_appends_a_repo_and_lists_in_sort_order(db) -> None:
    """Adding a non-zero-sort_order repo lands AFTER the primary in
    list_for_project. Confirms the explicit sort_order beats the
    fallback created_at tiebreaker."""
    await add(
        db,
        project_id="01KTEST00000000000000000PR",
        payload=RepositoryCreate(
            subpath="frontend",
            display_name="Frontend",
            git_remote_url="https://example.com/frontend.git",
            git_provider=enums.GitProvider.GITHUB,
            sort_order=1,
        ),
    )
    await db.commit()
    rows = await list_for_project(db, project_id="01KTEST00000000000000000PR")
    assert [r.subpath for r in rows] == ["", "frontend"]


@pytest.mark.asyncio
async def test_add_rejects_subpath_with_slash(db) -> None:
    """Subpath must be a single segment — slashes would let one repo
    nest inside another, breaking the flat layout invariant."""
    with pytest.raises(RepositoryError) as exc:
        await add(
            db,
            project_id="01KTEST00000000000000000PR",
            payload=RepositoryCreate(
                subpath="nested/path",
                display_name="Bad",
            ),
        )
    assert exc.value.code == "repository.subpath_invalid"


@pytest.mark.asyncio
async def test_add_rejects_duplicate_subpath(db) -> None:
    """The auto-migration already wrote a row at subpath='' for the
    seeded project. A second add with the same subpath must fail
    with a stable code so the API layer can map to 409."""
    with pytest.raises(RepositoryError) as exc:
        await add(
            db,
            project_id="01KTEST00000000000000000PR",
            payload=RepositoryCreate(
                subpath="",
                display_name="Duplicate root",
            ),
        )
    assert exc.value.code == "repository.subpath_conflict"


@pytest.mark.asyncio
async def test_primary_returns_lowest_sort_order_active(db) -> None:
    """primary_for_project is the back-compat hook for code that used
    to read Project.git_remote_url. It must return the first ACTIVE
    repo by sort_order — archived ones are skipped even if they
    have the lowest sort_order."""
    # Add a NEW repo at sort_order=-1 (would normally be first).
    added = await add(
        db,
        project_id="01KTEST00000000000000000PR",
        payload=RepositoryCreate(
            subpath="ahead",
            display_name="Ahead",
            sort_order=-1,
        ),
    )
    await db.commit()

    prim = await primary_for_project(db, project_id="01KTEST00000000000000000PR")
    assert prim is not None
    assert prim.subpath == "ahead"

    # Archive it — primary should fall back to the original migration row.
    await archive(db, repository_id=added.id)
    await db.commit()
    prim = await primary_for_project(db, project_id="01KTEST00000000000000000PR")
    assert prim is not None
    assert prim.subpath == ""


@pytest.mark.asyncio
async def test_primary_returns_none_for_empty_project(db) -> None:
    """Empty projects (no repos) are now a first-class concept — the
    workspace creation path inspects the return value and skips
    cloning when None."""
    from sqlalchemy import text
    # Drop the auto-migrated row from the seeded project to simulate
    # an explicitly empty project.
    await db.execute(
        text(
            "DELETE FROM project_repositories WHERE project_id = :pid"
        ),
        {"pid": "01KTEST00000000000000000PR"},
    )
    await db.commit()

    prim = await primary_for_project(db, project_id="01KTEST00000000000000000PR")
    assert prim is None
    rows = await list_for_project(db, project_id="01KTEST00000000000000000PR")
    assert rows == []
