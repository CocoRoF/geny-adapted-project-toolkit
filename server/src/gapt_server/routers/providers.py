"""Infrastructure-provider routes — currently Cloudflare only.

Stores the Cloudflare API token in the secret vault under
SYSTEM scope, key `provider.cloudflare.api_token`. The vault row
id is mirrored on the singleton `provider_configs` row (PK
`kind = "cloudflare"`) so config + credential travel together.

Endpoints under `/_gapt/api/providers/cloudflare`:

- `GET    /`                       — read current config (no token)
- `PUT    /`                       — set/replace token + selection
- `DELETE /`                       — clear config + remove token
- `POST   /verify`                 — round-trip the token, discover
                                      accounts/zones/tunnels
- `GET    /tunnel/snapshot`        — current ingress + inferred mode
- `POST   /tunnel/ensure-wildcard` — idempotent ingress upsert
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic runtime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

logger = structlog.get_logger(__name__)

from gapt_server.container import get_app_settings, get_db_session
from gapt_server.db import enums, models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.providers.cloudflare.client import (
    CloudflareApiError,
    CloudflareClient,
)
from gapt_server.domains.providers.cloudflare.migration import (
    LocalConfigError,
    UnsafeTunnelIdError,
    generate_cutover_script,
    generate_revert_script,
    inspect_local,
    run_cutover_script,
)
from gapt_server.domains.providers.cloudflare.service import (
    CloudflareService,
    IngressEntry,
)
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError
from gapt_server.settings import Settings  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.secrets import get_vault


router = APIRouter(prefix="/_gapt/api/providers/cloudflare", tags=["providers"])


CF_PROVIDER_KIND = "cloudflare"
CF_TOKEN_KEY = "provider.cloudflare.api_token"


# ─────────────────────────────────────────────────────────── DTOs ──


class CloudflareConfig(BaseModel):
    """Non-secret config the operator picks from the discovered
    options. None values mean "not selected yet"."""

    account_id: str | None = None
    zone_id: str | None = None
    tunnel_id: str | None = None
    preview_domain: str | None = None
    """The hostname suffix that GAPT subdomain mode emits previews
    under (e.g. `gapt.hrletsgo.me`). Stored here for convenience —
    the canonical value still comes from `GAPT_CADDY_PREVIEW_DOMAIN`."""
    upstream: str | None = None
    """The HTTP(S) URL that the wildcard ingress should forward to.
    Default `http://localhost:38080` (the GAPT Caddy port)."""


class CloudflareConfigResponse(BaseModel):
    configured: bool
    """True iff a token is stored. UI uses this to decide between
    'enter token' setup vs 'manage' view."""
    config: CloudflareConfig
    verified_at: datetime | None
    updated_at: datetime | None


class PutCloudflareConfigRequest(BaseModel):
    api_token: str | None = Field(
        default=None,
        description=(
            "If provided, the new token is written to the vault. Omit "
            "to update non-secret fields without rotating credentials."
        ),
    )
    config: CloudflareConfig


class CloudflareVerifyResponse(BaseModel):
    """Result of `POST /verify` — the token round-tripped and we
    enumerated what it can see."""

    token: dict[str, Any]
    accounts: list[dict[str, Any]]
    tunnels_by_account: dict[str, list[dict[str, Any]]]
    zones: list[dict[str, Any]]
    warnings: list[str] = []
    """Non-fatal issues — e.g. token lacks Account scope so we
    derived accounts from Zone ownership instead. UI surfaces these
    so the operator knows what extra scope to add."""


class TunnelSnapshotResponse(BaseModel):
    mode: str
    """`"remote_managed" | "local_config" | "unknown"` — drives the
    UI's mode badge + migration hint."""
    ingress: list[dict[str, Any]]
    warp_routing: dict[str, Any] | None
    raw: dict[str, Any] | None = None
    """Original API body, only included when caller passed
    `?debug=true`."""


class EnsureWildcardRequest(BaseModel):
    wildcard_hostname: str | None = None
    """Defaults to `*.<config.preview_domain>` when empty."""
    upstream: str | None = None
    """Defaults to `config.upstream` then `http://localhost:38080`."""


class LocalInspectionResponse(BaseModel):
    path: str
    exists: bool
    readable: bool
    raw_text: str
    tunnel_id: str | None
    tunnel_uuid: str | None
    """API-shaped UUID, extracted from credentials_file when the
    `tunnel:` field is a friendly name."""
    credentials_file: str | None
    ingress: list[dict[str, Any]]


class MigrationPushRequest(BaseModel):
    """Optional overrides for push-to-remote — when the operator
    has entered account_id / tunnel_id in the UI but hasn't saved
    yet, we still want the migration to proceed."""

    account_id: str | None = None
    tunnel_id: str | None = None


