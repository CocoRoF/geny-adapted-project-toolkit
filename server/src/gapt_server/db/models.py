"""SQLAlchemy ORM models — control-plane data model.

GAPT is a single-admin self-hosted tool. There is no User / Org /
Membership table — auth is `settings.admin_id` + cookie, see
`domains/auth/principal.py`. Anything that used to FK into `users`
or `orgs` is either gone or carries a plain-text actor id (audit
rows, deploy-run trigger source) so the audit log can still answer
"who triggered this" without a table that has no rows besides admin.

All primary keys are 26-character ULID strings. All timestamps are
timezone-aware (`timestamptz`). Enums are native Postgres types — see
`gapt_server.db.enums`.
"""

from __future__ import annotations

# `datetime` is read at runtime by SQLAlchemy 2's `mapped_column` via
# `typing.get_type_hints`, so it cannot move into a TYPE_CHECKING block
# (per-file-ignore would also work but inline noqa survives stricter
# selectors).
from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gapt_server.db.base import Base
from gapt_server.db.enums import (
    AgentSessionStatus,
    AuditActorType,
    AuditOutcome,
    DeployTargetKind,
    GitProvider,
    SandboxStatus,
    SecretBackend,
    SecretOwnerScope,
    WorkspaceStatus,
)
from gapt_server.db.ulid import ulid_default

ULID_LEN = 26


def _pk() -> Mapped[str]:
    return mapped_column(String(ULID_LEN), primary_key=True, default=ulid_default)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _pg_enum(py_enum: type, name: str) -> Enum:
    """Build a SQLAlchemy ``Enum`` mapped to a Postgres native enum.

    StrEnum subclasses send their *value* (snake_case) on the wire — not
    the Python *name* — which is what our migration creates the enum
    type with.
    """
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
    )


# ─────────────────────────────────────────────────────────── projects ──


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = _pk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    git_remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    git_provider: Mapped[GitProvider] = mapped_column(
        _pg_enum(GitProvider, "git_provider_enum"), nullable=False
    )
    git_auth_secret_ref: Mapped[str | None] = mapped_column(Text)
    default_compose_paths: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    compose_profile_dev: Mapped[str | None] = mapped_column(String(80))
    compose_profile_prod: Mapped[str | None] = mapped_column(String(80))
    # HMAC secret for inbound GitHub-style push webhooks. None until
    # the user mints one via `POST /webhooks/secret`. We never echo it
    # back after creation — the user copies the response once and
    # pastes into GitHub's webhook config.
    webhook_secret: Mapped[str | None] = mapped_column(String(64))
    # Phase N.2.5 — records which scaffold preset created this project
    # row (`fullstack_fastapi_nextjs`, `empty`, etc.). NULL for projects
    # created via the "import" flow. Audit-only; nothing in the request
    # path reads it back.
    scaffold_preset_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ─────────────────────────────────────────────────────── environments ──


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    deploy_target_kind: Mapped[DeployTargetKind] = mapped_column(
        _pg_enum(DeployTargetKind, "deploy_target_kind_enum"), nullable=False
    )
    deploy_target_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    require_2fa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    secret_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    cost_multiplier: Mapped[float] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="1"
    )
    hooks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Last deploy summary — kept here so the UI can show "last deployed
    # X, current URL Y" without joining a runs table (none exists yet).
    # Schema: {run_id, status, bound_url, deployed_at, version}.
    last_run: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_environments_project_name"),)


# ─────────────────────────────────────────────────────── deploy_runs ──


class DeployRun(Base):
    """Per-deploy audit-grade row. Recorded for every trigger
    (manual / webhook / rollback). Replaces the old `Environment.
    last_run` JSONB-only model — we keep `last_run` as a denormalised
    "most recent success" cache but the source of truth lives here so
    the UI can show history + offer rollback to N previous versions."""

    __tablename__ = "deploy_runs"

    id: Mapped[str] = _pk()
    environment_id: Mapped[str] = mapped_column(
        String(ULID_LEN),
        ForeignKey("environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Snapshot of the deploy intent. `version` is the
    # `DeployRequest.version` (freeform — image tag, git sha, etc.).
    version: Mapped[str] = mapped_column(String(200), nullable=False)
    # Terminal state. `pending`/`running` rows exist only while the
    # task is in flight; the finalizer flips them to one of the
    # terminal kinds.
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    bound_url: Mapped[str | None] = mapped_column(Text)
    exec_code: Mapped[str | None] = mapped_column(String(80))
    # Last ~2 KB of stdout+stderr. Full streams are firehose — we cap
    # at the same tail the orchestrator keeps in memory.
    log_tail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Freeform actor label (`admin`, `webhook`, etc). No FK — single-
    # admin model, plus we want webhook-triggered rows to record
    # `webhook` directly. Nullable for the rare case of system-internal
    # rollbacks.
    actor_id: Mapped[str | None] = mapped_column(String(80))
    # `manual` | `webhook:<branch>` | `rollback` | `schedule`.
    # Free-text so we don't need a new enum every time we add a
    # trigger source.
    trigger_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="manual"
    )

    __table_args__ = (
        Index("ix_deploy_runs_environment_id", "environment_id"),
        Index("ix_deploy_runs_started_at", "started_at"),
    )


# ─────────────────────────────────────────────────────────── workspaces ──


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    worktree_path: Mapped[str] = mapped_column(Text, nullable=False)
    sandbox_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN), ForeignKey("sandboxes.id", ondelete="SET NULL")
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        _pg_enum(WorkspaceStatus, "workspace_status_enum"),
        nullable=False,
        server_default=WorkspaceStatus.CREATING.value,
    )
    port_assignments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_workspaces_project_status", "project_id", "status"),
        # Phase C.1: one *active* workspace per (project, branch).
        # Partial index — archived rows can pile up freely so the audit
        # value is preserved, and re-creating a workspace for a branch
        # whose prior row was archived still works.
        Index(
            "ix_workspaces_project_branch_active",
            "project_id",
            "branch",
            unique=True,
            postgresql_where=text("status != 'archived'"),
        ),
    )


