"""SQLAlchemy ORM models — control-plane data model.

Sources:
- `docs/03_system_architecture.md` §3.3 — conceptual model
- `docs/plan/m1/e1_backend_foundation.md` §1.1 — table-level decisions

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
    Role,
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


# ──────────────────────────────────────────────────────────────── users ──


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()


class UserAgentPrefs(Base):
    """User-global Agent / manifest overrides.

    Single row per user. Every field is optional — when null we fall
    back to the manifest's bundled value. Wired into
    `GaptEnvironmentService.instantiate_pipeline` via the optional
    `overrides` parameter, which patches stage[6].api.config + the
    top-level manifest fields before instantiating the Pipeline.

    Scope is deliberately tiny — model / max_tokens / max_iterations /
    cost_budget_usd / timeout_s. The full geny preset editor (21-stage
    enable/disable graph) is out of scope for M1.5; this covers the
    questions every new user asks ("which model? what's my budget?")
    without dragging in the full pipeline composition UI.
    """

    __tablename__ = "user_agent_prefs"

    id: Mapped[str] = _pk()
    user_id: Mapped[str] = mapped_column(
        String(ULID_LEN),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # `model` accepts vendor-prefixed (claude-sonnet-4-6) and bare
    # (sonnet / opus / haiku) forms — geny-executor's `_route_model`
    # normalises both.
    model: Mapped[str | None] = mapped_column(String(80))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    max_iterations: Mapped[int | None] = mapped_column(Integer)
    cost_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    timeout_s: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ───────────────────────────────────────────────────────────────── orgs ──


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = _pk()
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class OrgMembership(Base):
    __tablename__ = "org_memberships"

    org_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "role_enum"), nullable=False)
    created_at: Mapped[datetime] = _created_at()


# ─────────────────────────────────────────────────────────── projects ──


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = _pk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    org_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("orgs.id", ondelete="RESTRICT"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
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
    created_at: Mapped[datetime] = _created_at()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_projects_org_slug"),)


class ProjectMembership(Base):
    __tablename__ = "project_memberships"

    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "role_enum"), nullable=False)
    created_at: Mapped[datetime] = _created_at()


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
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_environments_project_name"),)


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

    __table_args__ = (Index("ix_workspaces_project_status", "project_id", "status"),)


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
    user_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
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
    created_at: Mapped[datetime] = _created_at()
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_agent_sessions_project_status", "project_id", "status"),)


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