class MigrationScriptResponse(BaseModel):
    filename: str
    sudo_command: str
    """The single-line command the operator should paste into their
    terminal. `bash <(curl -fsSL <url>)` would be slicker but
    needs a download endpoint; this version embeds the script
    inline via a heredoc-friendly wrapper for copy/paste."""
    script: str


class MigrationVerifyResponse(BaseModel):
    ok: bool
    mode: str
    """`remote_managed` / `local_config` / `unknown`."""
    connection_summary: str
    """Summary of how many active cloudflared connectors Cloudflare
    sees for this tunnel."""
    message: str


class CertStatusResponse(BaseModel):
    """Reports the wildcard-cert state for the configured zone +
    surfaces dashboard deep-links so the operator can fix it in one
    click. None values mean we couldn't determine (token scope, no
    zone selected, ...)."""

    zone_id: str | None
    zone_name: str | None
    preview_domain: str | None
    wildcard_hostname: str | None
    """Convenience: `*.<preview-domain>`."""

    has_wildcard_cert: bool
    """True iff a certificate pack covering `*.<preview-domain>` was
    found via the API. False = needs action."""

    needs_acm: bool
    """True when the desired wildcard is more than one level deep
    (e.g. `*.gapt.hrletsgo.me` — two labels beyond the zone apex).
    Cloudflare's free Universal SSL only issues `*.<apex>` covers,
    so deeper wildcards require Advanced Certificate Manager
    (`$10/mo`) regardless of plan."""

    existing_covering_certs: list[str] = []
    """Hostnames of currently-active certificate packs in the
    zone — e.g. `["*.hrletsgo.me", "hrletsgo.me"]`. UI uses this
    to suggest "switch preview_domain to the apex and use the
    cert you already have, for free"."""

    alternative_preview_domain: str | None = None
    """When `needs_acm=True` but an existing cert already covers
    the zone apex's first-level wildcard, this is the apex (e.g.
    `hrletsgo.me`). Switching `GAPT_CADDY_PREVIEW_DOMAIN` to this
    value sidesteps ACM entirely. None when no alternative exists."""

    total_tls_enabled: bool | None
    """None when we couldn't query (e.g. token missing zone scope)."""

    total_tls_supported: bool
    """Cloudflare gates Total TLS by plan — but the API is
    accessible on all paid + free zones. We default to True and
    let actual API call failure surface the truth."""

    dashboard_url: str | None
    """Deep-link to Cloudflare dashboard → SSL/TLS → Edge
    Certificates for this exact zone. Operator can click straight
    through instead of navigating manually."""

    can_enable_via_api: bool
    """True when token has the scope to PATCH Total TLS. False = UI
    falls back to dashboard link."""

    message: str
    """Human-readable summary of the recommended next action."""


class EnableTotalTlsRequest(BaseModel):
    certificate_authority: str = Field(
        default="google",
        description=(
            "CA that issues certs under Total TLS. Defaults to "
            "Google (Public Trust); `lets_encrypt` and `ssl_com` "
            "are the other options."
        ),
    )


class EnableTotalTlsResponse(BaseModel):
    ok: bool
    message: str
    raw: dict[str, Any] | None = None


class RunCutoverRequest(BaseModel):
    sudo_password: str | None = Field(
        default=None,
        description=(
            "Operator's sudo password. Sent over HTTPS, piped to "
            "`sudo -S` via stdin, never logged or persisted. May be "
            "null when the host has a NOPASSWD sudoers rule for the "
            "involved commands (rare in default installs)."
        ),
    )
    tunnel_id: str | None = None
    """Optional override — same fallback chain as push-to-remote."""


class RunCutoverResponse(BaseModel):
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    message: str


# ────────────────────────────────────────────────── helpers ──


async def _load_config(db: AsyncSession) -> models.ProviderConfig | None:
    row = await db.get(models.ProviderConfig, CF_PROVIDER_KIND)
    return row


async def _read_token(db: AsyncSession, vault: SecretVault) -> str | None:
    """Fetch the system-scoped Cloudflare token by key. Returns None
    when not configured."""
    items = await vault.list(
        db,
        scope=enums.SecretOwnerScope.SYSTEM,
        owner_id="admin",
    )
    target = next(
        (md for md in items if md.key_name == CF_TOKEN_KEY),
        None,
    )
    if target is None:
        return None
    try:
        return await vault.read(
            db, secret_id=target.id, purpose="provider.cloudflare", actor_id="admin"
        )
    except SecretVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc


def _require_token(token: str | None) -> str:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.not_configured",
                "reason": "Cloudflare provider not configured — store an API token first.",
            },
        )
    return token


