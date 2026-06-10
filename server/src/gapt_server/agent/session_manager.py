"""`ProjectAwareSessionManager` — orchestrates an AgentSession boot.

Boot order (Cycle 2.8a):

1. Validate the user has access to the workspace + project.
2. Build the `CredentialBundle` (Cycle 2.2) reading any SDK provider
   secrets out of the vault.
3. Instantiate the geny-executor `Pipeline` from the manifest (Cycle
   2.1) with the credential bundle attached.
4. Insert an `agent_sessions` row (status=ACTIVE) and emit a
   `session.create` audit event.
5. Return an `AgentSessionHandle` carrying the pipeline + DB row id.

What this PR *does not* do yet:

- SSE streaming wire-up (Cycle 2.10).
- Cost / token roll-up (Cycle 2.9).
- Freshness policy (paused → archive) on the ARQ worker (Cycle 2.8b).
- MCP bridge spawn config — that's filled in once
  ``mcp_config={"mcpServers": {"gapt": {...}}}`` flows through
  ``CredentialBundle.extras``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from geny_executor import CredentialBundle
from sqlalchemy import select

from gapt_server.agent.credentials import (
    SecretRefMap,
    build_claude_code_cli_creds,
    build_for_session,
    claude_binary,
)
from gapt_server.agent.environment_service import (
    GaptEnvironmentService,
    ManifestOverrides,
)
from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditEvent, AuditSink, NullAuditSink
from gapt_server.domains.projects.service import fetch_project_for

if TYPE_CHECKING:
    from pathlib import Path

    from geny_executor import Pipeline, ProviderCredentials
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.domains.auth import AdminPrincipal
    from gapt_server.domains.secrets.vault import SecretVault

logger = structlog.get_logger(__name__)


class SessionManagerError(RuntimeError):
    """Stable code suffix surfaces to the router as HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AgentSessionHandle:
    """What ``create_session`` hands back to callers (router + tests)."""

    session_id: str
    project_id: str
    workspace_id: str
    user_id: str
    env_manifest_id: str
    pipeline: Pipeline
    worktree_path: str = ""
    status: enums.AgentSessionStatus = enums.AgentSessionStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    # Phase N.3 — per-session USD cap enforced by GAPT's invoke handler.
    # `None` means no cap (free mode). Carried through to the runtime
    # so a rehydrate after server restart preserves the policy.
    cost_budget_usd: float | None = None
    # Phase N.3 — snapshot of the persisted token / cost counters at
    # handle-construction time. Zero for freshly created sessions,
    # populated from the DB row when rehydrating. The router uses
    # these to seed the in-memory `CostAccumulator` so the budget
    # gate sees the FULL cumulative spend across all server lives
    # (pre-fix, every rehydrate reset the accumulator to 0 and the
    # next cost_callback would clobber the DB's totals back down).
    initial_cost_usd: float = 0.0
    initial_input_tokens: int = 0
    initial_output_tokens: int = 0
    initial_cache_write_tokens: int = 0
    initial_cache_read_tokens: int = 0
    # geny-executor 2.2.0 — the `claude_code_cli` ProviderCredentials
    # from the session's CredentialBundle. The session bootstrap
    # (`routers/sessions._build_runtime_from_handle` / oneshot) uses
    # them to construct a `ClaudeCodeCLIClient(runner_factory=...)`
    # when a workspace sandbox is bound, because `runner_factory` (a
    # callable) cannot ride through `ProviderCredentials.extras` —
    # the executor's `_creds_to_client_kwargs` doesn't map it. The
    # plaintext exposure is no wider than before: the pipeline's own
    # resolved client holds the same key for the session's lifetime.
    cli_credentials: ProviderCredentials | None = None


