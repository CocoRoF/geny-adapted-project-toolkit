"""GAPT self-introspection MCP — the chat agent's window into GAPT.

The agent (Claude Code CLI inside the workspace sandbox) normally
designs/edits the user's project without caring where it runs. But
when the user reports "deploys fail in GAPT" / "my preview 502s in
GAPT", the agent needs ACCURATE knowledge of the hosting GAPT
instance: which services are running at which ports, what the last
deploy run said, which preview routes exist. Guessing from the
worktree alone produces confident nonsense.

This module exposes that knowledge as a small read-only MCP toolset,
mounted at ``/_gapt/api/mcp`` (streamable HTTP). Session creation
injects a per-session ``--mcp-config`` pointing the CLI at it through
Caddy (the workspace container can't reach the host directly, but it
shares ``gapt-net`` with Caddy). The tool descriptions tell the model
to reach for them ONLY when debugging GAPT-hosted behaviour — the
ordinary coding loop never needs them.

Auth: a signed bearer token minted per session, carrying the
workspace + project ids (the tools are scoped to that workspace).
HMAC over ``settings.session_secret`` — no DB round-trip, survives
server restarts, and can't be forged from inside the sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from gapt_server.db import models

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from gapt_server.container import AppContainer

logger = structlog.get_logger(__name__)

_TOKEN_TTL_S = 7 * 24 * 3600

# Request-scoped identity decoded from the bearer token by the ASGI
# wrapper below; the FastMCP tools read it to scope their queries.
_CURRENT: ContextVar[tuple[str, str] | None] = ContextVar(
    "gapt_introspect_identity", default=None
)


# ─────────────────────────────────────────────── token mint/verify ──


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint_introspect_token(
    *, workspace_id: str, project_id: str, secret: str, ttl_s: int = _TOKEN_TTL_S
) -> str:
    payload = json.dumps(
        {"wid": workspace_id, "pid": project_id, "exp": int(time.time()) + ttl_s},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify_introspect_token(token: str, *, secret: str) -> tuple[str, str] | None:
    """Returns ``(workspace_id, project_id)`` or None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        body = json.loads(payload)
        if int(body.get("exp", 0)) < time.time():
            return None
        wid, pid = body.get("wid"), body.get("pid")
        if not isinstance(wid, str) or not isinstance(pid, str):
            return None
        return (wid, pid)
    except Exception:  # noqa: BLE001 — any malformed token is just unauthorized
        return None


# ───────────────────────────────────────────────────── MCP server ──


