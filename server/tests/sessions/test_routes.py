"""HTTP-level tests for session routes — closes M1-E2."""

from __future__ import annotations

import asyncio
import contextlib
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

    async def run_stream(self, message: str, state=None) -> Any:
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
async def test_list_sessions_enriched_and_archive_filter(fx: _Fx) -> None:
    """Phase J.1 — `list_sessions` must (a) return `turn_count` +
    `first_user_message` per session by default, and (b) honour
    `include_archived=true` so the history page can show archived
    rows."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)

        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        # Seed a couple of user_message events directly so we don't
        # have to drive a real pipeline turn. The route's enrichment
        # query reads from session_events.
        from gapt_server.container import build_container  # noqa: PLC0415
        from gapt_server.db import models  # noqa: PLC0415

        container = fx.app.state.container
        assert container.session_factory is not None
        async with container.session_factory() as bg:
            bg.add(
                models.SessionEvent(
                    session_id=session_id,
                    seq=1,
                    kind="user_message",
                    data={"text": "first prompt — what is 2+2?"},
                )
            )
            bg.add(
                models.SessionEvent(
                    session_id=session_id,
                    seq=2,
                    kind="user_message",
                    data={"text": "follow-up question"},
                )
            )
            await bg.commit()
        del build_container  # noqa: F811 — silence "imported but unused" if linter is picky

        # Default (no include_archived): the single active session,
        # with turn_count=2 and the first prompt's snippet.
        listed = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["id"] == session_id
        assert rows[0]["turn_count"] == 2
        assert rows[0]["first_user_message"] == "first prompt — what is 2+2?"

        # Archive the session, then default list should be empty.
        archived = await client.post(
            f"/_gapt/api/sessions/{session_id}/archive"
        )
        assert archived.status_code == 200
        default = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert default.status_code == 200
        assert default.json() == []

        # include_archived=true brings the row back, still enriched.
        all_rows = await client.get(
            f"/_gapt/api/projects/{project_id}/sessions?include_archived=true"
        )
        assert all_rows.status_code == 200
        body = all_rows.json()
        assert len(body) == 1
        assert body[0]["status"] == "archived"
        assert body[0]["turn_count"] == 2
        assert body[0]["first_user_message"] == "first prompt — what is 2+2?"


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
        # Phase I.2 — `_run_with_lifecycle` now publishes the user's
        # prompt as a `user_message` event before the runner runs.
        assert kinds == ["user_message", "text", "done"]
        assert replay.json()[0]["data"] == {"text": "hi"}
        assert replay.json()[1]["data"] == {"chunk": "echo:hi"}


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
        # Phase I.2 — user_message lands first; runner's stub then
        # yields nothing so lifecycle closes with `done`.
        assert kinds == ["user_message", "done"]


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


@pytest.mark.asyncio
async def test_invoke_returns_budget_exhausted_when_cost_at_or_above_cap(
    fx: _Fx,
) -> None:
    """Phase N.3 — when ``cost_budget_usd`` is set AND cumulative
    ``cost_usd`` has reached the cap, the next invoke is rejected
    with HTTP 402 ``session.budget_exhausted`` BEFORE the executor
    runs. This keeps geny-executor's own budget metadata from ever
    landing in the agent's prompt context (the leak that produced
    the "남은 예산이 빠듯하니..." meta-cognitive chatter)."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id, "cost_budget_usd": 0.10},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]

        # Simulate the session having already spent past its cap. The
        # accumulator is the live cost source consulted by the gate;
        # the DB column tracks the same value once on_cost_update
        # fires in real flow.
        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        assert runtime.cost_budget_usd == pytest.approx(0.10)
        runtime.accumulator.cost_usd = 0.15

        resp = await client.post(
            f"/_gapt/api/sessions/{session_id}/invoke",
            json={"message": "next turn"},
        )
        assert resp.status_code == 402, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "session.budget_exhausted"
        assert detail["cost_usd"] == pytest.approx(0.15)
        assert detail["cost_budget_usd"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_invoke_proceeds_when_no_budget_cap_set(fx: _Fx) -> None:
    """Phase N.3 — leaving ``cost_budget_usd`` unset = no cap. The
    gate must be a true no-op so existing "free mode" sessions
    keep behaving the same as before the budget enforcement landed."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        # Cap unset; pile up any cost — the gate must not trip.
        assert runtime.cost_budget_usd is None
        runtime.accumulator.cost_usd = 9999.99

        resp = await client.post(
            f"/_gapt/api/sessions/{session_id}/invoke",
            json={"message": "should pass"},
        )
        assert resp.status_code == 202, resp.text


@pytest.mark.asyncio
async def test_invoke_proceeds_when_cost_below_cap(fx: _Fx) -> None:
    """Phase N.3 — strict ``>=`` gate: an in-budget session must
    still be allowed to invoke even when the cumulative cost is
    just shy of the cap. Boundary test for the gate condition."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id, "cost_budget_usd": 1.00},
        )
        session_id = created.json()["id"]

        registry = client._transport.app.state.container.session_registry  # type: ignore[attr-defined]
        runtime = await registry.get(session_id)
        runtime.accumulator.cost_usd = 0.50  # half the cap

        resp = await client.post(
            f"/_gapt/api/sessions/{session_id}/invoke",
            json={"message": "in budget"},
        )
        assert resp.status_code == 202, resp.text


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


