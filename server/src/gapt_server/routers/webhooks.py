"""GitHub-style push webhooks → auto-deploy.

Two endpoints:

  `POST /_gapt/api/projects/{pid}/webhooks/secret`
    Mint a fresh HMAC secret. Returns it once + replaces any
    previously-minted secret on the project row. The user pastes the
    response into GitHub's webhook settings under "Secret".

  `POST /_gapt/api/projects/{pid}/webhooks/github`
    Receive a push event. Verifies `X-Hub-Signature-256` against the
    project's stored `webhook_secret`, parses the push, finds every
    Environment whose `deploy_target_config.trigger.branch` matches
    the pushed ref, and triggers a deploy for each in the background
    (returns 202 + the list of triggered env_ids).

The push trigger is opt-in per environment — without
`trigger.branch` configured the env never auto-deploys, so adding a
webhook URL alone doesn't accidentally fire anything.

We intentionally don't shell out to git pull on receipt. The
existing workspace clone is on whatever branch the user chose at
workspace-create time; the deploy reads it as-is. If the user wants
a different branch they reconfigure the workspace + env. (A "auto-
update workspace on push" feature is a future cycle.)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_app_settings,
    get_audit_sink,
    get_container,
    get_db_session,
    get_notifications,
    get_policy_engine,
)
from gapt_server.db import models
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.deploy import (
    AcceptAnyCodeVerifier,
    DeployOrchestrator,
    DeployStatusKind,
    DeployTargetError,
    OrchestratorError,
)
from gapt_server.domains.notifications import (
    NotificationKind,
    NotificationService,
)
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.policy.engine import PolicyEngine  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.deploy import _build_target, _record_deploy_run
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.container import AppContainer
    from gapt_server.settings import Settings


router = APIRouter(prefix="/_gapt/api/projects", tags=["webhooks"])


# ──────────────────────────────────────────────── mint secret ──


class MintSecretResponse(BaseModel):
    """Returned ONCE — we never echo the stored secret again. The
    user has to mint a new one if they lose it."""

    secret: str
    webhook_url_hint: str = Field(
        description="Path the user should configure in GitHub — "
        "prepend it with the project's apex domain."
    )


@router.post(
    "/{project_id}/webhooks/secret",
    response_model=MintSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mint_webhook_secret(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> MintSecretResponse:
    """Generate + store a fresh HMAC secret for this project's
    webhook endpoint."""
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    project = (
        await db.execute(select(models.Project).where(models.Project.id == project_id))
    ).scalar_one()

    # 32 bytes → 64-char hex. Same shape GitHub recommends. Replace
    # any prior secret atomically; no rotation/grace window because
    # webhooks are easy to re-configure.
    secret_hex = secrets.token_hex(32)
    project.webhook_secret = secret_hex
    await db.flush()
    await db.commit()
    return MintSecretResponse(
        secret=secret_hex,
        webhook_url_hint=f"/_gapt/api/projects/{project_id}/webhooks/github",
    )


# ──────────────────────────────────────────── push receiver ──


class GithubWebhookResponse(BaseModel):
    triggered: list[str]
    skipped: list[str]
    actions: list[str]


def _verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time HMAC verify against GitHub's
    `X-Hub-Signature-256: sha256=<hex>` header. Returns False on
    missing / malformed / mismatched signature — caller turns that
    into 401.

    GitHub uses sha256-HMAC of the raw request body. The `sha256=`
    prefix is required by GitHub but easy to forget; we accept both
    `sha256=<hex>` and bare `<hex>` shapes for resilience.
    """
    if not header:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = header[7:] if header.startswith("sha256=") else header
    return hmac.compare_digest(expected, received.strip())


def _ref_branch(ref: str | None) -> str | None:
    """Extract a branch from `refs/heads/<branch>`. Tag pushes
    (`refs/tags/...`) return None — we don't auto-deploy on tags
    by default."""
    if not isinstance(ref, str):
        return None
    if not ref.startswith("refs/heads/"):
        return None
    return ref[len("refs/heads/") :]


def _matches_trigger(env_cfg: dict[str, Any], branch: str) -> bool:
    """Per-env trigger policy. The env author sets
    `deploy_target_config.trigger = {"branch": "main"}` (or
    "production", or whatever they consider the prod ref). Anything
    else → no auto-deploy."""
    trigger = env_cfg.get("trigger") if isinstance(env_cfg, dict) else None
    if not isinstance(trigger, dict):
        return False
    wanted = trigger.get("branch")
    return isinstance(wanted, str) and wanted == branch


