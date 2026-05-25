"""Workspace background services — start / stop / expose / list.

Surfaces the in-process `ServiceRegistry` so the frontend
`ServicesPanel` can drive long-running dev servers (`npm run dev`,
`python -m http.server`, …) inside the worktree and one-click expose
them through Caddy.

Endpoints:
- `GET    /api/workspaces/{wid}/services`              — list
- `POST   /api/workspaces/{wid}/services`              — start
- `DELETE /api/workspaces/{wid}/services/{label}`      — stop + remove
- `POST   /api/workspaces/{wid}/services/{label}/stop` — stop, keep entry
- `POST   /api/workspaces/{wid}/services/{label}/restart` — stop+start
- `POST   /api/workspaces/{wid}/services/{label}/expose` — Caddy bind
- `DELETE /api/workspaces/{wid}/services/{label}/expose` — unbind

Log tail is served by the existing `GET
/api/workspaces/{wid}/file-tail?path=...` (M2-A1) — the response
carries the absolute log path so the SPA can pass the worktree-
relative form straight through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    AppContainer,
    get_app_settings,
    get_container,
    get_db_session,
    get_service_registry,
)
from gapt_server.db import enums, models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.caddy import (
    CaddyAdminClient,
    CaddyAdminError,
    CaddyHttpTransport,
    SubdomainBinding,
    SubdomainManager,
)
from gapt_server.domains.services import (
    Service,
    ServiceAlreadyExists,
    ServiceNotFound,
    ServiceRegistry,
)
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


router = APIRouter(prefix="/api/workspaces", tags=["services"])


class StartServiceRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    cmd: str = Field(min_length=1, max_length=2000)
    port: int | None = Field(default=None, ge=1, le=65535)
    env: dict[str, str] | None = Field(default=None)


class ServiceResponse(BaseModel):
    workspace_id: str
    label: str
    cmd: str
    port: int | None = None
    auto_port: int | None = None
    pid: int | None = None
    state: str
    started_at: float
    exited_at: float | None = None
    exit_code: int | None = None
    bound_url: str | None = None
    bound_host: str | None = None
    # Worktree-relative log path so the SPA can hand it to the
    # file-tail endpoint.
    log_path: str

    @classmethod
    def from_service(cls, svc: Service) -> ServiceResponse:
        # Strip the worktree prefix from the log_path so the response
        # is workspace-relative. Path traversal isn't a risk — the
        # file-tail endpoint re-validates.
        rel_log = svc.log_path
        if rel_log.startswith(svc.worktree_path):
            rel_log = "/" + rel_log[len(svc.worktree_path) :].lstrip("/")
        return cls(
            workspace_id=svc.workspace_id,
            label=svc.label,
            cmd=svc.cmd,
            port=svc.port,
            auto_port=svc.auto_port,
            pid=svc.pid,
            state=svc.state.value,
            started_at=svc.started_at,
            exited_at=svc.exited_at,
            exit_code=svc.exit_code,
            bound_url=svc.bound_url,
            bound_host=svc.bound_host,
            log_path=rel_log,
        )


async def _workspace_or_404(
    db: AsyncSession, user: AdminPrincipal, workspace_id: str
) -> models.Workspace:
    # Single-admin model — no membership check; we still bounce non-
    # running rows with 409 so the service spawn path fails early.
    _ = user
    row = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    if row.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workspace.not_running",
                "reason": f"workspace {workspace_id} is {row.status.value}",
            },
        )
    return row


def _build_subdomain_manager(settings: Settings) -> SubdomainManager | None:
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return None
    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    client = CaddyAdminClient(transport=transport)
    return SubdomainManager(client=client, preview_domain=settings.caddy_preview_domain)


@router.get("/{workspace_id}/services", response_model=list[ServiceResponse])
async def list_services(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
) -> list[ServiceResponse]:
    await _workspace_or_404(db, user, workspace_id)
    services = await registry.list(workspace_id)
    return [ServiceResponse.from_service(s) for s in services]


@router.post(
    "/{workspace_id}/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_service(
    workspace_id: str,
    payload: StartServiceRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
) -> ServiceResponse:
    workspace = await _workspace_or_404(db, user, workspace_id)
    try:
        svc = await registry.start(
            workspace_id=workspace_id,
            label=payload.label,
            cmd=payload.cmd,
            worktree_path=workspace.worktree_path,
            port=payload.port,
            env=payload.env,
        )
    except ServiceAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "service.already_running", "reason": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "service.spawn_failed", "reason": str(exc)},
        ) from exc
    return ServiceResponse.from_service(svc)


@router.post(
    "/{workspace_id}/services/{label}/stop",
    response_model=ServiceResponse,
)
async def stop_service(
    workspace_id: str,
    label: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
) -> ServiceResponse:
    await _workspace_or_404(db, user, workspace_id)
    try:
        svc = await registry.stop(workspace_id, label)
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "service.not_found", "reason": str(exc)},
        ) from exc
    return ServiceResponse.from_service(svc)


@router.delete(
    "/{workspace_id}/services/{label}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_service(
    workspace_id: str,
    label: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> None:
    await _workspace_or_404(db, user, workspace_id)
    # Stop first (no-op if already exited), then remove. Also unbind
    # the Caddy route if one was registered — otherwise we'd leave a
    # dangling subdomain pointing at a dead service.
    try:
        svc = await registry.get(workspace_id, label)
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "service.not_found", "reason": str(exc)},
        ) from exc
    if svc.bound_host is not None:
        manager = _build_subdomain_manager(settings)
        if manager is not None:
            try:
                await manager.unregister(_workspace_slug(workspace_id, label))
            except CaddyAdminError:
                # Best-effort — a dangling Caddy route is a known
                # mode and the operator can clean it up via the admin
                # API. Don't block the delete.
                pass
        await registry.set_bound(workspace_id, label, None, None)
    try:
        await registry.stop(workspace_id, label)
    except ServiceNotFound:
        pass
    await registry.remove(workspace_id, label)


@router.post(
    "/{workspace_id}/services/{label}/restart",
    response_model=ServiceResponse,
)
async def restart_service(
    workspace_id: str,
    label: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
) -> ServiceResponse:
    workspace = await _workspace_or_404(db, user, workspace_id)
    try:
        existing = await registry.get(workspace_id, label)
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "service.not_found", "reason": str(exc)},
        ) from exc
    # Capture the args before we stop+remove (post-stop the service
    # is still in the registry but its proc is dead — fine to read).
    cmd, port = existing.cmd, existing.port
    try:
        await registry.stop(workspace_id, label)
    except ServiceNotFound:
        pass
    await registry.remove(workspace_id, label)
    try:
        svc = await registry.start(
            workspace_id=workspace_id,
            label=label,
            cmd=cmd,
            worktree_path=workspace.worktree_path,
            port=port,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "service.spawn_failed", "reason": str(exc)},
        ) from exc
    return ServiceResponse.from_service(svc)


class ExposeRequest(BaseModel):
    # Optional override — defaults to whatever the service reports as
    # `port` (user-set or auto-detected).
    port: int | None = Field(default=None, ge=1, le=65535)
    # Optional host hint when GAPT is reverse-proxying from a separate
    # machine (rare in dev; defaults to localhost).
    upstream_host: str | None = Field(default=None, min_length=1, max_length=255)


class ExposeResponse(BaseModel):
    workspace_id: str
    label: str
    host: str
    url: str
    port: int


def _workspace_slug(workspace_id: str, label: str) -> str:
    """`<wid>-<label>` lowercase, DNS-friendly. Caddy treats this as
    the subdomain prefix under the configured preview domain."""
    return f"{workspace_id.lower()}-{label.lower()}"


@router.post(
    "/{workspace_id}/services/{label}/expose",
    response_model=ExposeResponse,
)
async def expose_service(
    workspace_id: str,
    label: str,
    payload: ExposeRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> ExposeResponse:
    await _workspace_or_404(db, user, workspace_id)
    try:
        svc = await registry.get(workspace_id, label)
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "service.not_found", "reason": str(exc)},
        ) from exc
    port = payload.port or svc.port
    if port is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "service.no_port",
                "reason": (
                    "no port specified and none auto-detected from the service "
                    "log yet; pass `port` explicitly or wait a few seconds"
                ),
            },
        )

    manager = _build_subdomain_manager(settings)
    if manager is None:
        # Dev fallback: no Caddy configured, but the user still wants
        # a clickable URL. Return localhost so the IDE preview iframe
        # at least works on the same host. Production must set
        # GAPT_CADDY_* envs to get a real preview domain.
        url = f"http://localhost:{port}"
        await registry.set_bound(workspace_id, label, "localhost", url)
        return ExposeResponse(
            workspace_id=workspace_id,
            label=label,
            host="localhost",
            url=url,
            port=port,
        )

    slug = _workspace_slug(workspace_id, label)
    # The workspace container's hostname matches `gapt-ws-<wid>`, and
    # Caddy is on the same `gapt-net` docker network, so Caddy resolves
    # the upstream via docker DNS without publishing the port to the
    # host. `payload.upstream_host` still overrides this for the rare
    # case where the operator runs Caddy off-network and needs a host
    # bridge address.
    default_upstream = f"gapt-ws-{workspace_id.lower()}"
    # Default to path-based routing on the apex GAPT domain. Apps
    # that require root-relative paths can switch to subdomain mode
    # by passing `mode: "subdomain"` on the request body once that
    # field lands (current shape covers the common case).
    from gapt_server.domains.caddy.subdomain import PreviewMode  # noqa: PLC0415

    binding = SubdomainBinding(
        workspace_slug=slug,
        upstream_host=payload.upstream_host or default_upstream,
        upstream_port=port,
        mode=PreviewMode.PATH,
        # Workspace dev services start with `npm run dev` / `vite` /
        # `uvicorn --reload` — none of which know they're mounted at
        # `/preview/<slug>`. Strip the prefix so the upstream sees
        # its own root. The Referer-fallback route the manager also
        # creates handles `<link href="/favicon.png">`-style root
        # requests for free. Prod-deploy paths (LocalComposeTarget)
        # keep the default `strip_prefix=False` because they build
        # with NEXT_PUBLIC_BASE_PATH baked in.
        strip_prefix=True,
    )
    try:
        host = await manager.register(binding)
    except CaddyAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    url = f"https://{host}"
    await registry.set_bound(workspace_id, label, host, url)
    return ExposeResponse(
        workspace_id=workspace_id, label=label, host=host, url=url, port=port
    )


@router.delete(
    "/{workspace_id}/services/{label}/expose",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unexpose_service(
    workspace_id: str,
    label: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> None:
    await _workspace_or_404(db, user, workspace_id)
    try:
        svc = await registry.get(workspace_id, label)
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "service.not_found", "reason": str(exc)},
        ) from exc
    if svc.bound_host is None:
        return
    manager = _build_subdomain_manager(settings)
    if manager is not None:
        try:
            await manager.unregister(_workspace_slug(workspace_id, label))
        except CaddyAdminError:
            pass
    await registry.set_bound(workspace_id, label, None, None)