# ──────────────────────────────────────────────────────────── sandboxes ──


class Sandbox(Base):
    __tablename__ = "sandboxes"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    status: Mapped[SandboxStatus] = mapped_column(
        _pg_enum(SandboxStatus, "sandbox_status_enum"),
        nullable=False,
        server_default=SandboxStatus.CREATING.value,
    )
    container_id: Mapped[str | None] = mapped_column(String(128))
    image_tag: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = _created_at()


# ────────────────────────────────────────────────────── agent_sessions ──


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    env_manifest_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AgentSessionStatus] = mapped_column(
        _pg_enum(AgentSessionStatus, "agent_session_status_enum"),
        nullable=False,
        server_default=AgentSessionStatus.ACTIVE.value,
    )
    cost_usd: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # Phase K.2 — Anthropic-style prompt cache token counts. The cost
    # is already correct (Phase I.3's fallback uses these via the
    # token.tracked payload's `cache_write` / `cache_read` keys), but
    # surfacing the counts here lets the cost dashboard explain
    # "6 input + 6 output ≈ $0.0001, yet you paid $0.013 because the
    # turn primed a 3400-token cache".
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = _created_at()
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_agent_sessions_project_status", "project_id", "status"),)


# ─────────────────────────────────────────────────────── session_events ──


class SessionEvent(Base):
    """Phase D.3 — durable SSE event log per agent session.

    Today's `SessionEventBus` keeps the last 1024 events in memory.
    A backend restart wipes that buffer and the chat panel reloads
    blank. Mirroring every published event into this table lets
    the replay endpoint reconstruct the conversation across server
    restarts.

    Primary key is `(session_id, seq)` because seq is monotonic
    per-session and the bus assigns it atomically — there's no
    ambiguity. The table is append-only (no UPDATE / DELETE in the
    happy path) so size grows with usage; old rows are pruned via
    background sweep (out of v1 scope — sweep is added when a real
    user starts hitting the 1MB-per-session threshold).
    """

    __tablename__ = "session_events"

    session_id: Mapped[str] = mapped_column(
        String(ULID_LEN),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_session_events_session_seq", "session_id", "seq"),
    )


# ────────────────────────────────────────────────────────────── secrets ──


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[str] = _pk()
    owner_scope: Mapped[SecretOwnerScope] = mapped_column(
        _pg_enum(SecretOwnerScope, "secret_owner_scope_enum"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(ULID_LEN), nullable=False)
    key_name: Mapped[str] = mapped_column(String(200), nullable=False)
    backend: Mapped[SecretBackend] = mapped_column(
        _pg_enum(SecretBackend, "secret_backend_enum"), nullable=False
    )
    backend_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("owner_scope", "owner_id", "key_name", name="uq_secrets_scope_owner_key"),
    )


# ─────────────────────────────────────────────── agent prefs (admin) ──


