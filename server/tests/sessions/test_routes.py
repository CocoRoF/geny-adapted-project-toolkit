"""HTTP-level tests for session routes — closes M1-E2."""

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

from gapt_server.agent.session_registry import SessionNotFound
from gapt_server.agent.streaming import SessionEventKind
from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import enums
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.settings import Settings
from tests._helpers.fake_sandbox import FakeSandboxBackend
from tests._helpers.db_guard import assert_safe_to_reset

if TYPE_CHECKING:
    from fastapi import FastAPI

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _require_dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn


def _reset_and_upgrade(sync_dsn: str) -> None:
    assert_safe_to_reset(sync_dsn)
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
class _Fx:
    app: FastAPI
    audit: InMemoryAuditSink


class _StubPipeline:
    """Pipeline stand-in — we only need `attach_runtime` to be callable.
    The default invoke runner is overridden in tests via direct registry
    access where needed."""

    def __init__(self) -> None:
        self.attached_hook_runner: Any = None
        self.attached_kwargs: dict[str, Any] = {}

    def attach_runtime(self, **kwargs: Any) -> None:
        self.attached_kwargs = kwargs
        self.attached_hook_runner = kwargs.get("hook_runner")

    async def run_stream(self, message: str) -> Any:
        if False:
            yield None  # pragma: no cover  — never invoked in route tests


async def _stub_instantiate_pipeline(*_args: Any, **_kwargs: Any) -> _StubPipeline:
    return _StubPipeline()


@pytest_asyncio.fixture
async def fx(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Fx]:
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)
    audit = InMemoryAuditSink()
    sandbox = FakeSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)

    # Stub out pipeline boot so tests don't need a real claude binary.
    container.env_service.instantiate_pipeline = _stub_instantiate_pipeline  # type: ignore[assignment]

    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app, audit=audit)
    finally:
        await container.aclose()


async def _create_project_with_workspace(client: AsyncClient) -> tuple[str, str]:
    """Creates a project + workspace, returns `(project_id, workspace_id)`."""
    created = await client.post(
        "/_gapt/api/projects",
        json={
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    wks = await client.post(
        f"/_gapt/api/projects/{project_id}/workspaces",
        json={"branch": "main"},
    )
    assert wks.status_code == 201, wks.text
    return project_id, wks.json()["id"]


# ──────────────────────────────────────────────────── create ──


@pytest.mark.asyncio
async def test_create_session_happy_path(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)

        resp = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["env_manifest_id"] == "gapt_default"
        assert body["workspace_id"] == workspace_id
        assert body["project_id"] == project_id
        session_id = body["id"]

        # Hook runner attached on the stub pipeline.
        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        assert runtime.pipeline.attached_hook_runner is not None


@pytest.mark.asyncio
async def test_create_session_unknown_workspace_returns_404(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, _ = await _create_project_with_workspace(client)
        resp = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": "01KS90000000000000000XXXXX"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "workspace.not_found"


# ────────────────────────────────────────────────── list / get ──


@pytest.mark.asyncio
async def test_list_and_fetch_session(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        listed = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert listed.status_code == 200
        ids = [s["id"] for s in listed.json()]
        assert ids == [session_id]

        one = await client.get(f"/_gapt/api/sessions/{session_id}")
        assert one.status_code == 200
        assert one.json()["id"] == session_id


@pytest.mark.asyncio
async def test_fetch_session_404(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _create_project_with_workspace(client)
        resp = await client.get("/_gapt/api/sessions/01KS90000000000000000XXXXX")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "session.not_found"


# ────────────────────────────────────────────────── invoke / stream ──


async def _scripted_runner(runtime: Any, message: str) -> None:
    await runtime.bus.publish(SessionEventKind.TEXT, {"chunk": f"echo:{message}"})


@pytest.mark.asyncio
async def test_invoke_and_replay_messages(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        # Swap in a scripted runner via direct runtime access so the
        # default Pipeline.run_stream (which our stub doesn't implement)
        # isn't used.
        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        await runtime.invoke("hi", runner=_scripted_runner)
        await runtime.wait_done()

        replay = await client.get(f"/_gapt/api/sessions/{session_id}/messages")
        assert replay.status_code == 200
        kinds = [m["kind"] for m in replay.json()]
        assert kinds == ["text", "done"]
        assert replay.json()[0]["data"] == {"chunk": "echo:hi"}


@pytest.mark.asyncio
async def test_invoke_endpoint_kicks_off_runner(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        # The real `invoke` endpoint uses `_default_invoke_runner` which
        # calls `pipeline.run_stream` — our stub generator yields nothing,
        # so the task finishes cleanly + emits `done`.
        resp = await client.post(
            f"/_gapt/api/sessions/{session_id}/invoke",
            json={"message": "hello"},
        )
        assert resp.status_code == 202
        assert resp.json()["session_id"] == session_id

        # Wait for the background task to finish so the `done` lands.
        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        await runtime.wait_done()

        replay = await client.get(f"/_gapt/api/sessions/{session_id}/messages")
        kinds = [m["kind"] for m in replay.json()]
        assert kinds == ["done"]


@pytest.mark.asyncio
async def test_interrupt_endpoint(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)

        async def forever(rt: Any, msg: str) -> None:
            await asyncio.sleep(10)

        await runtime.invoke("never", runner=forever)
        await asyncio.sleep(0)  # let the task enter the sleep

        resp = await client.post(f"/_gapt/api/sessions/{session_id}/interrupt")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": session_id, "cancelled": True}

        await runtime.wait_done()
        replay = await client.get(f"/_gapt/api/sessions/{session_id}/messages")
        kinds = [m["kind"] for m in replay.json()]
        assert "error" in kinds


@pytest.mark.asyncio
async def test_invoke_404_when_runtime_missing(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _create_project_with_workspace(client)
        resp = await client.post(
            "/_gapt/api/sessions/01KS90000000000000000XXXXX/invoke",
            json={"message": "x"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "session.not_found"


# ────────────────────────────────────────────────── archive ──


@pytest.mark.asyncio
async def test_archive_session(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        archived = await client.post(f"/_gapt/api/sessions/{session_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == enums.AgentSessionStatus.ARCHIVED.value

        # Runtime evicted.
        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        with pytest.raises(SessionNotFound):
            await registry.get(session_id)

        # Not in the active list any more.
        listed = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert listed.json() == []


# ────────────────────────────────────────────────── stream SSE ──


@pytest.mark.asyncio
async def test_stream_emits_text_and_done(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        # Push events synchronously before the client connects so they
        # land in the replay buffer; with since=0 the streamer flushes
        # them before subscribing.
        await runtime.invoke("hi", runner=_scripted_runner)
        await runtime.wait_done()

        async with client.stream("GET", f"/_gapt/api/sessions/{session_id}/stream?since=0") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if b"event: done" in body:
                    break

        assert b"event: text" in body
        assert b'"chunk":"echo:hi"' in body
        assert b"event: done" in body