def build_introspect_app(container: AppContainer) -> ASGIApp:
    """FastMCP streamable-HTTP app wrapped in bearer-token auth.

    Mounted by ``create_app`` under ``/_gapt/api/mcp`` so it rides the
    existing Caddy ``/_gapt/api/*`` route — reachable from workspace
    containers via gapt-net without new routing."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415 — heavy import, app-boot only
    from mcp.server.transport_security import (  # noqa: PLC0415
        TransportSecuritySettings,
    )

    mcp = FastMCP(
        "gapt-self",
        stateless_http=True,
        # Requests arrive via Caddy with Host values like
        # `gapt-dev-caddy-1:8080` — the SDK's DNS-rebinding guard only
        # allows localhost by default and would 421 them. AuthN is the
        # signed bearer token (checked before the transport ever runs),
        # so the Host check adds nothing here.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
        instructions=(
            "Read-only introspection of the GAPT instance THIS session runs "
            "inside. Use ONLY when debugging GAPT-hosted behaviour (deploys, "
            "service exposure, previews) — not for ordinary coding tasks."
        ),
    )

    def _identity() -> tuple[str, str]:
        ident = _CURRENT.get()
        if ident is None:  # pragma: no cover — wrapper guarantees this
            raise RuntimeError("introspection identity missing")
        return ident

    async def _workspace(db: Any, wid: str) -> models.Workspace | None:
        return (
            await db.execute(select(models.Workspace).where(models.Workspace.id == wid))
        ).scalar_one_or_none()

    @mcp.tool()
    async def gapt_overview() -> str:
        """Where am I? Snapshot of the GAPT environment hosting this
        session: the workspace (status, worktree, cloned repos), its
        project, running services, and configured deploy environments.
        Call this FIRST when the user reports something not working
        "in GAPT"."""
        wid, pid = _identity()
        sf = container.session_factory
        if sf is None:
            return json.dumps({"error": "db not configured"})
        async with sf() as db:
            ws = await _workspace(db, wid)
            project = (
                await db.execute(select(models.Project).where(models.Project.id == pid))
            ).scalar_one_or_none()
            envs = (
                (
                    await db.execute(
                        select(models.Environment).where(
                            models.Environment.project_id == pid
                        )
                    )
                )
                .scalars()
                .all()
            )
        services = await container.services.list(wid)
        return json.dumps(
            {
                "context": (
                    "This session runs INSIDE a GAPT workspace sandbox. The "
                    "worktree is mounted at /workspace. GAPT (not you) owns "
                    "service exposure, preview routing and deploys."
                ),
                "project": {"id": pid, "slug": getattr(project, "slug", None)},
                "workspace": {
                    "id": wid,
                    "name": getattr(ws, "name", None),
                    "status": str(getattr(ws, "status", "")),
                    "worktree_path": getattr(ws, "worktree_path", None),
                },
                "services": [
                    {
                        "label": s.label,
                        "state": s.state.value,
                        "port": s.port,
                        "auto_port": s.auto_port,
                        "bound_url": s.bound_url,
                    }
                    for s in services
                ],
                "environments": [
                    {
                        "name": e.name,
                        "kind": str(e.deploy_target_kind),
                        "last_run": (e.last_run or {}).get("status"),
                    }
                    for e in envs
                ],
            },
            ensure_ascii=False,
        )

    @mcp.tool()
    async def gapt_services() -> str:
        """GAPT-managed dev services of THIS workspace (npm run dev
        etc.): state, declared vs auto-detected port (drift = the
        declared port was taken), and the public preview URL if
        exposed. Use when "the preview 502s" / "expose doesn't work"."""
        wid, _pid = _identity()
        services = await container.services.list(wid)
        return json.dumps([s.snapshot() for s in services], ensure_ascii=False, default=str)

    @mcp.tool()
    async def gapt_service_log(label: str, tail_lines: int = 120) -> str:
        """Tail a GAPT-managed service's combined stdout+stderr log
        (includes install-step output). Use to see WHY a dev server
        died or which port it actually bound."""
        wid, _pid = _identity()
        for s in await container.services.list(wid):
            if s.label == label:
                try:
                    lines = Path(s.log_path).read_text(errors="replace").splitlines()
                except OSError as exc:
                    return f"(log unreadable: {exc})"
                return "\n".join(lines[-max(1, min(tail_lines, 2000)) :])
        return f"(no service labelled {label!r} — call gapt_services for the list)"

    @mcp.tool()
    async def gapt_environments() -> str:
        """Deploy environments of this project with their most recent
        run (status + exec_code). Use when "deploys fail in GAPT" —
        the exec_code pinpoints the failing stage (e.g.
        deploy.compose_up_failed, deploy.port_conflict)."""
        _wid, pid = _identity()
        sf = container.session_factory
        if sf is None:
            return json.dumps({"error": "db not configured"})
        out: list[dict[str, Any]] = []
        async with sf() as db:
            envs = (
                (
                    await db.execute(
                        select(models.Environment).where(
                            models.Environment.project_id == pid
                        )
                    )
                )
                .scalars()
                .all()
            )
            for e in envs:
                run = (
                    await db.execute(
                        select(models.DeployRun)
                        .where(models.DeployRun.environment_id == e.id)
                        .order_by(models.DeployRun.started_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                out.append(
                    {
                        "name": e.name,
                        "kind": str(e.deploy_target_kind),
                        "target_config": e.deploy_target_config,
                        "latest_run": (
                            None
                            if run is None
                            else {
                                "id": run.id,
                                "status": run.status,
                                "exec_code": run.exec_code,
                                "bound_url": run.bound_url,
                                "started_at": str(run.started_at),
                            }
                        ),
                    }
                )
        return json.dumps(out, ensure_ascii=False, default=str)

    @mcp.tool()
    async def gapt_deploy_log(environment_name: str) -> str:
        """Captured log of the most recent deploy run for one
        environment — includes [gapt] preflight lines (port remaps,
        routing) and the raw docker compose output."""
        _wid, pid = _identity()
        sf = container.session_factory
        if sf is None:
            return "(db not configured)"
        async with sf() as db:
            env = (
                await db.execute(
                    select(models.Environment).where(
                        models.Environment.project_id == pid,
                        models.Environment.name == environment_name,
                    )
                )
            ).scalar_one_or_none()
            if env is None:
                return f"(no environment named {environment_name!r})"
            run = (
                await db.execute(
                    select(models.DeployRun)
                    .where(models.DeployRun.environment_id == env.id)
                    .order_by(models.DeployRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if run is None:
            return "(no deploy runs yet)"
        return (
            f"run={run.id} status={run.status} exec_code={run.exec_code}\n"
            f"{run.log_tail or '(empty log)'}"
        )

    @mcp.tool()
    async def gapt_preview_routes() -> str:
        """Live Caddy preview routes related to this workspace/project
        (path or host, upstream dial, cache policy). Use when a
        preview URL 404s/502s or serves the wrong app."""
        wid, pid = _identity()
        from gapt_server.routers.services import (  # noqa: PLC0415 — avoid cycle
            _build_subdomain_manager,
        )

        manager = _build_subdomain_manager(container.settings)
        if manager is None:
            return "(caddy not configured)"
        needles = (wid.lower(), pid.lower())
        out: list[dict[str, Any]] = []
        for r in await manager.list_routes():
            rid = str(r.get("@id", ""))
            if not rid.startswith("gapt-preview-"):
                continue
            if not any(n in rid for n in needles):
                continue
            match = (r.get("match") or [{}])[0]
            dial = None
            no_store = False
            for h in r.get("handle", []) or []:
                if h.get("handler") == "reverse_proxy":
                    ups = h.get("upstreams") or [{}]
                    dial = ups[0].get("dial")
                if h.get("handler") == "headers" and h.get("response", {}).get(
                    "set", {}
                ).get("Cache-Control") == ["no-store"]:
                    no_store = True
            out.append(
                {
                    "id": rid,
                    "path": match.get("path"),
                    "host": match.get("host"),
                    "header_match": sorted((match.get("header_regexp") or {}).keys()),
                    "dial": dial,
                    "no_store": no_store,
                }
            )
        return json.dumps(out, ensure_ascii=False)

    inner = mcp.streamable_http_app()

    class _BearerWrapper:
        """ASGI shim: verify the bearer token, stash the identity in a
        ContextVar for the tools, 401 otherwise."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app
            # Exposed so create_app's lifespan can run the FastMCP
            # session manager alongside the FastAPI app.
            self.session_manager = mcp.session_manager

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            headers = {
                k.decode().lower(): v.decode()
                for k, v in scope.get("headers", [])
            }
            auth = headers.get("authorization", "")
            ident = (
                verify_introspect_token(
                    auth.removeprefix("Bearer ").strip(),
                    secret=container.settings.session_secret,
                )
                if auth.startswith("Bearer ")
                else None
            )
            if ident is None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error": "invalid or missing introspection token"}',
                    }
                )
                return
            token = _CURRENT.set(ident)
            try:
                await self.app(scope, receive, send)
            finally:
                _CURRENT.reset(token)

    return _BearerWrapper(inner)