@dataclass
class ProjectAwareSessionManager:
    """Stateless orchestrator. The DB row is the source of truth — we
    don't keep an in-memory session table here. The router/SSE layer
    (Cycle 2.10) is what caches live pipelines per session_id.
    """

    env_service: GaptEnvironmentService
    audit_sink: AuditSink = field(default_factory=NullAuditSink)
    default_manifest_id: str = "gapt_default"

    # ──────────────────────────────────────────────── create ──

    async def create_session(
        self,
        db: AsyncSession,
        *,
        user: AdminPrincipal,
        workspace_id: str,
        env_id: str | None = None,
        secret_refs: SecretRefMap | None = None,
        workspace_root_override: Path | None = None,
        vault: SecretVault | None = None,
        mcp_config: dict[str, Any] | None = None,
        settings_path: str | None = None,
        session_overrides: ManifestOverrides | None = None,
    ) -> AgentSessionHandle:
        # 1) Locate the workspace + verify the project exists.
        ws = await self._fetch_workspace(db, workspace_id)
        await fetch_project_for(db, actor=user, project_id=ws.project_id)

        # 2) Build credentials. If the caller didn't supply a vault we
        #    still bootstrap claude_code_cli (host OAuth path); SDK
        #    provider keys are skipped.
        prefs = await _load_admin_prefs(db)
        permission_mode = (
            prefs.permission_mode if prefs and prefs.permission_mode else "bypassPermissions"
        )
        if vault is None:
            bundle = CredentialBundle(
                by_provider={
                    "claude_code_cli": build_claude_code_cli_creds(
                        binary_path=claude_binary(),
                        workspace_root=str(workspace_root_override)
                        if workspace_root_override
                        else ws.worktree_path,
                        mcp_config=mcp_config,
                        settings_path=settings_path,
                        default_permission_mode=permission_mode,
                    )
                }
            )
        else:
            bundle = await build_for_session(
                db=db,
                vault=vault,
                actor_id=user.id,
                secret_refs=secret_refs or SecretRefMap(),
                workspace_root=(
                    str(workspace_root_override) if workspace_root_override else ws.worktree_path
                ),
                mcp_config=mcp_config,
                settings_path=settings_path,
                permission_mode=permission_mode,
            )

        # 3) Instantiate the pipeline against the chosen manifest.
        #
        # Resolution order (Phase G.5):
        #   1. Request `env_id` (ChatPanel manifest picker)
        #   2. `admin_agent_prefs.default_manifest_id` (Settings)
        #   3. `Settings.default_manifest_id` (env var / hard-coded)
        admin_default = prefs.default_manifest_id if prefs else None
        env_manifest_id = env_id or admin_default or self.default_manifest_id
        # Phase G.4 — per-session overrides win over global admin
        # prefs. Caller passes `session_overrides` when the chat
        # panel's model/max_tokens pill is set; missing fields fall
        # through to the prefs, which fall through to the manifest's
        # bundled defaults. This is a one-shot ratchet — the override
        # only affects this session, not future creates.
        overrides = _merge_overrides(_prefs_to_overrides(prefs), session_overrides)
        try:
            pipeline = await self.env_service.instantiate_pipeline(
                env_manifest_id, credentials=bundle, overrides=overrides
            )
        except Exception as exc:
            raise SessionManagerError(
                "session.pipeline_boot_failed",
                f"pipeline boot for env {env_manifest_id!r} failed: {exc!s}",
            ) from exc

        # Phase N.3 — derive the effective budget cap GAPT will enforce.
        # Session override wins; otherwise inherit the admin pref.
        # `None` at both levels = no cap (free mode).
        effective_budget: float | None = None
        if session_overrides is not None and session_overrides.cost_budget_usd is not None:
            effective_budget = float(session_overrides.cost_budget_usd)
        elif prefs is not None and prefs.cost_budget_usd is not None:
            effective_budget = float(prefs.cost_budget_usd)

        # 4) Persist + audit.
        session_id = new_ulid()
        row = models.AgentSession(
            id=session_id,
            project_id=ws.project_id,
            workspace_id=ws.id,
            env_manifest_id=env_manifest_id,
            status=enums.AgentSessionStatus.ACTIVE,
            cost_budget_usd=effective_budget,
        )
        db.add(row)
        await db.flush()
        await self.audit_sink.log(
            AuditEvent(
                action="session.create",
                actor_type=enums.AuditActorType.USER,
                actor_id=user.id,
                outcome=enums.AuditOutcome.OK,
                scope={
                    "project_id": ws.project_id,
                    "workspace_id": ws.id,
                    "session_id": session_id,
                },
                subject={
                    "env_manifest_id": env_manifest_id,
                    "cost_budget_usd": effective_budget,
                },
            )
        )
        logger.info(
            "session.create",
            session_id=session_id,
            project_id=ws.project_id,
            workspace_id=ws.id,
            env_manifest_id=env_manifest_id,
            cost_budget_usd=effective_budget,
        )
        return AgentSessionHandle(
            session_id=session_id,
            project_id=ws.project_id,
            workspace_id=ws.id,
            user_id=user.id,
            env_manifest_id=env_manifest_id,
            pipeline=pipeline,
            worktree_path=ws.worktree_path,
            status=enums.AgentSessionStatus.ACTIVE,
            cost_budget_usd=effective_budget,
            cli_credentials=bundle.by_provider.get("claude_code_cli"),
        )

    # ───────────────────────────────────────────── rehydrate ──

    async def rehydrate_session(
        self,
        db: AsyncSession,
        *,
        user: AdminPrincipal,
        session_id: str,
        vault: SecretVault | None = None,
        mcp_config: dict[str, Any] | None = None,
        settings_path: str | None = None,
    ) -> AgentSessionHandle:
        """Rebuild an in-memory `AgentSessionHandle` from the DB row.

        The `SessionRegistry` is in-process, so every server restart
        empties it. The DB row outlives the process; without this
        method an active session looks gone to the router and the
        chat panel surfaces `session.not_found` after every restart.

        Rehydrate uses the *current* user's vault + the workspace
        that the session was originally bound to, and rebuilds the
        Pipeline from the same manifest. Status is unchanged."""
        row = await self._fetch_session(db, session_id)
        await fetch_project_for(db, actor=user, project_id=row.project_id)
        if row.status == enums.AgentSessionStatus.ARCHIVED:
            raise SessionManagerError(
                "session.archived",
                f"session_id={session_id} is archived; cannot rehydrate",
            )
        ws = await self._fetch_workspace(db, row.workspace_id)

        prefs = await _load_admin_prefs(db)
        permission_mode = (
            prefs.permission_mode if prefs and prefs.permission_mode else "bypassPermissions"
        )
        if vault is None:
            bundle = CredentialBundle(
                by_provider={
                    "claude_code_cli": build_claude_code_cli_creds(
                        binary_path=claude_binary(),
                        workspace_root=ws.worktree_path,
                        mcp_config=mcp_config,
                        settings_path=settings_path,
                        default_permission_mode=permission_mode,
                    )
                }
            )
        else:
            bundle = await build_for_session(
                db=db,
                vault=vault,
                actor_id=user.id,
                workspace_root=ws.worktree_path,
                mcp_config=mcp_config,
                settings_path=settings_path,
                permission_mode=permission_mode,
            )

        overrides = _prefs_to_overrides(prefs)
        try:
            pipeline = await self.env_service.instantiate_pipeline(
                row.env_manifest_id, credentials=bundle, overrides=overrides
            )
        except Exception as exc:
            raise SessionManagerError(
                "session.pipeline_boot_failed",
                f"pipeline rehydrate for env {row.env_manifest_id!r} failed: {exc!s}",
            ) from exc

        logger.info(
            "session.rehydrate",
            session_id=session_id,
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            env_manifest_id=row.env_manifest_id,
        )
        return AgentSessionHandle(
            session_id=session_id,
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            user_id=user.id,
            env_manifest_id=row.env_manifest_id,
            pipeline=pipeline,
            worktree_path=ws.worktree_path,
            status=row.status,
            cost_budget_usd=(
                float(row.cost_budget_usd) if row.cost_budget_usd is not None else None
            ),
            initial_cost_usd=float(row.cost_usd or 0.0),
            initial_input_tokens=int(row.input_tokens or 0),
            initial_output_tokens=int(row.output_tokens or 0),
            initial_cache_write_tokens=int(row.cache_write_tokens or 0),
            initial_cache_read_tokens=int(row.cache_read_tokens or 0),
            cli_credentials=bundle.by_provider.get("claude_code_cli"),
        )

    # ───────────────────────────────────────────── archive ──

    async def archive(
        self,
        db: AsyncSession,
        *,
        user: AdminPrincipal,
        session_id: str,
    ) -> None:
        row = await self._fetch_session(db, session_id)
        await fetch_project_for(db, actor=user, project_id=row.project_id)
        row.status = enums.AgentSessionStatus.ARCHIVED
        await db.flush()
        await self.audit_sink.log(
            AuditEvent(
                action="session.archive",
                actor_type=enums.AuditActorType.USER,
                actor_id=user.id,
                outcome=enums.AuditOutcome.OK,
                scope={
                    "project_id": row.project_id,
                    "workspace_id": row.workspace_id,
                    "session_id": session_id,
                },
            )
        )

    # ─────────────────────────────────────────── reactivate ──

    async def reactivate(
        self,
        db: AsyncSession,
        *,
        user: AdminPrincipal,
        session_id: str,
    ) -> models.AgentSession:
        """Phase L.2 — flip an archived session back to `active` so
        the chat panel can attach to it again. Idempotent: an already-
        active session just bumps `last_active_at` so the picker can
        re-order it to the top of the list.

        The conversation memory itself (Phase L.1's PipelineState
        rebuilt from session_events on rehydrate) is what makes
        reactivation meaningful — without that, this would just
        unhide a row that the agent had no recollection of.
        """
        from datetime import UTC, datetime  # noqa: PLC0415

        row = await self._fetch_session(db, session_id)
        await fetch_project_for(db, actor=user, project_id=row.project_id)
        was_archived = row.status == enums.AgentSessionStatus.ARCHIVED
        row.status = enums.AgentSessionStatus.ACTIVE
        row.last_active_at = datetime.now(UTC)
        await db.flush()
        if was_archived:
            await self.audit_sink.log(
                AuditEvent(
                    action="session.reactivate",
                    actor_type=enums.AuditActorType.USER,
                    actor_id=user.id,
                    outcome=enums.AuditOutcome.OK,
                    scope={
                        "project_id": row.project_id,
                        "workspace_id": row.workspace_id,
                        "session_id": session_id,
                    },
                )
            )
        return row

    # ─────────────────────────────────────────────── helpers ──

    @staticmethod
    async def _fetch_workspace(db: AsyncSession, workspace_id: str) -> models.Workspace:
        row = (
            await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if row is None:
            raise SessionManagerError(
                "workspace.not_found",
                f"workspace_id={workspace_id} does not exist",
            )
        return row

    @staticmethod
    async def _fetch_session(db: AsyncSession, session_id: str) -> models.AgentSession:
        row = (
            await db.execute(
                select(models.AgentSession).where(models.AgentSession.id == session_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise SessionManagerError(
                "session.not_found",
                f"session_id={session_id} does not exist",
            )
        return row

async def _load_admin_prefs(
    db: AsyncSession,
) -> models.AdminAgentPrefs | None:
    """Fetch the singleton admin agent prefs row (or None when unset).
    Used twice per session boot — once to feed `permission_mode`
    into `build_for_session` (creds layer), once to derive
    `ManifestOverrides` for the env_service (manifest layer)."""
    return (
        await db.execute(
            select(models.AdminAgentPrefs).where(models.AdminAgentPrefs.id == "admin")
        )
    ).scalar_one_or_none()


def _prefs_to_overrides(
    prefs: models.AdminAgentPrefs | None,
) -> ManifestOverrides | None:
    """Project the prefs row onto the subset of fields that patch the
    manifest. `permission_mode` is *not* part of the manifest — it's
    a credentials concern handled by `build_for_session`."""
    if prefs is None:
        return None
    return ManifestOverrides(
        model=prefs.model,
        max_tokens=prefs.max_tokens,
        max_iterations=prefs.max_iterations,
        cost_budget_usd=float(prefs.cost_budget_usd) if prefs.cost_budget_usd is not None else None,
        timeout_s=prefs.timeout_s,
    )


def _merge_overrides(
    base: ManifestOverrides | None,
    patch: ManifestOverrides | None,
) -> ManifestOverrides | None:
    """Phase G.4 — combine global prefs (`base`) with per-session
    request fields (`patch`). Non-`None` patch fields win; missing
    fields fall back to `base`. Returns `None` when both inputs are
    fully empty — same shape `_prefs_to_overrides(None)` returns so
    the env_service code path is unchanged."""
    if base is None and patch is None:
        return None
    if base is None:
        return patch
    if patch is None:
        return base
    return ManifestOverrides(
        model=patch.model if patch.model is not None else base.model,
        max_tokens=patch.max_tokens if patch.max_tokens is not None else base.max_tokens,
        max_iterations=(
            patch.max_iterations if patch.max_iterations is not None else base.max_iterations
        ),
        cost_budget_usd=(
            patch.cost_budget_usd
            if patch.cost_budget_usd is not None
            else base.cost_budget_usd
        ),
        timeout_s=patch.timeout_s if patch.timeout_s is not None else base.timeout_s,
        # Phase L.4 — thinking knobs follow the same patch-wins rule.
        thinking_enabled=(
            patch.thinking_enabled
            if patch.thinking_enabled is not None
            else base.thinking_enabled
        ),
        thinking_budget_tokens=(
            patch.thinking_budget_tokens
            if patch.thinking_budget_tokens is not None
            else base.thinking_budget_tokens
        ),
    )


# Kept for back-compat with any external callers.
async def _load_overrides(
    db: AsyncSession,
) -> ManifestOverrides | None:
    return _prefs_to_overrides(await _load_admin_prefs(db))