def _config_dict(row: models.ProviderConfig | None) -> CloudflareConfig:
    if row is None:
        return CloudflareConfig()
    raw = row.config or {}
    return CloudflareConfig(
        account_id=raw.get("account_id"),
        zone_id=raw.get("zone_id"),
        tunnel_id=raw.get("tunnel_id"),
        preview_domain=raw.get("preview_domain"),
        upstream=raw.get("upstream"),
    )


# ────────────────────────────────────────────────── endpoints ──


@router.get("", response_model=CloudflareConfigResponse)
async def get_config(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> CloudflareConfigResponse:
    row = await _load_config(db)
    return CloudflareConfigResponse(
        configured=bool(row and row.token_secret_id),
        config=_config_dict(row),
        verified_at=row.verified_at if row else None,
        updated_at=row.updated_at if row else None,
    )


@router.put("", response_model=CloudflareConfigResponse)
async def put_config(
    payload: PutCloudflareConfigRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> CloudflareConfigResponse:
    row = await _load_config(db)
    if row is None:
        row = models.ProviderConfig(kind=CF_PROVIDER_KIND, config={})
        db.add(row)

    # Rotate or first-write the token.
    if payload.api_token:
        if row.token_secret_id:
            try:
                await vault.rotate(
                    db,
                    secret_id=row.token_secret_id,
                    new_value=payload.api_token,
                )
            except SecretVaultError:
                # Vault row was deleted out-of-band — fall through to store
                row.token_secret_id = None
        if not row.token_secret_id:
            md = await vault.store(
                db,
                scope=enums.SecretOwnerScope.SYSTEM,
                owner_id="admin",
                key_name=CF_TOKEN_KEY,
                value=payload.api_token,
            )
            row.token_secret_id = md.id

    # Detect a preview_domain change so we can auto-ensure the
    # new wildcard ingress in the Cloudflare tunnel — that's the
    # one Caddy-side prerequisite for a domain swap and the only
    # piece the operator would otherwise have to remember to click.
    prev_domain = ((row.config or {}).get("preview_domain") or "").strip().lower()
    row.config = payload.config.model_dump(exclude_none=False)
    await db.commit()
    await db.refresh(row)

    new_domain = (row.config.get("preview_domain") or "").strip().lower()
    auto_ensured_actions: list[str] = []
    if (
        row.token_secret_id
        and new_domain
        and new_domain != prev_domain
        and row.config.get("account_id")
        and row.config.get("tunnel_id")
    ):
        try:
            token = await _read_token(db, vault)
            if token:
                client = CloudflareClient(token)
                svc = CloudflareService(client)
                wildcard = f"*.{new_domain}"
                upstream = (
                    row.config.get("upstream") or "http://localhost:38080"
                )

                # 1. Cloudflared tunnel ingress (always tryable —
                # requires Account:Cloudflare Tunnel:Edit).
                snap = await svc.snapshot(
                    row.config["account_id"], row.config["tunnel_id"]
                )
                if snap.mode == "remote_managed" and not any(
                    e.hostname == wildcard for e in snap.ingress
                ):
                    await svc.ensure_wildcard_ingress(
                        row.config["account_id"],
                        row.config["tunnel_id"],
                        wildcard_hostname=wildcard,
                        upstream=upstream,
                    )
                    auto_ensured_actions.append(f"ingress:{wildcard}")

                # 2. Wildcard DNS record — only attempted when a
                # zone is selected AND the token has Zone:DNS:Edit.
                # For cloudflared tunnels the canonical target is
                # `<tunnel-uuid>.cfargotunnel.com`. Idempotent: skip
                # if a record with the same name already exists.
                zone_id = row.config.get("zone_id")
                if zone_id:
                    try:
                        existing = await client.list_dns_records(
                            zone_id, name=wildcard
                        )
                        if not existing:
                            tunnel_target = (
                                f"{row.config['tunnel_id']}.cfargotunnel.com"
                            )
                            await client.create_dns_record(
                                zone_id,
                                type="CNAME",
                                name=wildcard,
                                content=tunnel_target,
                                proxied=True,
                                comment="GAPT preview wildcard (auto-created)",
                            )
                            auto_ensured_actions.append(f"dns:{wildcard}")
                    except CloudflareApiError as exc:
                        # 403 = scope missing. Logged but not fatal —
                        # the diagnose will surface the missing DNS.
                        logger.info(
                            "providers.cloudflare.put.dns_auto_skip",
                            error=str(exc),
                            wildcard=wildcard,
                        )
        except CloudflareApiError:
            # Best-effort — operator can still hit the explicit
            # "Configure wildcard ingress" button.
            pass

    response = CloudflareConfigResponse(
        configured=bool(row.token_secret_id),
        config=_config_dict(row),
        verified_at=row.verified_at,
        updated_at=row.updated_at,
    )
    if auto_ensured_actions:
        logger.info(
            "providers.cloudflare.put.auto_ensured",
            actions=auto_ensured_actions,
        )
    return response


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> None:
    row = await _load_config(db)
    if row is None:
        return
    secret_id = row.token_secret_id
    await db.delete(row)
    await db.flush()
    if secret_id:
        try:
            await vault.delete(db, secret_id=secret_id)
        except SecretVaultError:
            # If the vault row is already gone, deleting the config
            # row is still the correct outcome.
            pass
    await db.commit()


@router.post("/verify", response_model=CloudflareVerifyResponse)
async def verify(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> CloudflareVerifyResponse:
    token = _require_token(await _read_token(db, vault))
    svc = CloudflareService(CloudflareClient(token))
    try:
        data = await svc.verify_and_discover()
    except CloudflareApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider.cloudflare.verify_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    # Touch verified_at on success so the UI can show "last verified".
    row = await _load_config(db)
    if row is not None:
        row.verified_at = datetime.utcnow()
        await db.commit()
    return CloudflareVerifyResponse(**data)


@router.get("/tunnel/snapshot", response_model=TunnelSnapshotResponse)
async def tunnel_snapshot(
    debug: bool = False,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> TunnelSnapshotResponse:
    row = await _load_config(db)
    cfg = _config_dict(row)
    if not (cfg.account_id and cfg.tunnel_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.incomplete",
                "reason": "account_id + tunnel_id must be selected first.",
            },
        )
    token = _require_token(await _read_token(db, vault))
    svc = CloudflareService(CloudflareClient(token))
    try:
        snap = await svc.snapshot(cfg.account_id, cfg.tunnel_id)
    except CloudflareApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider.cloudflare.snapshot_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    return TunnelSnapshotResponse(
        mode=snap.mode,
        ingress=[e.to_api() for e in snap.ingress],
        warp_routing=snap.warp_routing,
        raw=snap.raw if debug else None,
    )


@router.post(
    "/tunnel/ensure-wildcard", response_model=TunnelSnapshotResponse
)
async def ensure_wildcard(
    payload: EnsureWildcardRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> TunnelSnapshotResponse:
    row = await _load_config(db)
    cfg = _config_dict(row)
    if not (cfg.account_id and cfg.tunnel_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.incomplete",
                "reason": "account_id + tunnel_id must be selected first.",
            },
        )

    hostname = payload.wildcard_hostname or (
        f"*.{cfg.preview_domain}" if cfg.preview_domain else ""
    )
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_hostname",
                "reason": "wildcard_hostname missing and preview_domain not configured.",
            },
        )
    upstream = payload.upstream or cfg.upstream or "http://localhost:38080"

    token = _require_token(await _read_token(db, vault))
    svc = CloudflareService(CloudflareClient(token))
    try:
        snap = await svc.ensure_wildcard_ingress(
            cfg.account_id,
            cfg.tunnel_id,
            wildcard_hostname=hostname,
            upstream=upstream,
        )
    except CloudflareApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY if exc.status >= 500 else exc.status,
            detail={
                "code": "provider.cloudflare.ensure_wildcard_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    return TunnelSnapshotResponse(
        mode=snap.mode,
        ingress=[e.to_api() for e in snap.ingress],
        warp_routing=snap.warp_routing,
        raw=None,
    )


# ─────────────────────── local→remote tunnel migration ──


@router.get("/migration/inspect-local", response_model=LocalInspectionResponse)
async def migration_inspect_local(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> LocalInspectionResponse:
    """Read the host's `cloudflared` config.yml and return the
    parsed ingress + tunnel id. No mutation — purely informational."""
    try:
        result = inspect_local()
    except LocalConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.local_config_unreadable",
                "reason": str(exc),
            },
        ) from exc
    return LocalInspectionResponse(
        path=result.path,
        exists=result.exists,
        readable=result.readable,
        raw_text=result.raw_text,
        tunnel_id=result.tunnel_id,
        tunnel_uuid=result.tunnel_uuid,
        credentials_file=result.credentials_file,
        ingress=result.ingress,
    )


