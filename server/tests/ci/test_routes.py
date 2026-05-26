"""HTTP-level tests for `GET /api/projects/{pid}/ci/runs`."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.routers import ci as ci_router
from gapt_server.settings import Settings
from tests._helpers.fake_sandbox import FakeSandboxBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _require_dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn


def _reset_and_upgrade(sync_dsn: str) -> None:
    with psycopg.connect(sync_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    env = os.environ.copy()
    env["GAPT_POSTGRES_DSN"] = sync_dsn
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=SERVER_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


def test_parse_github_repo_variants() -> None:
    """Unit smoke for the URL parser — no DB needed."""
    f = ci_router.parse_github_repo
    assert f("https://github.com/cocoroF/geny-adapted-project-toolkit.git") == (
        "cocoroF/geny-adapted-project-toolkit"
    )
    assert f("https://github.com/owner/repo") == "owner/repo"
    assert f("git@github.com:owner/repo.git") == "owner/repo"
    assert f("owner/repo") == "owner/repo"
    assert f("https://example.com/foo/bar") is None


@dataclass
class _Fx:
    app: FastAPI


@pytest_asyncio.fixture
async def fx() -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(
        postgres_dsn=sync_dsn,
        ci_github_token="ghp_testtoken",
        auth_enabled=False,
    )
    audit = InMemoryAuditSink()
    sandbox = FakeSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app)
    finally:
        await container.aclose()


async def _create_project(client: AsyncClient) -> str:
    created = await client.post(
        "/api/projects",
        json={
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://github.com/owner/repo.git",
        },
    )
    return created.json()["id"]


def _stub_runner(runs_payload: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    """Build a runner that returns canned `gh run list --json` output."""

    async def runner(
        argv: list[str], env: dict[str, str], cwd: str | None
    ) -> tuple[int, str, str]:
        if "run" in argv and "list" in argv and "--json" in argv:
            return (0, json.dumps(runs_payload), "")
        return (1, "", f"unexpected argv: {argv}")

    return runner


@pytest.mark.asyncio
async def test_list_ci_runs_happy_path(fx: _Fx) -> None:
    from gapt_server.domains.git import GithubProvider

    canned_runs = [
        {
            "databaseId": 1,
            "displayTitle": "CI / build",
            "headBranch": "main",
            "headSha": "abc123",
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.com/owner/repo/actions/runs/1",
        }
    ]
    original = ci_router._build_provider

    def build_with_stub(token: str, repo: str) -> GithubProvider:
        return GithubProvider(
            token=token,
            repo=repo,
            runner=_stub_runner(canned_runs),
            gh_binary="/usr/bin/gh-stub",
        )

    ci_router._build_provider = build_with_stub  # type: ignore[assignment]
    try:
        async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
            project_id = await _create_project(client)
            resp = await client.get(f"/api/projects/{project_id}/ci/runs?limit=5")
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["id"] == 1
            assert rows[0]["status"] == "completed_success"
    finally:
        ci_router._build_provider = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_ci_runs_412_when_token_missing() -> None:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    # ci_github_token omitted
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)
    audit = InMemoryAuditSink()
    sandbox = FakeSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            project_id = await _create_project(client)
            resp = await client.get(f"/api/projects/{project_id}/ci/runs")
            assert resp.status_code == 412
            assert resp.json()["detail"]["code"] == "ci.no_token"
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_ci_runs_uses_system_scoped_vault_token(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """When `GAPT_CI_GITHUB_TOKEN` is unset but a `github_token` is
    saved in Settings (Vault, SYSTEM scope), the CI surface must pick
    it up and audit the read."""
    from gapt_server.db import enums as db_enums
    from gapt_server.domains.git import GithubProvider
    from gapt_server.domains.secrets.backend import EncryptedSqliteBackend
    from gapt_server.domains.secrets.vault import SecretVault
    from gapt_server.routers import secrets as secrets_router

    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(
        postgres_dsn=sync_dsn,
        vault_sqlite_path=str(tmp_path / "vault.sqlite"),
        auth_enabled=False,
    )  # NO ci_github_token
    audit = InMemoryAuditSink()
    sandbox = FakeSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    # The vault is a module-level singleton; pin ours so the test
    # doesn't reuse a sibling test's vault/audit-sink.
    backend = EncryptedSqliteBackend(
        db_path=settings.vault_sqlite_path,
        master_key=settings.vault_master_key,
    )
    vault = SecretVault(backend, audit_sink=audit)
    secrets_router.set_vault(vault)

    captured_tokens: list[str] = []
    original = ci_router._build_provider

    def build_with_stub(token: str, repo: str) -> GithubProvider:
        captured_tokens.append(token)
        return GithubProvider(
            token=token,
            repo=repo,
            runner=_stub_runner([]),
            gh_binary="/usr/bin/gh-stub",
        )

    ci_router._build_provider = build_with_stub  # type: ignore[assignment]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            project_id = await _create_project(client)

            # Store the system-scoped github_token directly via the vault.
            container_state = app.state.container  # type: ignore[attr-defined]
            async with container_state.session_factory() as db:
                await vault.store(
                    db,
                    scope=db_enums.SecretOwnerScope.SYSTEM,
                    owner_id=settings.admin_id,
                    key_name="github_token",
                    value="ghp_admin_scoped_secret",
                )
                await db.commit()

            resp = await client.get(f"/api/projects/{project_id}/ci/runs")
            assert resp.status_code == 200, resp.text

        # The provider received the system-scoped token, not the empty
        # settings fallback.
        assert captured_tokens == ["ghp_admin_scoped_secret"]
        # And the read was audited.
        reads = [e for e in audit.events if e.action == "secret.read"]
        assert any(e.payload.get("purpose") == "ci.list_runs" for e in reads)
    finally:
        ci_router._build_provider = original  # type: ignore[assignment]
        secrets_router.set_vault(None)  # type: ignore[arg-type]
        await container_state.aclose()