@router.post(
    "/{project_id}/webhooks/github",
    response_model=GithubWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def github_push_webhook(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GithubWebhookResponse:
    """Receive a GitHub push event. NO user auth — verified by
    HMAC signature instead. We never trust the request body until
    the signature checks out.

    Behaviour:
      1. Project must have a `webhook_secret` set (412 otherwise).
      2. `X-Hub-Signature-256` must validate (401).
      3. Event must be a push to a branch (200 with skip note for
         tags / ping / other event kinds).
      4. For every environment with `trigger.branch == <pushed>`,
         enqueue a deploy as a background task. Return immediately
         with 202.
    """
    project = (
        await db.execute(select(models.Project).where(models.Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "project.not_found", "reason": project_id},
        )
    if not project.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "webhook.secret_not_minted",
                "reason": "no webhook secret on this project — mint one first",
            },
        )

    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(project.webhook_secret, body, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "webhook.signature_invalid", "reason": ""},
        )

    event = request.headers.get("X-GitHub-Event", "").lower()
    if event == "ping":
        return GithubWebhookResponse(
            triggered=[], skipped=[], actions=["ping ok"]
        )
    if event != "push":
        return GithubWebhookResponse(
            triggered=[], skipped=[], actions=[f"event {event!r} ignored"]
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "webhook.bad_json", "reason": ""},
        )
    branch = _ref_branch(payload.get("ref"))
    if branch is None:
        return GithubWebhookResponse(
            triggered=[],
            skipped=[],
            actions=[f"ref {payload.get('ref')!r} not a branch — ignored"],
        )

    envs = (
        await db.execute(
            select(models.Environment).where(
                models.Environment.project_id == project_id
            )
        )
    ).scalars().all()

    triggered: list[str] = []
    skipped: list[str] = []
    actions: list[str] = []
    for env in envs:
        cfg = env.deploy_target_config or {}
        if not _matches_trigger(cfg, branch):
            skipped.append(env.id)
            continue
        # Snapshot what we need for the background task — the
        # request session closes when this handler returns so the
        # task opens its own.
        actions.append(f"queued deploy for env {env.name!r} (branch={branch})")
        asyncio.create_task(
            _run_webhook_deploy(
                container=container,
                env_id=env.id,
                env_name=env.name,
                project_id=project_id,
                version=payload.get("after", "")[:40] or branch,
                branch=branch,
                policy_engine=policy_engine,
                audit_sink=audit_sink,
                notifications=notifications,
                settings=settings,
            )
        )
        triggered.append(env.id)

    return GithubWebhookResponse(
        triggered=triggered, skipped=skipped, actions=actions
    )


async def _run_webhook_deploy(
    *,
    container: AppContainer,
    env_id: str,
    env_name: str,
    project_id: str,
    version: str,
    branch: str,
    policy_engine: PolicyEngine,
    audit_sink: AuditSink,
    notifications: NotificationService,
    settings: Settings,
) -> None:
    """Background deploy worker. Opens a fresh DB session, runs the
    orchestrator, records a `DeployRun` row, fires a notification.
    Never raises — failures are caught + recorded as a failed run."""
    if container.session_factory is None:
        return
    async with container.session_factory() as db:
        env_row = (
            await db.execute(
                select(models.Environment).where(models.Environment.id == env_id)
            )
        ).scalar_one_or_none()
        if env_row is None:
            return
        target = _build_target(env_row.deploy_target_kind, settings)
        orchestrator = DeployOrchestrator(
            policy_engine=policy_engine,
            target=target,
            audit_sink=audit_sink,
            two_factor=AcceptAnyCodeVerifier(),
        )
        cfg = env_row.deploy_target_config or {}
        compose_path = cfg.get("compose_path", "docker-compose.yml")
        compose_paths = [p for p in (cfg.get("compose_paths") or []) if isinstance(p, str)]
        target_options = {**(env_row.deploy_target_config or {})}
        status_value = "failed"
        bound_url: str | None = None
        exec_code: str | None = None
        log_tail = ""
        try:
            result = await orchestrator.deploy(
                actor_id="webhook",  # literal actor for push-triggered deploys
                project_id=project_id,
                environment_id=env_id,
                environment_name=env_name,
                version=version,
                compose_path=compose_path,
                compose_paths=compose_paths,
                secret_refs=list(env_row.secret_refs or []),
                target_options=target_options,
            )
            status_value = result.status.value
            bound_url = result.bound_url
            exec_code = result.exec_code
            log_tail = result.log
        except (OrchestratorError, DeployTargetError) as exc:
            exec_code = exc.code
            log_tail = str(exc)[:2000]
        except Exception as exc:
            exec_code = "webhook.deploy.crashed"
            log_tail = f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            await _record_deploy_run(
                db,
                env_row=env_row,
                version=version,
                status_value=status_value,
                bound_url=bound_url,
                exec_code=exec_code,
                log_tail=log_tail,
                actor_id=None,  # system-triggered
                trigger_kind=f"webhook:{branch}",
            )
            await db.commit()
            await notifications.emit(
                kind=NotificationKind.DEPLOY_SUCCESS
                if status_value == "success"
                else NotificationKind.DEPLOY_FAILED,
                title=f"Deploy {status_value}: {env_name} (push {branch})",
                body=f"Project={project_id} version={version}",
                actor_id=None,
                project_id=project_id,
                severity="info" if status_value == "success" else "error",
                details={"env_id": env_id, "trigger": f"webhook:{branch}"},
            )


# Keep these names referenced — pydantic + structlog read them at
# runtime and a stricter ruff selector might otherwise prune the
# imports as unused.
_USED: tuple[type, type, type] = (datetime, DeployStatusKind, UTC.__class__)