@router.post("/migration/push-to-remote", response_model=TunnelSnapshotResponse)
async def migration_push_to_remote(
    payload: MigrationPushRequest | None = None,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> TunnelSnapshotResponse:
    """Replay the local YAML's `ingress` into Cloudflare's remote
    config. Account_id / tunnel_id are resolved in order:
    request body → saved provider config → local YAML (for tunnel
    UUID only). When the body supplies values they're also
    persisted back to the provider row so subsequent calls don't
    need them again."""
    payload = payload or MigrationPushRequest()
    row = await _load_config(db)
    cfg = _config_dict(row)

    try:
        local = inspect_local()
    except LocalConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.local_config_unreadable",
                "reason": str(exc),
            },
        ) from exc
    if not local.exists or not local.readable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.local_config_missing",
                "reason": f"`{local.path}` not found or unreadable.",
            },
        )
    tunnel_id = payload.tunnel_id or cfg.tunnel_id or local.tunnel_uuid
    if not tunnel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_tunnel_id",
                "reason": (
                    "tunnel UUID not available — provide one in the request "
                    "body, save it via Settings, or ensure `{path}` references "
                    "the UUID (currently `{tid}`)."
                ).format(path=local.path, tid=local.tunnel_id or "—"),
            },
        )
    account_id = payload.account_id or cfg.account_id
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_account_id",
                "reason": (
                    "account_id missing — provide one in the request body, "
                    "save it via Settings, or run Verify to discover it."
                ),
            },
        )

    token = _require_token(await _read_token(db, vault))
    client = CloudflareClient(token)
    svc = CloudflareService(client)

    # Build the API-shaped config from the local ingress, preserving
    # order. Make sure there's a terminal catch-all — Cloudflare
    # rejects ingress arrays without one.
    entries = [IngressEntry.from_api(e) for e in local.ingress]
    if not entries or not (
        not entries[-1].hostname and entries[-1].service.startswith("http_status:")
    ):
        entries.append(IngressEntry(hostname="", service="http_status:404"))

    try:
        await client.put_tunnel_configuration(
            account_id,
            tunnel_id,
            config={"ingress": [e.to_api() for e in entries]},
        )
        snap = await svc.snapshot(account_id, tunnel_id)
    except CloudflareApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider.cloudflare.push_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    # Mirror any resolved-but-unsaved selections back to the
    # provider row so subsequent UI actions don't ask the operator
    # to pick again.
    if row is not None:
        new_cfg = dict(row.config or {})
        changed = False
        if not cfg.tunnel_id:
            new_cfg["tunnel_id"] = tunnel_id
            changed = True
        if not cfg.account_id:
            new_cfg["account_id"] = account_id
            changed = True
        if changed:
            row.config = new_cfg
            await db.commit()

    return TunnelSnapshotResponse(
        mode=snap.mode,
        ingress=[e.to_api() for e in snap.ingress],
        warp_routing=snap.warp_routing,
        raw=None,
    )


