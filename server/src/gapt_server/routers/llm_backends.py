"""`/_gapt/api/llm-backends` — backend health + Claude Code auth.

Phase G.1. Surface for the Settings → LLM Backends panel and the
Claude Code CLI auth modal.

Endpoint map::

    GET    /_gapt/api/llm-backends/health
           grid of every provider's health (anthropic, openai,
           google, vllm, claude_code_cli).

    POST   /_gapt/api/llm-backends/cli/claude-code/recheck
           re-probe only the Claude Code CLI card.

    GET    /_gapt/api/llm-backends/cli/claude-code/auth/status
           wraps `claude auth status --json`.

    POST   /_gapt/api/llm-backends/cli/claude-code/auth/login
           spawn `claude auth login`, return a job_id.

    POST   /_gapt/api/llm-backends/cli/claude-code/auth/logout
           one-shot `claude auth logout`.

    POST   /_gapt/api/llm-backends/cli/claude-code/test
           one-shot `claude --print "ping"` to verify auth.

    GET    /_gapt/api/llm-backends/auth/jobs/{job_id}/events  (SSE)
           live stream of the auth job subprocess output. Replays
           history first so a client that connects after the URL
           was printed still sees it.

    POST   /_gapt/api/llm-backends/auth/jobs/{job_id}/input
           forward a line of user input (e.g. the device-code
           auth code) to the subprocess stdin.

    POST   /_gapt/api/llm-backends/auth/jobs/{job_id}/cancel
           SIGTERM the job's process group.

    GET    /_gapt/api/llm-backends/auth/jobs/{job_id}
           polling fallback — full history snapshot.

    POST   /_gapt/api/llm-backends/api-keys/{provider}
           store an API key (anthropic / openai / google) into the
           SYSTEM-scoped vault under the `<provider>_api_key`
           keyname the executor already reads.

    DELETE /_gapt/api/llm-backends/api-keys/{provider}
           remove the stored key.

    POST   /_gapt/api/llm-backends/cli/claude-code/setup-token
           store `claude setup-token` output into the vault under
           `claude_setup_token` for the CLI subprocess to reuse.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.container import get_db_session
from gapt_server.db import enums
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.llm_backends import (
    AuthJob,
    PROVIDER_LABELS,
    ProviderHealth,
    cancel_job,
    claude_binary_path,
    collect_health,
    get_job,
    spawn_auth_job,
    submit_input,
)
from gapt_server.domains.llm_backends.health import _run_cmd
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.providers import get_vault


router = APIRouter(prefix="/_gapt/api/llm-backends", tags=["llm-backends"])


# ────────────────────────────────────────── response shapes ──


class ProviderHealthDto(BaseModel):
    provider: str
    label: str
    kind: Literal["api", "cli"]
    state: Literal["ok", "missing", "expired", "unreachable", "unknown"]
    detail: str
    env_var: str | None = None
    binary_path: str | None = None
    binary_version: str | None = None
    auth_method: str | None = None
    expires_at_ms: int | None = None

    @classmethod
    def from_domain(cls, h: ProviderHealth) -> ProviderHealthDto:
        return cls(**h.__dict__)


class BackendsHealthResponse(BaseModel):
    providers: list[ProviderHealthDto]


class AuthStatusResponse(BaseModel):
    raw: dict[str, Any]
    logged_in: bool | None = None
    auth_method: str | None = None
    subscription_type: str | None = None
    email: str | None = None


class StartLoginRequest(BaseModel):
    """Body for `POST /cli/claude-code/auth/login`. `use_console=true`
    routes through Anthropic Console (API-billing) instead of the
    Claude.ai subscription flow."""

    use_console: bool = False
    email: str | None = None


class StartLoginResponse(BaseModel):
    job_id: str
    kind: str
    argv: list[str]
    hint: str


class AuthInputRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    append_newline: bool = True


class TestConnectionResponse(BaseModel):
    ok: bool
    duration_ms: int
    detail: str
    raw_stdout_tail: str | None = None
    raw_stderr_tail: str | None = None


class JobSnapshotResponse(BaseModel):
    job_id: str
    kind: str
    argv: list[str]
    started_at: float
    finished_at: float | None
    exit_code: int | None
    history: list[dict[str, Any]]


class SetupTokenRequest(BaseModel):
    token: str = Field(min_length=8, max_length=4096)


class StoreApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


class StoredKeyResponse(BaseModel):
    provider: str
    key_name: str
    stored: bool


# ──────────────────────────────────────────── health routes ──


@router.get("/health", response_model=BackendsHealthResponse)
async def get_backends_health(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> BackendsHealthResponse:
    """Probe every provider in parallel. Single-admin model means
    `actor_id` is always the admin id; we just thread it through."""
    rows = await collect_health(db=db, vault=vault, actor_id=user.id)
    return BackendsHealthResponse(
        providers=[ProviderHealthDto.from_domain(r) for r in rows]
    )


@router.post("/cli/claude-code/recheck", response_model=ProviderHealthDto)
async def recheck_claude_code(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ProviderHealthDto:
    """Re-probe just the Claude Code CLI card — what the UI calls
    after the user finishes `claude auth login` in the modal."""
    rows = await collect_health(db=db, vault=vault, actor_id=user.id)
    for r in rows:
        if r.provider == "claude_code_cli":
            return ProviderHealthDto.from_domain(r)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "llm_backends.missing", "reason": "claude_code_cli row not returned"},
    )


# ────────────────────────────────────── Claude Code auth flow ──


def _require_binary() -> str:
    binary = claude_binary_path()
    if not binary:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "claude_code.binary_missing",
                "reason": (
                    "`claude` binary not on PATH. Install Claude Code "
                    "and ensure it's executable."
                ),
            },
        )
    return binary


@router.get("/cli/claude-code/auth/status", response_model=AuthStatusResponse)
async def claude_code_auth_status(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> AuthStatusResponse:
    """Wraps `claude auth status --json` so the modal knows whether
    the host already has a subscription / console key / nothing.

    Note: the CLI's `loggedIn: true` does NOT mean the OAuth token
    is *fresh*. Cross-check `expires_at_ms` in the health card
    (`collect_health`) when the modal needs the real story."""
    binary = _require_binary()
    rc, out, err = await _run_cmd([binary, "auth", "status", "--json"], timeout=5.0)
    if rc != 0:
        try:
            raw = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            raw = {}
        raw.setdefault("loggedIn", False)
        raw.setdefault("error", err.strip()[:300])
        return AuthStatusResponse(raw=raw, logged_in=False)
    try:
        raw = json.loads(out)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "claude_code.bad_status_payload",
                "reason": f"non-JSON from `claude auth status`: {out[:200]}",
            },
        ) from exc
    return AuthStatusResponse(
        raw=raw,
        logged_in=bool(raw.get("loggedIn")),
        auth_method=raw.get("authMethod"),
        subscription_type=raw.get("subscriptionType"),
        email=raw.get("email"),
    )


@router.post("/cli/claude-code/auth/login", response_model=StartLoginResponse)
async def claude_code_auth_login(
    payload: StartLoginRequest,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StartLoginResponse:
    """Spawn `claude auth login` in the background and hand back a
    `job_id` the modal follows over SSE. `--claudeai` is the
    subscription path (default); `--console` is the API-key path
    (Anthropic Console billing)."""
    binary = _require_binary()
    argv: list[str] = [binary, "auth", "login"]
    if payload.use_console:
        argv.append("--console")
    else:
        argv.append("--claudeai")
    if payload.email:
        argv += ["--email", payload.email]
    try:
        job = await spawn_auth_job(
            kind="claude_code_console_login" if payload.use_console else "claude_code_login",
            argv=argv,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "auth_job.spawn_failed", "reason": str(exc)},
        ) from exc
    return StartLoginResponse(
        job_id=job.job_id,
        kind=job.kind,
        argv=argv,
        hint=(
            "Open the device-code URL the CLI prints, complete the "
            "login, paste the auth code back into the modal."
        ),
    )


@router.post("/cli/claude-code/auth/logout")
async def claude_code_auth_logout(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    binary = _require_binary()
    rc, out, err = await _run_cmd([binary, "auth", "logout"], timeout=10.0)
    return {"ok": rc == 0, "stdout": out, "stderr": err}


@router.post("/cli/claude-code/test", response_model=TestConnectionResponse)
async def claude_code_test(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> TestConnectionResponse:
    """Run `claude --print "ping"` with the right auth posture and
    report whether the CLI got a real response.

    Two correctness gates ported from the Geny version:

    1. `--bare` is **only** safe on the API-key path. Subscription
       users who just completed OAuth will otherwise get "Not
       logged in" even though the credential file is fine. We pick
       per the resolved API key.
    2. `--print --output-format json` returns rc=0 even when the
       envelope carries `is_error: true` (rate limit, auth fail,
       etc.). We parse the envelope so the UI doesn't show "ok"
       on a real error.
    """
    import os  # noqa: PLC0415
    from gapt_server.agent.credentials import _resolve_user_secret  # noqa: PLC0415

    binary = _require_binary()
    vault_key = await _resolve_user_secret(
        db=db,
        vault=vault,
        actor_id=user.id,
        key_name="anthropic_api_key",
        purpose="llm_backends.test",
    )
    have_api_key = bool(vault_key or os.environ.get("ANTHROPIC_API_KEY", "").strip())

    argv = [binary, "--print"]
    if have_api_key:
        argv.append("--bare")
    argv += ["--output-format", "json", "ping"]

    started = time.monotonic()
    rc, out, err = await _run_cmd(argv, timeout=20.0)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    is_error_envelope = False
    envelope_msg = ""
    api_error_status: int | None = None
    if out.strip():
        try:
            envelope = json.loads(out)
            if isinstance(envelope, dict) and envelope.get("is_error"):
                is_error_envelope = True
                envelope_msg = str(
                    envelope.get("result") or envelope.get("error") or ""
                ).strip()
                api_status_raw = envelope.get("api_error_status")
                if api_status_raw is not None:
                    try:
                        api_error_status = int(api_status_raw)
                    except (TypeError, ValueError):
                        api_error_status = None
        except json.JSONDecodeError:
            pass

    ok = rc == 0 and bool(out.strip()) and not is_error_envelope
    if ok:
        detail = "Response received."
    elif is_error_envelope:
        if api_error_status == 401 or "Not logged in" in envelope_msg:
            detail = (
                "Authentication failed. Re-run `claude auth login` "
                f"via the modal. (CLI: {envelope_msg or 'auth failed'})"
            )
        else:
            detail = f"CLI error: {envelope_msg or 'unknown'}" + (
                f" (HTTP {api_error_status})" if api_error_status else ""
            )
    else:
        detail = f"exit code {rc}"
    return TestConnectionResponse(
        ok=ok,
        duration_ms=elapsed_ms,
        detail=detail,
        raw_stdout_tail=out[-400:] if out else None,
        raw_stderr_tail=err[-400:] if err else None,
    )


# ─────────────────────────────── auth-job SSE / input / cancel ──


def _job_or_404(job_id: str) -> AuthJob:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "auth_job.not_found", "reason": f"no job {job_id!r}"},
        )
    return job


@router.get("/auth/jobs/{job_id}/events")
async def auth_job_events(
    job_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """SSE stream of the auth subprocess's stdout/stderr/exit
    events. Replays history first so a late-connecting client still
    sees the device-code URL.

    Each event payload: `{channel, text, ts, exit_code?}`.
    `channel` is `stdout` / `stderr` / `stdin` / `exit`.

    Heartbeats every 30s so reverse-proxies don't close idle
    connections during the human-visible URL → auth-code wait.
    """
    job = _job_or_404(job_id)

    async def gen() -> AsyncIterator[bytes]:
        for entry in list(job.history):
            yield f"data: {json.dumps(entry)}\n\n".encode("utf-8")
        while True:
            try:
                entry = await asyncio.wait_for(job.lines.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield b": heartbeat\n\n"
                if job.is_finished:
                    return
                continue
            if entry is None:
                return
            yield f"data: {json.dumps(entry)}\n\n".encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/auth/jobs/{job_id}/input")
async def auth_job_input(
    job_id: str,
    payload: AuthInputRequest,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    try:
        await submit_input(job, payload.text, append_newline=payload.append_newline)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "auth_job.stdin_unavailable", "reason": str(exc)},
        ) from exc
    return {"ok": True}


@router.post("/auth/jobs/{job_id}/cancel")
async def auth_job_cancel(
    job_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    killed = cancel_job(job)
    return {"ok": True, "killed": killed, "already_finished": job.is_finished}


@router.get("/auth/jobs/{job_id}", response_model=JobSnapshotResponse)
async def auth_job_snapshot(
    job_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> JobSnapshotResponse:
    """Polling fallback for clients that can't use SSE — returns the
    full history snapshot + exit_code if known."""
    job = _job_or_404(job_id)
    return JobSnapshotResponse(
        job_id=job.job_id,
        kind=job.kind,
        argv=job.argv,
        started_at=job.started_at,
        finished_at=job.finished_at,
        exit_code=job.exit_code,
        history=list(job.history),
    )


# ───────────────────────────────────── credential persistence ──


_PROVIDER_TO_VAULT_KEY: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
}


async def _vault_set_admin_secret(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    key_name: str,
    value: str,
) -> None:
    """Upsert an admin-scoped secret by key_name. The vault's
    `store()` rejects duplicates with 409, so we rotate when the key
    already exists. Keeps the API endpoints idempotent — the modal
    submits the same key twice and gets a clean update either way.
    """
    try:
        items = await vault.list(
            db, scope=enums.SecretOwnerScope.SYSTEM, owner_id=actor_id
        )
    except SecretVaultError:
        items = []
    existing = next((md for md in items if md.key_name == key_name), None)
    if existing is None:
        await vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id=actor_id,
            key_name=key_name,
            value=value,
        )
    else:
        await vault.rotate(db, secret_id=existing.id, new_value=value)
    await db.commit()


async def _vault_delete_admin_secret(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    key_name: str,
) -> bool:
    """Drop an admin-scoped secret by key_name. Returns True when a
    row was deleted, False when nothing matched (idempotent — the
    DELETE endpoint reports "not stored" not 404)."""
    try:
        items = await vault.list(
            db, scope=enums.SecretOwnerScope.SYSTEM, owner_id=actor_id
        )
    except SecretVaultError:
        return False
    existing = next((md for md in items if md.key_name == key_name), None)
    if existing is None:
        return False
    await vault.delete(db, secret_id=existing.id)
    await db.commit()
    return True


@router.post("/api-keys/{provider}", response_model=StoredKeyResponse)
async def store_provider_api_key(
    provider: str,
    payload: StoreApiKeyRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StoredKeyResponse:
    """Save the operator-pasted API key into the admin-scoped vault
    under the `<provider>_api_key` keyname the executor already
    reads via `_USER_SECRET_KEYS`. Idempotent — rotates an existing
    entry."""
    vault_key = _PROVIDER_TO_VAULT_KEY.get(provider)
    if vault_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "llm_backends.unsupported_provider",
                "reason": (
                    f"provider {provider!r} doesn't have an API-key slot. "
                    f"Known: {sorted(_PROVIDER_TO_VAULT_KEY)}"
                ),
            },
        )
    await _vault_set_admin_secret(
        db=db,
        vault=vault,
        actor_id=user.id,
        key_name=vault_key,
        value=payload.api_key.strip(),
    )
    return StoredKeyResponse(provider=provider, key_name=vault_key, stored=True)


@router.delete("/api-keys/{provider}", response_model=StoredKeyResponse)
async def delete_provider_api_key(
    provider: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StoredKeyResponse:
    vault_key = _PROVIDER_TO_VAULT_KEY.get(provider)
    if vault_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "llm_backends.unsupported_provider",
                "reason": f"provider {provider!r} unknown",
            },
        )
    deleted = await _vault_delete_admin_secret(
        db=db, vault=vault, actor_id=user.id, key_name=vault_key
    )
    return StoredKeyResponse(provider=provider, key_name=vault_key, stored=not deleted)


@router.post("/cli/claude-code/setup-token", response_model=StoredKeyResponse)
async def store_claude_setup_token(
    payload: SetupTokenRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StoredKeyResponse:
    """Persist a long-lived token produced by `claude setup-token`
    so the executor can re-export it as `ANTHROPIC_API_KEY` on every
    workspace boot.

    Storage uses the *same* vault keyname as the regular Anthropic
    API key (`anthropic_api_key`) — the executor's credential
    bundle doesn't distinguish setup-tokens from console keys at
    the env-var level, and treating them as one keyname keeps the
    health card honest (a setup-token paste flips the card to "ok"
    immediately).
    """
    await _vault_set_admin_secret(
        db=db,
        vault=vault,
        actor_id=user.id,
        key_name="anthropic_api_key",
        value=payload.token.strip(),
    )
    return StoredKeyResponse(
        provider="claude_code_cli",
        key_name="anthropic_api_key",
        stored=True,
    )