@pytest.mark.asyncio
async def test_reactivate_session(fx: _Fx) -> None:
    """Phase L.2 — archived session can be flipped back to active so
    the chat panel can attach to it again. Idempotent for already-
    active sessions (no-op, status stays active)."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        # Archive first.
        await client.post(f"/_gapt/api/sessions/{session_id}/archive")

        # Reactivate.
        reactivated = await client.post(
            f"/_gapt/api/sessions/{session_id}/reactivate"
        )
        assert reactivated.status_code == 200, reactivated.text
        assert reactivated.json()["status"] == enums.AgentSessionStatus.ACTIVE.value

        # Now it shows up in the default (active-only) list again.
        listed = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert [s["id"] for s in listed.json()] == [session_id]

        # Idempotent: a second reactivate keeps it active.
        again = await client.post(
            f"/_gapt/api/sessions/{session_id}/reactivate"
        )
        assert again.status_code == 200
        assert again.json()["status"] == enums.AgentSessionStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_list_sessions_workspace_filter(fx: _Fx) -> None:
    """Phase L.3 — `?workspace_id=` filters the list to one workspace
    so the ChatPanel's SessionPicker doesn't mix sessions across
    workspaces."""
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, ws_a = await _create_project_with_workspace(client)
        # Second workspace in the same project.
        ws_b_resp = await client.post(
            f"/_gapt/api/projects/{project_id}/workspaces",
            json={"branch": "other"},
        )
        ws_b = ws_b_resp.json()["id"]

        sa_resp = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": ws_a},
        )
        sa_id = sa_resp.json()["id"]
        sb_resp = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": ws_b},
        )
        sb_id = sb_resp.json()["id"]

        only_a = await client.get(
            f"/_gapt/api/projects/{project_id}/sessions?workspace_id={ws_a}"
        )
        assert [s["id"] for s in only_a.json()] == [sa_id]
        only_b = await client.get(
            f"/_gapt/api/projects/{project_id}/sessions?workspace_id={ws_b}"
        )
        assert [s["id"] for s in only_b.json()] == [sb_id]
        # Without the filter both come back.
        all_rows = await client.get(f"/_gapt/api/projects/{project_id}/sessions")
        assert {s["id"] for s in all_rows.json()} == {sa_id, sb_id}


# ────────────────────────────────────────────────── stream SSE ──


@pytest.mark.asyncio
async def test_stream_emits_text_and_done_via_uvicorn(
    fx: _Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase M.3 — end-to-end SSE coverage on the route layer.

    Pre-M.3 this test was skipped because httpx's `ASGITransport`
    buffers chunks until the generator returns, hiding the SSE
    intermediate frames. We now spin up a real uvicorn server on an
    ephemeral port (see `tests/_helpers/uvicorn_server.py`) and read
    the streaming body off a real TCP socket. The same lifespan that
    runs in prod fires here, so the test also exercises the
    `start_sweep()` + audit-sink-start path the in-memory ASGI
    transport never reached.
    """
    from gapt_server.agent import session_registry  # noqa: PLC0415

    monkeypatch.setattr(session_registry, "DEFAULT_KEEPALIVE_S", 0.05)

    from tests._helpers.uvicorn_server import run_uvicorn  # noqa: PLC0415

    async with run_uvicorn(fx.app) as server:
        async with AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            project_id, workspace_id = await _create_project_with_workspace(client)
            created = await client.post(
                f"/_gapt/api/projects/{project_id}/sessions",
                json={"workspace_id": workspace_id},
            )
            session_id = created.json()["id"]

            registry = fx.app.state.container.session_registry  # type: ignore[attr-defined]
            runtime = await registry.get(session_id)
            # Push the entire turn through the bus BEFORE the client
            # opens the SSE socket so the replay window covers it.
            # `runtime.invoke` returns once the task is scheduled; we
            # await done so the publish ordering is deterministic.
            await runtime.invoke("hi", runner=_scripted_runner)
            await runtime.wait_done()

            body = b""
            async with asyncio.timeout(15):
                async with client.stream(
                    "GET",
                    f"/_gapt/api/sessions/{session_id}/stream?since=0",
                ) as resp:
                    assert resp.status_code == 200
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    # Real-socket streaming — chunks arrive as the
                    # server flushes them, not after the generator
                    # finishes. Break the read loop once we've seen the
                    # terminal `done` frame so the test doesn't wait
                    # for the keepalive timeout to lapse.
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if b"event: done" in body:
                            break

            assert b"event: text" in body
            assert b'"chunk":"echo:hi"' in body
            assert b"event: done" in body