@router.get("/migration/script", response_model=MigrationScriptResponse)
async def migration_script(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> MigrationScriptResponse:
    """Generate the sudo cutover script. The operator runs this
    once on the host; GAPT verifies the result afterward."""
    row = await _load_config(db)
    cfg = _config_dict(row)
    tunnel_id = cfg.tunnel_id
    if not tunnel_id:
        # Try the local config as a fallback. Cloudflared's CLI
        # accepts both the UUID and the friendly name in `tunnel run`,
        # so the cutover script's command line works with either.
        try:
            local = inspect_local()
            tunnel_id = local.tunnel_uuid or local.tunnel_id
        except LocalConfigError:
            tunnel_id = None
    if not tunnel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_tunnel_id",
                "reason": (
                    "tunnel id missing from both provider config and "
                    "local cloudflared config — select a tunnel first."
                ),
            },
        )
    script = generate_cutover_script(tunnel_id)
    return MigrationScriptResponse(
        filename="gapt-cloudflared-migrate.sh",
        sudo_command=(
            "sudo bash -c 'cat > /tmp/gapt-cloudflared-migrate.sh <<EOF\n"
            f"{script}EOF\n"
            "bash /tmp/gapt-cloudflared-migrate.sh'"
        ),
        script=script,
    )


@router.get("/migration/revert-script", response_model=MigrationScriptResponse)
async def migration_revert_script(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> MigrationScriptResponse:
    """Generates a script that removes the GAPT systemd drop-in,
    reverting cloudflared back to local config.yml mode."""
    script = generate_revert_script()
    return MigrationScriptResponse(
        filename="gapt-cloudflared-revert.sh",
        sudo_command=(
            "sudo bash -c 'cat > /tmp/gapt-cloudflared-revert.sh <<EOF\n"
            f"{script}EOF\n"
            "bash /tmp/gapt-cloudflared-revert.sh'"
        ),
        script=script,
    )


@router.post("/migration/run-cutover", response_model=RunCutoverResponse)
async def migration_run_cutover(
    payload: RunCutoverRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> RunCutoverResponse:
    """Execute the cutover script on the host with operator-supplied
    sudo password. Only works in deployments where the GAPT server
    runs directly on the host with sudo access — i.e. dev / bare-
    metal installs. In containerised prod the script must be run
    by hand (the manual copy-paste workflow stays available).

    The password reaches `sudo -S` via stdin and is never logged,
    persisted, or echoed in the response. Audit emits a
    `provider.cloudflare.migration.cutover_run` entry with the exit
    code only."""
    import asyncio  # noqa: PLC0415

    row = await _load_config(db)
    cfg = _config_dict(row)
    tunnel_id = payload.tunnel_id or cfg.tunnel_id
    if not tunnel_id:
        try:
            local = inspect_local()
            tunnel_id = local.tunnel_uuid or local.tunnel_id
        except LocalConfigError:
            tunnel_id = None
    if not tunnel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_tunnel_id",
                "reason": (
                    "tunnel id missing — provide one in the request, save "
                    "it via Settings, or ensure local cloudflared config is "
                    "readable."
                ),
            },
        )
    try:
        script = generate_cutover_script(tunnel_id)
    except UnsafeTunnelIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.unsafe_tunnel_id",
                "reason": str(exc),
            },
        ) from exc

    try:
        result = await run_cutover_script(
            script, sudo_password=payload.sudo_password, timeout_s=60.0
        )
    except FileNotFoundError as exc:
        # `sudo` binary missing — most likely the GAPT process is
        # inside a minimal container.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_sudo",
                "reason": (
                    "`sudo` is not available in this GAPT process — auto-run "
                    "needs a host install (not containerised). Use the "
                    "manual copy-paste flow instead."
                ),
            },
        ) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "provider.cloudflare.cutover_timeout",
                "reason": (
                    "Cutover script did not finish within 60s — check "
                    "`systemctl status cloudflared` on the host."
                ),
            },
        ) from exc

    # Classify the failure mode so the UI can show a useful
    # message without forcing the operator to parse stderr.
    stderr_lc = result.stderr.lower()
    if result.ok:
        message = (
            "Script executed successfully. cloudflared was restarted "
            "into remote-managed mode."
        )
    elif "password is required" in stderr_lc or "no password was provided" in stderr_lc:
        message = (
            "sudo requires a password and none was supplied. Enter your "
            "sudo password and click \"Run with sudo\" again."
        )
    elif "incorrect password" in stderr_lc or "try again" in stderr_lc:
        message = "sudo rejected the password. Re-enter and retry."
    elif "not allowed to execute" in stderr_lc:
        message = (
            "sudo refused — the operator account doesn't have permission "
            "to run the migration commands. Use the manual flow or add "
            "the required sudoers rule."
        )
    else:
        message = (
            f"Script exited with code {result.exit_code}. See log below "
            "and `journalctl -u cloudflared -n 100` on the host."
        )
    return RunCutoverResponse(
        ok=result.ok,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        message=message,
    )


