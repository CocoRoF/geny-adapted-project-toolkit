"""Preview subdomain + share-link endpoints.

- `POST /api/workspaces/{wid}/preview` — register the workspace's
  subdomain with Caddy and return the resolved host.
- `DELETE /api/workspaces/{wid}/preview` — unregister.
- `POST /api/workspaces/{wid}/share?ttl=` — mint an HMAC-signed
  share link. The recipient hits `{slug}.{preview_domain}/?share=...`
  and Caddy's request handler validates via this server (M2).

`POST /preview` is idempotent: re-posting with the same upstream
returns the same host. The Caddy delete is a no-op for unknown
routes so DELETE is also idempotent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import get_app_settings, get_db_session
from gapt_server.db import models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.caddy import (
    CaddyAdminClient,
    CaddyAdminError,
    CaddyHttpTransport,
    ShareLinkError,
    SubdomainBinding,
    SubdomainManager,
    issue_share_link,
)
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


router = APIRouter(prefix="/api/workspaces", tags=["preview"])

# Separate router with no auth — Caddy's on-demand TLS hook hits this
# unauthenticated. Mounted under /api/preview/ask.
ask_router = APIRouter(prefix="/api/preview", tags=["preview"])


@ask_router.get("/ask")
async def caddy_on_demand_ask(
    domain: str = Query(min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> dict[str, str]:
    """Caddy on-demand TLS gate.

    Caddy fires `GET ?domain=foo.preview.example.com` before
    requesting a certificate; 200 = mint cert, anything else = refuse.
    We look up the slug portion against the workspaces table and only
    approve hosts that match an active (non-archived) workspace under
    the configured preview domain. This is what keeps an attacker
    from making us mint certs for arbitrary names."""
    if not settings.caddy_preview_domain:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "preview.disabled", "reason": "GAPT_CADDY_PREVIEW_DOMAIN unset"},
        )
    suffix = f".{settings.caddy_preview_domain}"
    if not domain.endswith(suffix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "preview.wrong_domain", "reason": f"{domain!r} not under {suffix!r}"},
        )
    slug = domain[: -len(suffix)]
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "preview.empty_slug", "reason": domain},
        )
    # Two slug families share this domain:
    #
    #   1. **Workspace preview** — slug == workspace.id lowercased
    #      (registered by `SubdomainManager.register` / the
    #      `_workspace_slug` helper). The classic dev IDE preview.
    #
    #   2. **Prod deploy** — slug == `prod-<env_name>-<project_id>`
    #      lowercased. Registered by `LocalComposeTarget._route_primary_service`
    #      and `routers.deploy.stack_reroute`. The deploy gets its
    #      own subdomain in subdomain-mode (the architecturally
    #      robust answer to path-mode root-relative URL collisions
    #      with the GAPT apex).
    #
    # We accept either. Look at the slug shape to decide which table
    # to query; on no match return 404 so Caddy refuses to mint the
    # cert (the standard on-demand TLS gate behaviour).
    if slug.startswith("prod-"):
        # `prod-<env_name>-<project_id>` — project_id is a ULID
        # (26 chars), env_name is whatever the operator named the
        # env. Split from the right so an env name containing `-`
        # (e.g. `staging-eu`) still resolves cleanly.
        rest = slug[len("prod-") :]
        if "-" not in rest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "preview.unknown", "reason": slug},
            )
        env_name, project_id_lower = rest.rsplit("-", 1)
        # project_id is stored uppercased; the slug is lowercase.
        env_row = (
            await db.execute(
                select(models.Environment).where(
                    models.Environment.project_id == project_id_lower.upper(),
                    models.Environment.name == env_name,
                )
            )
        ).scalar_one_or_none()
        if env_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "preview.unknown", "reason": slug},
            )
        return {"domain": domain}

    # Workspace preview — slug == workspace.id lowercased.
    row = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == slug.upper())
        )
    ).scalar_one_or_none()
    if row is None or row.status.value == "archived":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "preview.unknown", "reason": slug},
        )
    return {"domain": domain}


class RegisterPreviewBody(BaseModel):
    upstream_host: str = Field(min_length=1, max_length=255)
    upstream_port: int = Field(ge=1, le=65535)


class PreviewResponse(BaseModel):
    host: str
    workspace_id: str


class ShareLinkResponse(BaseModel):
    token: str
    url: str
    expires_in_s: int


def _build_manager(settings: Settings) -> SubdomainManager | None:
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return None
    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    client = CaddyAdminClient(transport=transport)
    return SubdomainManager(client=client, preview_domain=settings.caddy_preview_domain)


async def _workspace_or_404(db: AsyncSession, *, wid: str) -> models.Workspace:
    row = (
        await db.execute(select(models.Workspace).where(models.Workspace.id == wid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": wid},
        )
    return row


def _workspace_slug(workspace: models.Workspace) -> str:
    """Stable, DNS-friendly slug for the workspace. Uses the row id
    (ULID) to avoid leaking branch names or other user-controlled
    strings into a subdomain — also keeps share links opaque."""
    return workspace.id.lower()


@router.post(
    "/{workspace_id}/preview",
    response_model=PreviewResponse,
    status_code=status.HTTP_200_OK,
)
async def register_preview(
    workspace_id: str,
    payload: RegisterPreviewBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> PreviewResponse:
    workspace = await _workspace_or_404(db, wid=workspace_id)
    try:
        await fetch_project_for(db, actor=user, project_id=workspace.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    manager = _build_manager(settings)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "preview.disabled",
                "reason": (
                    "preview subdomains require GAPT_CADDY_ADMIN_URL + "
                    "GAPT_CADDY_PREVIEW_DOMAIN — see docs/operations"
                ),
            },
        )

    binding = SubdomainBinding(
        workspace_slug=_workspace_slug(workspace),
        upstream_host=payload.upstream_host,
        upstream_port=payload.upstream_port,
    )
    try:
        host = await manager.register(binding)
    except CaddyAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    return PreviewResponse(host=host, workspace_id=workspace_id)


@router.delete(
    "/{workspace_id}/preview",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unregister_preview(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> None:
    workspace = await _workspace_or_404(db, wid=workspace_id)
    try:
        await fetch_project_for(db, actor=user, project_id=workspace.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    manager = _build_manager(settings)
    if manager is None:
        # Idempotent: no Caddy → nothing to remove.
        return None

    try:
        await manager.unregister(_workspace_slug(workspace))
    except CaddyAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc


@router.post(
    "/{workspace_id}/share",
    response_model=ShareLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def mint_share_link(
    workspace_id: str,
    ttl_s: int = Query(default=3600, ge=60),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> ShareLinkResponse:
    workspace = await _workspace_or_404(db, wid=workspace_id)
    try:
        await fetch_project_for(db, actor=user, project_id=workspace.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    if ttl_s > settings.share_link_max_ttl_s:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "share.ttl_too_long",
                "reason": (
                    f"ttl_s={ttl_s} exceeds the cap {settings.share_link_max_ttl_s}; "
                    "raise GAPT_SHARE_LINK_MAX_TTL_S to allow longer windows"
                ),
            },
        )

    try:
        token = issue_share_link(
            workspace_id=workspace_id,
            secret=settings.share_link_secret,
            ttl_s=ttl_s,
        )
    except ShareLinkError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc

    # The URL points at the wildcard subdomain if Caddy is wired,
    # otherwise we return the token only (the UI surfaces a hint).
    if settings.caddy_preview_domain:
        host = f"{_workspace_slug(workspace)}.{settings.caddy_preview_domain}"
        url = f"https://{host}/?share={token}"
    else:
        url = f"share://{token}"

    return ShareLinkResponse(token=token, url=url, expires_in_s=ttl_s)
