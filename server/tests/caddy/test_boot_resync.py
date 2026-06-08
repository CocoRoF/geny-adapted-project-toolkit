"""Boot-time Caddy ↔ DB resync — stale cleanup contract.

We test the cleanup pass against an in-memory fake Caddy client +
a fake session_factory that yields a stub Environment list. The
replay pass exercises real `docker compose` / `StackManager`
which can't be unit-tested without containers; covered manually
during live verification.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest


# ──────────────────────────────────────────── fakes ──


class _FakeClient:
    """Minimal subset of `CaddyAdminClient` the cleanup pass touches:
    GET routes + DELETE by id. Keeps the recorded delete calls so
    the test can assert."""

    def __init__(self, routes: list[dict[str, Any]]) -> None:
        self._routes = routes
        self.deleted: list[str] = []
        # Raise on these route_ids when delete is attempted (for the
        # "individual failure doesn't poison the loop" test).
        self.fail_on: set[str] = set()

    async def get(self, path: str) -> Any:
        assert path == "/config/apps/http/servers/main/routes", path
        return list(self._routes)

    async def delete(self, path: str) -> None:
        assert path.startswith("/id/"), path
        rid = path[len("/id/"):]
        if rid in self.fail_on:
            raise RuntimeError(f"forced fail on {rid}")
        self.deleted.append(rid)


@dataclass
class _FakeEnv:
    id: str = "env-1"
    project_id: str = "proj-1"
    name: str = "prod"
    deploy_target_config: dict[str, Any] | None = None


def _make_session_factory(envs: list[_FakeEnv]):
    """Fake `async_sessionmaker` — `async with` yields an object
    whose `execute()` returns a result with a `.scalars().all()`
    chain that drops the env list."""

    class _Scalars:
        def __init__(self, rows: list[_FakeEnv]) -> None:
            self._rows = rows

        def all(self) -> list[_FakeEnv]:
            return list(self._rows)

    class _Result:
        def __init__(self, rows: list[_FakeEnv]) -> None:
            self._rows = rows

        def scalars(self) -> _Scalars:
            return _Scalars(self._rows)

    class _Session:
        async def execute(self, _stmt: Any) -> _Result:
            return _Result(envs)

    @asynccontextmanager
    async def _factory():
        yield _Session()

    return _factory


@dataclass
class _FakeSettings:
    caddy_admin_url: str = "http://localhost:32019"
    caddy_preview_domain: str = "preview.example.com"
    caddy_apex_host: str | None = None
    caddy_subdomain_zone: str | None = None


# ─────────────────────────────────────── test helpers ──


def _build_routes(slugs: list[str], catchall_wildcard: str | None = None) -> list[dict]:
    rs: list[dict] = []
    for s in slugs:
        rs.append(
            {
                "@id": f"gapt-preview-{s}",
                "match": [{"host": [f"{s}.preview.example.com"]}],
                "handle": [{"handler": "reverse_proxy"}],
            }
        )
    if catchall_wildcard:
        rs.append(
            {
                "@id": "gapt-preview-zone-catchall",
                "match": [{"host": [catchall_wildcard]}],
                "handle": [{"handler": "static_response", "status_code": 404}],
            }
        )
    return rs


# ────────────────────────────────────── stale cleanup ──


@pytest.mark.asyncio
async def test_individual_routes_are_never_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot cleanup must NOT delete `gapt-preview-<slug>` routes
    from a DB-slug allowlist — the 2026-05-28 incident showed
    that heuristic strands healthy workspace/deploy routes whose
    slugs aren't visible from a pure Environment query. The
    catchall is the only thing this pass touches."""
    from gapt_server.domains.caddy import boot_resync

    fake_client = _FakeClient(
        _build_routes(["orphan", "blog", "prod-myenv-proj-1"])
    )
    monkeypatch.setattr(boot_resync, "CaddyAdminClient", lambda transport: fake_client)
    monkeypatch.setattr(boot_resync, "CaddyHttpTransport", lambda base_url: object())

    envs = [_FakeEnv(name="myenv", project_id="proj-1")]
    report = boot_resync.ResyncReport()
    await boot_resync.cleanup_stale_routes(
        session_factory=_make_session_factory(envs),
        settings=_FakeSettings(),
        report=report,
    )
    assert fake_client.deleted == []
    assert report.stale_deleted == []