@router.post("/migration/verify", response_model=MigrationVerifyResponse)
async def migration_verify(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> MigrationVerifyResponse:
    """Re-snapshot + check that the tunnel reports remote-managed.
    Also looks up the tunnel's connection count via the API so the
    operator sees that cloudflared came back online."""
    row = await _load_config(db)
    cfg = _config_dict(row)
    if not (cfg.account_id and cfg.tunnel_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.incomplete",
                "reason": "account_id + tunnel_id required before verifying.",
            },
        )
    token = _require_token(await _read_token(db, vault))
    client = CloudflareClient(token)
    svc = CloudflareService(client)
    try:
        snap = await svc.snapshot(cfg.account_id, cfg.tunnel_id)
        tunnels = await client.list_tunnels(cfg.account_id)
    except CloudflareApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider.cloudflare.verify_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    this_tunnel = next(
        (t for t in tunnels if t.get("id") == cfg.tunnel_id), None
    )
    connections = (
        len(this_tunnel.get("connections") or []) if this_tunnel else 0
    )
    ok = snap.mode == "remote_managed" and connections > 0
    if snap.mode == "remote_managed" and connections == 0:
        message = (
            "Mode flipped to remote-managed but no live cloudflared "
            "connector — the daemon may not have restarted yet. Wait "
            "30s and re-verify."
        )
    elif snap.mode == "remote_managed":
        message = "Migration complete — cloudflared is fetching config from Cloudflare."
    elif snap.mode == "local_config":
        message = (
            "Tunnel still in local_config mode. The cutover script may "
            "not have been run yet, or it didn't complete cleanly. Run "
            "`sudo systemctl status cloudflared` to inspect."
        )
    else:
        message = (
            "Tunnel mode reported as `unknown` — Cloudflare hasn't "
            "received a connector check-in for the new remote config "
            "yet. Wait ~30s and re-verify."
        )
    return MigrationVerifyResponse(
        ok=ok,
        mode=snap.mode,
        connection_summary=f"{connections} active cloudflared connector(s)",
        message=message,
    )


# ───────────────────────────────────── wildcard cert helpers ──


def _zone_dashboard_url(account_id: str | None, zone_name: str | None) -> str | None:
    """Cloudflare's SSL/TLS → Edge Certificates page URL. Requires
    account_id + zone NAME (not zone id) — that's just how their
    dashboard URLs are structured."""
    if not account_id or not zone_name:
        return None
    return (
        f"https://dash.cloudflare.com/{account_id}/{zone_name}/"
        "ssl-tls/edge-certificates"
    )