class AdminAgentPrefs(Base):
    """Admin-global Agent / manifest overrides.

    Single row (the admin's). Every field is optional — when null we
    fall back to the manifest's bundled value. Used by
    `GaptEnvironmentService.instantiate_pipeline` via the optional
    `overrides` parameter, which patches stage[6].api.config + the
    top-level manifest fields before instantiating the Pipeline.

    Scope is deliberately tiny — model / max_tokens / max_iterations /
    cost_budget_usd / timeout_s / permission_mode. The full geny
    preset editor (21-stage enable/disable graph) is out of scope for
    M1.5; this covers the questions the operator asks ("which model?
    what's my budget?") without dragging in the full pipeline
    composition UI.
    """

    __tablename__ = "admin_agent_prefs"

    # Singleton row — the literal string "admin" is the only key
    # we ever read or write. Saves a join when the API just wants the
    # one row that exists.
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="admin")
    # `model` accepts vendor-prefixed (claude-sonnet-4-6) and bare
    # (sonnet / opus / haiku) forms — geny-executor's `_route_model`
    # normalises both.
    model: Mapped[str | None] = mapped_column(String(80))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    max_iterations: Mapped[int | None] = mapped_column(Integer)
    cost_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    timeout_s: Mapped[int | None] = mapped_column(Integer)
    # CLI permission mode — controls whether spawned `claude_code_cli`
    # auto-approves tool calls. Values: "bypassPermissions" (default —
    # allow all), "acceptEdits" (allow file edits, prompt for risky),
    # "default" (prompt for everything — almost certainly will hang in
    # our non-interactive flow), "plan" (read-only). Null = server default.
    permission_mode: Mapped[str | None] = mapped_column(String(40))
    # Phase G.5 — operator-chosen default manifest. Null falls back to
    # `Settings.default_manifest_id` (gapt_default). Lets the user
    # swap between provider variants (claude_code_cli /
    # anthropic_sdk / openai / google) without redeploying. Resolved
    # by `ProjectAwareSessionManager.create_session` when the request
    # doesn't supply `env_id`.
    default_manifest_id: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ──────────────────────────────────────────────────────── audit_events ──


class AuditEvent(Base):
    """One row per state-changing action across the control plane.

    Monthly partitioning is deferred to a follow-up migration — see
    `docs/progress/m1/e1_backend_foundation.md` Drift for the rationale.
    The `(ts, scope_jsonb->>'project_id')` composite index is created
    explicitly so the most-common query (project audit feed) hits an
    index even before partitioning.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = _pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_type: Mapped[AuditActorType] = mapped_column(
        _pg_enum(AuditActorType, "audit_actor_type_enum"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(ULID_LEN))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    outcome: Mapped[AuditOutcome] = mapped_column(
        _pg_enum(AuditOutcome, "audit_outcome_enum"), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    exec_code: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    __table_args__ = (
        Index("ix_audit_events_ts", "ts"),
        Index("ix_audit_events_action_ts", "action", "ts"),
    )


# ──────────────────────────────────────────────────────── provider_configs ──


class ProviderConfig(Base):
    """Single-row-per-provider integration config (Cloudflare, etc.).

    Non-secret config only — selected account_id / zone_id / tunnel_id,
    discovered domain, last-verified timestamp, free-form metadata.
    The credential itself (API token) is in the secret vault under
    SYSTEM scope, key_name = `provider.<kind>.api_token`; only the
    secret_id is referenced here so the row carries no plaintext.

    `kind` is the primary key — one config per provider kind. This is
    the same singleton-row pattern as `admin_agent_prefs`.
    """

    __tablename__ = "provider_configs"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    """Provider kind, e.g. "cloudflare". Lowercase, snake_case."""

    token_secret_id: Mapped[str | None] = mapped_column(String(ULID_LEN))
    """Vault row id holding the API token. None when not configured."""

    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    """Free-form per-provider config — for Cloudflare: account_id,
    zone_id, tunnel_id, preview_domain, last verification result."""

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Last successful API roundtrip. UI shows this so the operator
    knows the token still works."""

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ──────────────────────────────────────────────── provider_migrations ──


class ProviderMigration(Base):
    """One row per destructive provider operation (cloudflared
    tunnel cutover, etc). Captures the before/after snapshot so the
    operator can review and 1-click revert later. Without this
    audit row we have no way to answer "what did the cutover I ran
    last Tuesday actually change" — the migration scripts are
    stateless."""

    __tablename__ = "provider_migrations"

    id: Mapped[str] = _pk()
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    """e.g. `cloudflare.tunnel_remote_managed` — namespaced so
    future provider migrations slot in here too."""

    provider_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    """FK-like reference to ProviderConfig.kind. Not a real FK
    because a migration may run AFTER the provider config was
    deleted (you might want to revert post-deletion)."""

    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    """`in_progress` | `ok` | `failed` | `rolled_back` | `dry_run`."""

    before_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    """Whatever we captured before mutating — for the cloudflared
    cutover: `{tunnel_ingress: [...], systemd_unit: "...",
    cloudflared_status: "..."}`."""

    after_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    """Re-fetched state after the operation, same shape as before."""

    error: Mapped[str | None] = mapped_column(Text)
    """Free-text error description when status=failed."""

    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the operator clicked revert. Null = still in effect."""

    __table_args__ = (
        Index("ix_provider_migrations_started_at", "started_at"),
        Index(
            "ix_provider_migrations_provider_kind_started_at",
            "provider_kind",
            "started_at",
        ),
    )