@pytest.mark.asyncio
async def test_stream_continues_across_turn_via_uvicorn(
    fx: _Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase M.3 — the post-Phase-L contract is that the SSE socket
    stays open across multiple turns (so the chat UI's `useEffect`
    doesn't have to re-subscribe per invoke). Pre-fix the route closed
    the stream after the first `done`; the next turn's text never
    reached the browser. This test verifies the route layer keeps the
    socket alive and forwards turn-2 frames.
    """
    from gapt_server.agent import session_registry  # noqa: PLC0415

    monkeypatch.setattr(session_registry, "DEFAULT_KEEPALIVE_S", 0.05)

    from tests._helpers.uvicorn_server import run_uvicorn  # noqa: PLC0415

    async with run_uvicorn(fx.app) as server:
        async with AsyncClient(base_url=server.base_url, timeout=10.0) as client:
            project_id, workspace_id = await _create_project_with_workspace(client)
            created = await client.post(
                f"/_gapt/api/projects/{project_id}/sessions",
                json={"workspace_id": workspace_id},
            )
            session_id = created.json()["id"]

            registry = fx.app.state.container.session_registry  # type: ignore[attr-defined]
            runtime = await registry.get(session_id)

            # Turn 1 lands before the socket opens (replay), turn 2
            # lands AFTER the reader is subscribed (live path).
            await runtime.invoke("hi", runner=_scripted_runner)
            await runtime.wait_done()

            seen_turn2 = asyncio.Event()
            body = b""

            async def reader() -> None:
                nonlocal body
                async with client.stream(
                    "GET",
                    f"/_gapt/api/sessions/{session_id}/stream?since=0",
                ) as resp:
                    assert resp.status_code == 200
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if b'"chunk":"echo:second"' in body:
                            seen_turn2.set()
                            break

            reader_task = asyncio.create_task(reader())
            # Give the reader a tick to subscribe + flush the replay.
            await asyncio.sleep(0.1)
            # Turn 2 — runs on the same runtime, publishes onto the
            # bus, MUST land on the still-open reader.
            await runtime.invoke("second", runner=_scripted_runner)
            await runtime.wait_done()

            async with asyncio.timeout(5):
                await seen_turn2.wait()
            reader_task.cancel()
            with contextlib.suppress(BaseException):
                await reader_task

            assert b'"chunk":"echo:hi"' in body
            assert b'"chunk":"echo:second"' in body


# ──────────────────────────────────── Phase M.2 — _full_replay combine + rehydrate round-trip ──


@pytest.mark.asyncio
async def test_full_replay_combines_db_prefix_with_memory_tail(fx: _Fx) -> None:
    """`/stream` 의 `_full_replay` 가 DB prefix + in-memory tail 을 합쳐서
    돌려주는지 직접 검증. 시나리오:
      1. 세션 만들고 invoke → DB 에 user_message + text + done 기록.
      2. 런타임을 registry 에서 pop 해서 in-memory 버스를 비움.
      3. 다시 rehydrate 후 라이브 이벤트 하나 publish.
      4. `_full_replay(since=0)` 는 DB-prefix 3건 + live 1건 = 4건, seq 단조."""
    from gapt_server.agent.session_registry import SessionEventKind as _Kind  # noqa: PLC0415
    from gapt_server.routers.sessions import (  # noqa: PLC0415
        _full_replay,
        _runtime_or_rehydrate,
    )

    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        container = client._transport.app.state.container  # type: ignore[attr-defined]
        registry = container.session_registry

        runtime = await registry.get(session_id)
        await runtime.invoke("first", runner=_scripted_runner)
        await runtime.wait_done()

        # Drop the runtime so the bus's ring buffer goes away.
        await registry.pop(session_id)

        # Build a manager + vault the rehydrate path needs.
        from gapt_server.agent.session_manager import (  # noqa: PLC0415
            ProjectAwareSessionManager,
        )
        from gapt_server.domains.auth.principal import (  # noqa: PLC0415
            AdminPrincipal,
        )

        from gapt_server.routers.sessions import SessionAccess  # noqa: PLC0415

        manager: ProjectAwareSessionManager = container.session_manager
        async with container.session_factory() as db:
            user = AdminPrincipal(id="admin", display_name="admin")
            access = SessionAccess(
                registry=registry,
                db=db,
                manager=manager,
                policy_engine=container.policy_engine,
                audit_sink=container.audit_sink,
                container=container,
                vault=None,  # type: ignore[arg-type]
                user=user,
            )
            rehydrated = await _runtime_or_rehydrate(session_id, access=access)

            # Publish one live event AFTER rehydrate.
            await rehydrated.bus.publish(_Kind.TEXT, {"text": "live-after-rehydrate"})

            combined = await _full_replay(db, rehydrated, since=0)

        seqs = [e.seq for e in combined]
        assert seqs == sorted(seqs)  # monotonic
        # DB prefix gave us {user_message, text, done}; live tail adds the new text.
        kinds = [e.kind.value for e in combined]
        assert "user_message" in kinds
        assert "done" in kinds
        # The live publication after rehydrate landed on the tail.
        assert any(
            e.kind is _Kind.TEXT and e.data.get("text") == "live-after-rehydrate"
            for e in combined
        )


@pytest.mark.asyncio
async def test_rehydrate_round_trip_restores_state_messages(fx: _Fx) -> None:
    """Pop + rehydrate 사이클이 `conversation_state.messages` 를 복원하는지.
    이걸 보장 못하면 서버 재시작 후 첫 invoke 가 컨텍스트 없이 돌아간다."""
    from gapt_server.agent.session_manager import (  # noqa: PLC0415
        ProjectAwareSessionManager,
    )
    from gapt_server.agent.session_registry import SessionEventKind as _Kind  # noqa: PLC0415
    from gapt_server.routers.sessions import (  # noqa: PLC0415
        _runtime_or_rehydrate,
    )
    from gapt_server.domains.auth.principal import (  # noqa: PLC0415
        AdminPrincipal,
    )

    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, workspace_id = await _create_project_with_workspace(client)
        created = await client.post(
            f"/_gapt/api/projects/{project_id}/sessions",
            json={"workspace_id": workspace_id},
        )
        session_id = created.json()["id"]

        container = client._transport.app.state.container  # type: ignore[attr-defined]
        registry = container.session_registry
        runtime = await registry.get(session_id)

        # Emit a complete turn (user → assistant text) through the bus so the
        # DB persister writes `session_events` rows the rehydrate path will
        # rebuild messages from.
        async def _two_turn_runner(rt: Any, _msg: str) -> None:
            await rt.bus.publish(_Kind.TEXT, {"text": "hello back"})

        await runtime.invoke("hi", runner=_two_turn_runner)
        await runtime.wait_done()

        # Drop runtime, then rehydrate.
        await registry.pop(session_id)

        from gapt_server.routers.sessions import SessionAccess  # noqa: PLC0415

        manager: ProjectAwareSessionManager = container.session_manager
        async with container.session_factory() as db:
            user = AdminPrincipal(id="admin", display_name="admin")
            access = SessionAccess(
                registry=registry,
                db=db,
                manager=manager,
                policy_engine=container.policy_engine,
                audit_sink=container.audit_sink,
                container=container,
                vault=None,  # type: ignore[arg-type]
                user=user,
            )
            rehydrated = await _runtime_or_rehydrate(session_id, access=access)

        # `state.messages` should now carry the prior user/assistant pair so
        # the next `Pipeline.run_stream` has memory of the first turn.
        state = rehydrated.conversation_state
        assert state is not None
        contents = [m.get("content") for m in state.messages]
        assert "hi" in contents
        assert "hello back" in contents