@router.get("/cert/status", response_model=CertStatusResponse)
async def cert_status(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> CertStatusResponse:
    """Inspect the zone's wildcard-cert situation. Cloudflare's
    Universal SSL doesn't cover `*.<domain>` — operators need
    either Total TLS (free, recommended) or an Advanced Certificate
    ($10/mo) for HTTPS handshakes against preview subdomains to
    succeed. This endpoint reports current state + tells the UI
    what action to surface.

    Best-effort: any API error degrades gracefully to "unknown"
    fields rather than failing the request."""
    row = await _load_config(db)
    cfg = _config_dict(row)
    # Fallback chain for preview_domain: saved provider config →
    # server's GAPT_CADDY_PREVIEW_DOMAIN env var. Without this the
    # cert guide is useless until the operator saves provider
    # settings, which is unnecessary friction.
    if not cfg.preview_domain and settings.caddy_preview_domain:
        cfg.preview_domain = settings.caddy_preview_domain
    if not row or not row.token_secret_id:
        return CertStatusResponse(
            zone_id=cfg.zone_id,
            zone_name=None,
            preview_domain=cfg.preview_domain,
            wildcard_hostname=(
                f"*.{cfg.preview_domain}" if cfg.preview_domain else None
            ),
            has_wildcard_cert=False,
            total_tls_enabled=None,
            total_tls_supported=True,
            dashboard_url=None,
            can_enable_via_api=False,
            message=(
                "Cloudflare provider not configured. Add a token and "
                "select a zone first."
            ),
        )

    token = _require_token(await _read_token(db, vault))
    client = CloudflareClient(token)
    zone_id = cfg.zone_id
    preview_domain = cfg.preview_domain
    wildcard = f"*.{preview_domain}" if preview_domain else None

    # If zone_id wasn't picked yet but a preview_domain exists, try
    # to find the matching zone in the operator's tokens.
    zone_name: str | None = None
    if not zone_id and preview_domain:
        try:
            zones = await client.list_zones(cfg.account_id)
            apex = preview_domain.split(".", 1)[-1] if "." in preview_domain else preview_domain
            for z in zones:
                # Match either exact preview_domain or its apex (e.g.
                # preview_domain="gapt.hrletsgo.me" → zone name
                # "hrletsgo.me" is the actual zone).
                if z.get("name") in (preview_domain, apex):
                    zone_id = z.get("id")
                    zone_name = z.get("name")
                    break
        except CloudflareApiError:
            pass

    if zone_id and not zone_name:
        # Look up the name we'll need for the dashboard deep-link.
        try:
            zones = await client.list_zones(cfg.account_id)
            zone_name = next(
                (z.get("name") for z in zones if z.get("id") == zone_id),
                None,
            )
        except CloudflareApiError:
            pass

    has_wildcard_cert = False
    total_tls_enabled: bool | None = None
    can_enable_via_api = False
    existing_covering_hosts: list[str] = []

    if zone_id:
        try:
            packs = await client.list_certificate_packs(zone_id)
            for p in packs:
                hosts = p.get("hosts") or []
                if p.get("status") != "active":
                    continue
                for h in hosts:
                    if isinstance(h, str) and h not in existing_covering_hosts:
                        existing_covering_hosts.append(h)
                if wildcard and wildcard in hosts:
                    has_wildcard_cert = True
        except CloudflareApiError:
            # Token doesn't have SSL:Read scope — fall through with
            # has_wildcard_cert=False and let the UI prompt for it.
            pass
        try:
            tls = await client.get_total_tls(zone_id)
            total_tls_enabled = bool(tls.get("enabled"))
            # Reaching the GET endpoint successfully strongly implies
            # the token has at least Read scope on SSL. PATCH needs
            # Edit — we'll discover the real answer when the operator
            # clicks "enable" and either gets ok=true or a 403. For
            # now, optimistically advertise the button.
            can_enable_via_api = True
        except CloudflareApiError as exc:
            if exc.status == 403:
                can_enable_via_api = False
            # else: unknown total_tls_enabled stays None

    # Detect: does the desired wildcard need ACM, and is there a
    # cheaper alternative the operator could use? Universal SSL
    # only covers `<apex>` + `*.<apex>` — anything deeper (e.g.
    # `*.gapt.hrletsgo.me`) needs ACM ($10/mo) regardless of plan.
    needs_acm = False
    alternative_preview_domain: str | None = None
    if preview_domain and zone_name:
        # Labels beyond the zone apex. `gapt.hrletsgo.me` in zone
        # `hrletsgo.me` → labels=["gapt"], depth=1. `hrletsgo.me`
        # itself in zone `hrletsgo.me` → labels=[], depth=0.
        depth = 0
        if preview_domain.lower().endswith(zone_name.lower()):
            extra = preview_domain[: -len(zone_name)].rstrip(".")
            depth = len([p for p in extra.split(".") if p]) if extra else 0
        needs_acm = depth >= 1  # >=1 labels deep means *.preview_domain is 2+ deep

        if needs_acm:
            apex_wildcard = f"*.{zone_name}"
            if apex_wildcard in existing_covering_hosts:
                alternative_preview_domain = zone_name

    dashboard_url = _zone_dashboard_url(cfg.account_id, zone_name)

    if has_wildcard_cert:
        message = (
            f"`{wildcard}` is covered by an active certificate pack. "
            "HTTPS handshakes should succeed."
        )
    elif alternative_preview_domain:
        message = (
            f"Your zone already has an active `*.{zone_name}` cert "
            "(free Universal SSL). The current preview domain needs a "
            "deeper wildcard which requires ACM ($10/mo). Easier fix: "
            f"set `GAPT_CADDY_PREVIEW_DOMAIN={alternative_preview_domain}` "
            "to reuse the existing free cert."
        )
    elif needs_acm:
        message = (
            f"`{wildcard}` is a second-level wildcard — Cloudflare's "
            "free Universal SSL doesn't cover it. Enabling Total TLS "
            "or issuing an Advanced Certificate requires Advanced "
            "Certificate Manager ($10/mo)."
        )
    elif total_tls_enabled:
        message = (
            "Total TLS is enabled but Cloudflare hasn't issued the "
            "wildcard cert yet. Wait a few minutes and re-check."
        )
    elif total_tls_enabled is False:
        message = (
            "Total TLS is disabled and no wildcard certificate pack "
            "covers the preview domain. Enable Total TLS for the "
            "quickest fix."
        )
    else:
        message = (
            "Couldn't determine SSL state — token may lack "
            "`Zone:SSL and Certificates:Read`. Use the dashboard link."
        )

    return CertStatusResponse(
        zone_id=zone_id,
        zone_name=zone_name,
        preview_domain=preview_domain,
        wildcard_hostname=wildcard,
        has_wildcard_cert=has_wildcard_cert,
        needs_acm=needs_acm,
        existing_covering_certs=existing_covering_hosts,
        alternative_preview_domain=alternative_preview_domain,
        total_tls_enabled=total_tls_enabled,
        total_tls_supported=True,
        dashboard_url=dashboard_url,
        can_enable_via_api=can_enable_via_api,
        message=message,
    )


@router.post("/cert/enable-total-tls", response_model=EnableTotalTlsResponse)
async def enable_total_tls(
    payload: EnableTotalTlsRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> EnableTotalTlsResponse:
    """Flip Total TLS on for the configured zone — the free
    Cloudflare-side action that auto-issues a wildcard cert. Needs
    `Zone:SSL and Certificates:Edit` on the token."""
    row = await _load_config(db)
    cfg = _config_dict(row)
    if not (row and row.token_secret_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.not_configured",
                "reason": "Cloudflare provider not configured.",
            },
        )
    zone_id = cfg.zone_id
    if not zone_id and cfg.preview_domain:
        token = _require_token(await _read_token(db, vault))
        client = CloudflareClient(token)
        try:
            zones = await client.list_zones(cfg.account_id)
            apex = (
                cfg.preview_domain.split(".", 1)[-1]
                if "." in cfg.preview_domain
                else cfg.preview_domain
            )
            zone_id = next(
                (z.get("id") for z in zones if z.get("name") in (cfg.preview_domain, apex)),
                None,
            )
        except CloudflareApiError:
            zone_id = None
    if not zone_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "provider.cloudflare.no_zone",
                "reason": (
                    "Zone not selected and couldn't be derived from "
                    "preview_domain. Pick a zone in provider settings."
                ),
            },
        )

    token = _require_token(await _read_token(db, vault))
    client = CloudflareClient(token)
    try:
        result = await client.enable_total_tls(
            zone_id, certificate_authority=payload.certificate_authority
        )
    except CloudflareApiError as exc:
        if exc.status == 403:
            return EnableTotalTlsResponse(
                ok=False,
                message=(
                    "Token lacks `Zone:SSL and Certificates:Edit`. Re-issue "
                    "the token with that scope, or use the dashboard link "
                    "from the cert status card."
                ),
                raw=None,
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider.cloudflare.total_tls_failed",
                "reason": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    # Mirror the picked zone_id back to the provider row so the
    # next status check uses it directly.
    if row is not None and not cfg.zone_id:
        row.config = {**(row.config or {}), "zone_id": zone_id}
        await db.commit()

    return EnableTotalTlsResponse(
        ok=True,
        message=(
            "Total TLS enabled. Cloudflare will issue the wildcard cert "
            "within a few minutes — re-run the diagnose to confirm."
        ),
        raw=result,
    )