@pytest.mark.asyncio
async def test_catchall_dropped_when_wildcard_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classic stuck-wildcard case: catch-all's host is
    `*.hrletsgo.me` but the current settings preview_domain is
    `gapt.hrletsgo.me`. The catch-all must be dropped so the next
    register emits one with the correct wildcard."""
    from gapt_server.domains.caddy import boot_resync

    fake_client = _FakeClient(
        _build_routes([], catchall_wildcard="*.hrletsgo.me")
    )
    monkeypatch.setattr(boot_resync, "CaddyAdminClient", lambda transport: fake_client)
    monkeypatch.setattr(boot_resync, "CaddyHttpTransport", lambda base_url: object())

    settings = _FakeSettings(caddy_preview_domain="gapt.hrletsgo.me")
    report = boot_resync.ResyncReport()
    await boot_resync.cleanup_stale_routes(
        session_factory=_make_session_factory([]),
        settings=settings,
        report=report,
    )
    assert "gapt-preview-zone-catchall" in fake_client.deleted
    assert report.catchall_reset is True


@pytest.mark.asyncio
async def test_catchall_kept_when_wildcard_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the catch-all's wildcard already matches the settings
    preview_domain, leave it alone — re-registering would churn
    the Caddy admin API for no reason."""
    from gapt_server.domains.caddy import boot_resync

    fake_client = _FakeClient(
        _build_routes([], catchall_wildcard="*.preview.example.com")
    )
    monkeypatch.setattr(boot_resync, "CaddyAdminClient", lambda transport: fake_client)
    monkeypatch.setattr(boot_resync, "CaddyHttpTransport", lambda base_url: object())

    report = boot_resync.ResyncReport()
    await boot_resync.cleanup_stale_routes(
        session_factory=_make_session_factory([]),
        settings=_FakeSettings(),
        report=report,
    )
    assert fake_client.deleted == []
    assert report.catchall_reset is False


@pytest.mark.asyncio
async def test_catchall_delete_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the catch-all delete itself fails (transient Caddy admin
    glitch), the function must NOT raise — the server still boots,
    the next register attempt re-creates the catch-all anyway.
    Best-effort semantics is load-bearing."""
    from gapt_server.domains.caddy import boot_resync

    fake_client = _FakeClient(
        _build_routes([], catchall_wildcard="*.wrong-zone.example")
    )
    fake_client.fail_on = {"gapt-preview-zone-catchall"}
    monkeypatch.setattr(boot_resync, "CaddyAdminClient", lambda transport: fake_client)
    monkeypatch.setattr(boot_resync, "CaddyHttpTransport", lambda base_url: object())

    report = boot_resync.ResyncReport()
    await boot_resync.cleanup_stale_routes(
        session_factory=_make_session_factory([]),
        settings=_FakeSettings(),
        report=report,
    )
    # No exception bubbled up; catchall_reset stayed False because
    # the delete didn't actually land.
    assert report.catchall_reset is False


@pytest.mark.asyncio
async def test_admin_url_unset_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `caddy_admin_url` is empty, the function returns
    without making any HTTP calls. Avoids surprise crashes in
    tests / CI where Caddy isn't available."""
    from gapt_server.domains.caddy import boot_resync

    called = {"http": False}

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        called["http"] = True
        raise AssertionError("should not be called")

    monkeypatch.setattr(boot_resync, "CaddyAdminClient", _explode)
    monkeypatch.setattr(boot_resync, "CaddyHttpTransport", _explode)

    settings = _FakeSettings(caddy_admin_url="")
    report = boot_resync.ResyncReport()
    await boot_resync.cleanup_stale_routes(
        session_factory=_make_session_factory([]),
        settings=settings,
        report=report,
    )
    assert called["http"] is False
    assert report.stale_deleted == []
