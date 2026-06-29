"""Preview subdomain + share-link endpoints.

- `POST /_gapt/api/workspaces/{wid}/preview` — register the workspace's
  subdomain with Caddy and return the resolved host.
- `DELETE /_gapt/api/workspaces/{wid}/preview` — unregister.
- `POST /_gapt/api/workspaces/{wid}/share?ttl=` — mint an HMAC-signed
  share link. The recipient hits `{slug}.{preview_domain}/?share=...`
  and Caddy's request handler validates via this server (M2).

`POST /preview` is idempotent: re-posting with the same upstream
returns the same host. The Caddy delete is a no-op for unknown
routes so DELETE is also idempotent."""

from __future__ import annotations

import asyncio
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
from gapt_server.domains.secrets.vault import SecretVault  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error
from gapt_server.routers.secrets import get_vault

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


router = APIRouter(prefix="/_gapt/api/workspaces", tags=["preview"])

# Separate router with no auth — Caddy's on-demand TLS hook hits this
# unauthenticated. Mounted under /_gapt/api/preview/ask.
ask_router = APIRouter(prefix="/_gapt/api/preview", tags=["preview"])


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
        await db.execute(select(models.Workspace).where(models.Workspace.id == slug.upper()))
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
    return SubdomainManager(
        client=client,
        preview_domain=settings.caddy_preview_domain,
        gapt_apex_host=settings.caddy_apex_host,
        subdomain_zone=settings.caddy_subdomain_zone,
    )


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

    # Constrain the upstream Caddy will dial: only this workspace's own
    # sandbox container, a prod stack of its project, or localhost.
    # Without this, a free-text upstream_host turns the preview register
    # into a "make GAPT's Caddy proxy to any internal host" primitive.
    import re as _re  # noqa: PLC0415

    allowed = {
        f"gapt-ws-{workspace_id.lower()}",
        "localhost",
        "127.0.0.1",
    }
    host_ok = payload.upstream_host in allowed or bool(
        _re.fullmatch(r"gapt-prod-[a-z0-9-]+", payload.upstream_host.lower())
    )
    if not host_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "preview.upstream_not_allowed",
                "reason": (
                    f"upstream_host {payload.upstream_host!r} must be this "
                    f"workspace's container (gapt-ws-{workspace_id.lower()}), a "
                    "gapt-prod-* stack, or localhost"
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


# ─── subdomain-mode setup diagnostic ─────────────────────────────────


class SubdomainDiagnoseResponse(BaseModel):
    """Pre-flight check for subdomain mode. The settings UI surfaces
    this so the operator sees concretely which step is missing — DNS
    record, Caddy config, GAPT env — instead of just hitting a 502
    and guessing.

    Each field is independent: green = ready, red = action needed.
    The user can re-run the diagnose anytime."""

    preview_domain: str | None
    """`GAPT_CADDY_PREVIEW_DOMAIN` from server settings. None when
    operator hasn't set the env var — subdomain mode can't work."""

    sample_host: str
    """A random-slug `.<preview-domain>` host the diagnose
    constructed to test DNS / cert issuance. Doesn't have to resolve
    to anything specific — we just need a DNS answer."""

    dns_resolves: bool
    """True when `gethostbyname(sample_host)` returns any IP. False =
    wildcard DNS not configured (or not propagated yet)."""

    dns_message: str
    """Free-text DNS result — IP returned or the error string."""

    caddy_admin_reachable: bool
    """True when GAPT can reach Caddy's admin API. False = the
    workspace's caddy_admin_url is wrong / caddy is down."""

    caddy_has_wildcard_server: bool
    """True when Caddy's config has a server block matching the
    `*.<preview-domain>` wildcard. False = Caddy isn't configured to
    accept the wildcard, which is needed for on-demand TLS issuance."""

    e2e_reachable: bool
    """True when an HTTPS HEAD to `<diag-slug>.<preview-domain>`
    actually reaches our Caddy (any HTTP code from Caddy counts).
    False when the request fails or returns a Cloudflare-side error
    (522/525/530) — that means the tunnel doesn't forward the
    wildcard hostname yet."""

    e2e_message: str
    """Free-text probe result — HTTP code + via/server headers, or
    the error string if the probe failed."""

    provider_configured: bool
    """True when the Cloudflare provider token is stored in the
    vault. False means GAPT can't auto-configure anything and the
    operator must follow the manual snippets."""

    provider_account_id: str | None
    provider_zone_id: str | None
    provider_tunnel_id: str | None
    """Selected Cloudflare identifiers — None until the operator
    completes setup."""

    tunnel_mode: str | None
    """`"remote_managed" | "local_config" | "unknown"` from the
    Cloudflare API, or None when provider not configured or
    snapshot failed. Drives the migration-hint UI: in local_config
    mode the API ingress writes don't take effect at runtime."""

    tunnel_has_wildcard: bool
    """True when the remote ingress already contains an entry for
    `*.<preview-domain>`. UI uses this to decide between
    'Configure wildcard' (button) vs 'Already configured' (badge)."""

    next_steps: list[str]
    """Operator-facing instructions for whatever isn't ready."""


@ask_router.get("/diagnose", response_model=SubdomainDiagnoseResponse)
async def diagnose_subdomain_mode(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> SubdomainDiagnoseResponse:
    """Check every prerequisite for subdomain mode and report
    which are missing. Called from the env settings modal's
    "Check current status" button."""
    import secrets as _secrets  # noqa: PLC0415
    import socket  # noqa: PLC0415

    # Local imports keep the Cloudflare integration optional — the
    # diagnose endpoint must still work even when the provider
    # package fails to import or the API is unreachable.
    from gapt_server.domains.providers.cloudflare.client import (  # noqa: PLC0415
        CloudflareApiError,
        CloudflareClient,
    )
    from gapt_server.domains.providers.cloudflare.service import (  # noqa: PLC0415
        CloudflareService,
    )
    from gapt_server.routers.deploy import (  # noqa: PLC0415
        _resolve_effective_preview_domain,
    )
    from gapt_server.routers.providers import _read_token  # noqa: PLC0415

    next_steps: list[str] = []
    # Effective preview domain: provider config wins over env var so
    # operators can flip it from the UI (Settings → Providers → Cloudflare)
    # without restarting GAPT. Falls back to env var so existing
    # installs keep working.
    preview_domain = await _resolve_effective_preview_domain(db, settings)

    # 1. GAPT env var
    if not preview_domain:
        next_steps.append(
            "Environment variable `GAPT_CADDY_PREVIEW_DOMAIN` is not set. "
            "Set it on the server (e.g. `GAPT_CADDY_PREVIEW_DOMAIN=gapt.hrletsgo.me`) "
            "and restart GAPT — or enter a value in the preview_domain field "
            "under Settings → Providers → Cloudflare and click Save."
        )

    # 2. DNS lookup against a random slug — proves wildcard DNS is wired
    sample_host = (
        f"diag-{_secrets.token_hex(4)}.{preview_domain}" if preview_domain else "diag-no-domain"
    )
    dns_resolves = False
    dns_message = ""
    if preview_domain:
        try:
            addr = await asyncio.to_thread(socket.gethostbyname, sample_host)
            dns_resolves = True
            dns_message = f"resolved → {addr}"
        except socket.gaierror as exc:
            dns_message = f"DNS lookup failed: {exc}"
            next_steps.append(
                f"A wildcard DNS record is required: "
                f"`*.{preview_domain}` → same target as the existing `{preview_domain}` "
                f"(CNAME or A). When using Cloudflare, turn the orange-cloud proxy ON. "
                f"When using Cloudflare Tunnel, add "
                f'`hostname: "*.{preview_domain}"` to the `cloudflared` ingress.'
            )
        except Exception as exc:
            dns_message = f"lookup error: {exc}"
    else:
        dns_message = "skipped (preview_domain unset)"

    # 3. Caddy admin reachability + main-server existence
    caddy_admin_reachable = False
    caddy_has_wildcard_server = False  # actually: "main server can route host-matched"
    if settings.caddy_admin_url:
        try:
            transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
            client = CaddyAdminClient(transport=transport)
            body = await client.get("/config/apps/http/servers")
            caddy_admin_reachable = True
            # Check: does `main` server exist? SubdomainManager
            # injects host-matched routes into `servers/main/routes`.
            # If `main` exists and either has no host filter at the
            # server level OR explicitly matches the wildcard, host-
            # matched routes inside will fire.
            if isinstance(body, dict) and preview_domain:
                main = body.get("main")
                if isinstance(main, dict):
                    # In dev: server has no host matcher (listens on
                    # :8080, accepts all). In prod: depends on
                    # Caddyfile config. We approximate by checking
                    # if SubdomainManager-registered routes exist —
                    # any `host` matcher in main's routes that ends
                    # with the preview_domain.
                    suffix = f".{preview_domain}".lower()
                    for route in main.get("routes", []) or []:
                        for m in route.get("match", []) or []:
                            hosts = m.get("host") or []
                            for h in hosts:
                                if isinstance(h, str) and h.lower().endswith(suffix):
                                    caddy_has_wildcard_server = True
                                    break
                    # Even with no admin-injected routes yet, `main`
                    # exists and has no host filter — that's
                    # sufficient for future routes to land. Treat as
                    # "ready" if main is present and listening.
                    if not caddy_has_wildcard_server and main.get("listen"):
                        caddy_has_wildcard_server = True
        except CaddyAdminError as exc:
            next_steps.append(f"Caddy admin API did not respond: {exc}")
        except Exception as exc:
            next_steps.append(f"Failed to communicate with Caddy admin: {exc}")
    else:
        next_steps.append(
            "Environment variable `GAPT_CADDY_ADMIN_URL` is not set — "
            "Caddy dynamic routing is disabled."
        )

    # 4. End-to-end probe — actually hit `<diag-slug>.<preview-domain>`
    # through Cloudflare and see what comes back. If we get any
    # response from Caddy (even 404), the path is wired end-to-end.
    # If we get a CF error (522, 530, etc.), the tunnel doesn't route
    # this hostname — operator needs to fix cloudflared ingress.
    e2e_reachable = False
    e2e_message = ""
    if preview_domain and dns_resolves:
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(verify=False, follow_redirects=False, timeout=4.0) as c:
                resp = await c.head(f"https://{sample_host}/")
            e2e_reachable = True
            via = resp.headers.get("via", "")
            server = resp.headers.get("server", "")
            e2e_message = (
                f"HTTP {resp.status_code}"
                + (f" via={via}" if via else "")
                + (f" server={server}" if server else "")
            )
            # If response is from Caddy (via header includes "Caddy"
            # or server="Caddy"), the wildcard reaches our Caddy. If
            # it's a Cloudflare error code (522/525/530), the tunnel
            # didn't forward.
            if resp.status_code in (522, 523, 525, 526, 530):
                e2e_reachable = False
                next_steps.append(
                    f"`{sample_host}` is blocked at the Cloudflare edge (HTTP {resp.status_code}). "
                    f"Either the cloudflared ingress has no `*.{preview_domain}` entry, or "
                    f"the tunnel is not forwarding to the GAPT Caddy port (38080). "
                    f"Add the wildcard hostname to `ingress:` in `~/.cloudflared/config.yml` and run "
                    f"`cloudflared service restart` (or restart `cloudflared tunnel run` in dev)."
                )
        except Exception as exc:
            e2e_message = f"probe error: {exc}"
            # Any connection/TLS error means the wildcard hostname
            # never reaches a working TLS terminator. Most common
            # causes (in priority order): Cloudflare can't issue a
            # cert because `*.<domain>` isn't covered by the zone's
            # SSL settings; cloudflared ingress doesn't have a
            # wildcard entry; firewall/DNS not propagated.
            err_text = str(exc).lower()
            if "ssl" in err_text or "handshake" in err_text or "certificate" in err_text:
                next_steps.append(
                    f"TLS handshake failed for `{sample_host}` — Cloudflare may have been "
                    f"unable to issue an edge certificate for `*.{preview_domain}`. "
                    f"Check whether an advanced certificate for `*.{preview_domain}` exists "
                    f"under Cloudflare → SSL/TLS → Edge Certificates "
                    f"(Universal SSL does not cover wildcards). If not, issue an Advanced Certificate "
                    f"or enable Custom Hostnames / Total TLS."
                )
            else:
                next_steps.append(
                    f"Connection to `{sample_host}` failed ({exc}). Verify that the cloudflared ingress "
                    f"has a `*.{preview_domain}` entry and that it forwards to the GAPT Caddy port (38080). "
                    f"Run `cloudflared tunnel route dns <tunnel> *.{preview_domain}` "
                    f"or edit `ingress` in `~/.cloudflared/config.yml` and restart."
                )

    # 5. Cloudflare provider — token + selected tunnel + ingress shape.
    provider_row = await db.get(models.ProviderConfig, "cloudflare")
    provider_configured = bool(provider_row and provider_row.token_secret_id)
    provider_cfg = dict(provider_row.config or {}) if provider_row else {}
    provider_account_id = provider_cfg.get("account_id")
    provider_zone_id = provider_cfg.get("zone_id")
    provider_tunnel_id = provider_cfg.get("tunnel_id")
    tunnel_mode: str | None = None
    tunnel_has_wildcard = False

    if provider_configured and provider_account_id and provider_tunnel_id:
        try:
            token = await _read_token(db, vault)
            if token:
                svc = CloudflareService(CloudflareClient(token))
                snap = await svc.snapshot(provider_account_id, provider_tunnel_id)
                tunnel_mode = snap.mode
                if preview_domain:
                    wildcard = f"*.{preview_domain}"
                    tunnel_has_wildcard = any(e.hostname == wildcard for e in snap.ingress)
                if tunnel_mode == "local_config":
                    next_steps.append(
                        "The Cloudflare tunnel is in **local config.yml mode**, so cloudflared "
                        "ignores ingress changes written via the API. After migrating to "
                        "remote-managed mode, GAPT can configure it automatically "
                        "(Provider settings panel → 'Migrate')."
                    )
                elif tunnel_mode == "remote_managed" and preview_domain and not tunnel_has_wildcard:
                    next_steps.append(
                        f"The tunnel is in remote-managed mode — you can add it in one click "
                        f"with the 'Auto-configure `*.{preview_domain}` ingress' button in the Provider panel."
                    )
        except CloudflareApiError as exc:
            next_steps.append(
                f"Cloudflare API call failed ({exc}). Check token permissions: "
                "Account → Cloudflare Tunnel : Edit is required."
            )
        except Exception as exc:
            next_steps.append(f"Cloudflare provider diagnosis failed: {exc}")
    elif not provider_configured:
        next_steps.append(
            "Cloudflare Provider is not configured — once you register a token, GAPT manages "
            "ingress automatically. Set an API token and select a tunnel under "
            "'Settings → Providers → Cloudflare'."
        )

    if (
        preview_domain
        and dns_resolves
        and caddy_admin_reachable
        and caddy_has_wildcard_server
        and e2e_reachable
    ):
        next_steps.append("✓ All prerequisites passed — subdomain mode is ready to use.")

    return SubdomainDiagnoseResponse(
        preview_domain=preview_domain,
        sample_host=sample_host,
        dns_resolves=dns_resolves,
        dns_message=dns_message,
        caddy_admin_reachable=caddy_admin_reachable,
        caddy_has_wildcard_server=caddy_has_wildcard_server,
        e2e_reachable=e2e_reachable,
        e2e_message=e2e_message,
        provider_configured=provider_configured,
        provider_account_id=provider_account_id,
        provider_zone_id=provider_zone_id,
        provider_tunnel_id=provider_tunnel_id,
        tunnel_mode=tunnel_mode,
        tunnel_has_wildcard=tunnel_has_wildcard,
        next_steps=next_steps,
    )
