"""HTTP-level tests for POST /api/sessions/oneshot."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import models
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth.session import InMemorySessionStore
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.routers.auth import set_session_store
from gapt_server.settings import Settings

if TYPE_CHECKING:
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


@dataclass
class _StubEvent:
    type: str
    data: dict[str, Any]


class _ScriptedPipeline:
    """Pipeline that yields a fixed event list when invoked. Stage
    matchers in `_map_pipeline_event` interpret the `type` field."""

    def __init__(self, events: list[_StubEvent]) -> None:
        self._events = events
        self.attached_hook_runner: Any = None

    def attach_runtime(self, **kwargs: Any) -> None:
        self.attached_hook_runner = kwargs.get("hook_runner")

    async def run_stream(self, _message: str) -> Any:
        for ev in self._events:
            yield ev


# Different tests want different scripts — module-level so the
# fixture-supplied factory can swap them.
_PIPELINE_SCRIPTS: dict[str, list[_StubEvent]] = {}


def _set_script(label: str, events: list[_StubEvent]) -> None:
    _PIPELINE_SCRIPTS[label] = events
    _PIPELINE_SCRIPTS["__current__"] = events


async def _stub_instantiate_pipeline(*_args: Any, **_kwargs: Any) -> _ScriptedPipeline:
    return _ScriptedPipeline(events=_PIPELINE_SCRIPTS.get("__current__", []))


@dataclass
class _Fx:
    app: FastAPI
    auth_enabled: bool


@pytest_asyncio.fixture
async def fx(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Fx]:
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    container.env_service.instantiate_pipeline = _stub_instantiate_pipeline  # type: ignore[assignment]

    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app, auth_enabled=False)
    finally:
        await container.aclose()
        _PIPELINE_SCRIPTS.clear()


async def _create_project_with_workspace(client: AsyncClient) -> tuple[str, str]:
    """Creates project + workspace. Returns (project_id, workspace_id)."""
    created = await client.post(
        "/api/projects",
        json={
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    wks = await client.post(
        f"/api/projects/{project_id}/workspaces",
        json={"branch": "main"},
    )
    assert wks.status_code == 201, wks.text
    return project_id, wks.json()["id"]


@pytest.mark.asyncio
async def test_oneshot_aggregates_text_chunks(fx: _Fx) -> None:
    _set_script(
        "happy",
        [
            _StubEvent(type="text", data={"text": "hello "}),
            _StubEvent(type="text", data={"text": "world"}),
        ],
    )
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, workspace_id = await _create_project_with_workspace(client)

        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": workspace_id, "message": "ping"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["text"] == "hello world"
        # The trailing `done` event arrives via _run_with_lifecycle.
        kinds = [e["kind"] for e in body["events"]]
        assert "text" in kinds
        assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_oneshot_captures_tool_calls(fx: _Fx) -> None:
    _set_script(
        "tools",
        [
            _StubEvent(type="tool.invoke", data={"name": "gapt_edit", "input": {"path": "a.py"}}),
            _StubEvent(type="tool.result", data={"name": "gapt_edit", "output": "ok"}),
            _StubEvent(type="text", data={"text": "done editing"}),
        ],
    )
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, workspace_id = await _create_project_with_workspace(client)
        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": workspace_id, "message": "edit a.py"},
        )
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["tool_calls"]) == 1
        assert body["tool_calls"][0]["name"] == "gapt_edit"
        assert len(body["tool_results"]) == 1
        assert body["text"] == "done editing"


@pytest.mark.asyncio
async def test_oneshot_surfaces_pipeline_error(fx: _Fx) -> None:
    _set_script(
        "err",
        [
            _StubEvent(
                type="pipeline.error",
                data={"exec_code": "exec.boom", "reason": "stage 5 failed"},
            ),
        ],
    )
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, workspace_id = await _create_project_with_workspace(client)
        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": workspace_id, "message": "x"},
        )
        body = resp.json()
        assert body["status"] == "error"
        assert body["exec_code"] == "exec.boom"
        assert body["error_reason"] == "stage 5 failed"


@pytest.mark.asyncio
async def test_oneshot_archives_session_on_completion(fx: _Fx) -> None:
    _set_script("brief", [_StubEvent(type="text", data={"text": "ok"})])
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, workspace_id = await _create_project_with_workspace(client)
        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": workspace_id, "message": "hi"},
        )
        session_id = resp.json()["session_id"]

        container = client._transport.app.state.container  # type: ignore[attr-defined]
        async with container.session_factory() as db:
            row = (
                await db.execute(
                    select(models.AgentSession).where(models.AgentSession.id == session_id)
                )
            ).scalar_one()
        assert row.status.value == "archived"


@pytest.mark.asyncio
async def test_oneshot_workspace_not_found(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _create_project_with_workspace(client)
        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": "01KS90000000000000000XXXXX", "message": "hi"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "workspace.not_found"


@pytest.mark.asyncio
async def test_oneshot_requires_auth() -> None:
    """A separate app instance with auth_enabled=True is used here so
    the oneshot 401 path stays covered."""
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)  # auth_enabled defaults to True
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    container.env_service.instantiate_pipeline = _stub_instantiate_pipeline  # type: ignore[assignment]
    set_session_store(InMemorySessionStore())
    app = create_app(settings=settings, container=container)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/sessions/oneshot",
                json={"workspace_id": "01KS90000000000000000XXXXX", "message": "hi"},
            )
            assert resp.status_code == 401
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_oneshot_timeout_returns_status_timeout(fx: _Fx) -> None:
    # No events emitted — the pipeline.run_stream returns immediately.
    # But _run_with_lifecycle then publishes `done`, so the drain
    # finishes cleanly. To force a real timeout we need the pipeline
    # to *hang*. We patch the runner's default behaviour at module
    # level via a fake event source.
    class _HangPipeline:
        attached_hook_runner: Any = None

        def attach_runtime(self, **kwargs: Any) -> None:
            self.attached_hook_runner = kwargs.get("hook_runner")

        async def run_stream(self, _message: str) -> Any:
            await asyncio.sleep(10)
            if False:
                yield None  # pragma: no cover

    async def _hang_factory(*_args: Any, **_kwargs: Any) -> _HangPipeline:
        return _HangPipeline()

    fx.app.state.container.env_service.instantiate_pipeline = _hang_factory  # type: ignore[assignment]

    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, workspace_id = await _create_project_with_workspace(client)
        resp = await client.post(
            "/api/sessions/oneshot",
            json={"workspace_id": workspace_id, "message": "hi", "timeout_s": 1},
        )
        body = resp.json()
        assert body["status"] == "timeout"
        assert body["exec_code"] == "exec.session.timeout"
