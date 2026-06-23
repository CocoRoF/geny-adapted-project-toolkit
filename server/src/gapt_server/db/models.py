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
    SnapshotKind,
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


# ─────────────────────────────────────────────────── project_repositories ──


class ProjectRepository(Base):
    """Phase N.4 — one project can carry zero or more git repositories.

    The old model baked git_remote_url / provider / compose paths /
    auth onto ``Project`` itself, so every project was exactly one git
    repo. The new model promotes that bundle to its own row and makes
    Project a logical container. An empty project (no rows) is now a
    first-class citizen — its workspaces get a plain worktree dir
    with no clones, ready for an operator to drop loose files or
    `git init` later. A project with N rows clones each repo into its
    own ``subpath`` under the workspace's worktree, side-by-side
    (VS Code's "multi-root workspace" semantics).

    Auto-migration of pre-N.4 projects: for each existing
    ``projects`` row, one ``project_repositories`` row is inserted
    carrying its git+compose bundle with ``subpath=''`` (legacy
    project-root layout). Once that has run the Project columns can
    be retired in a follow-up migration.
    """

    __tablename__ = "project_repositories"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Folder name inside the workspace's worktree. Empty string is
    # the legacy "project root" layout — the single repo sits AT
    # ``/workspace`` rather than under a sub-folder. Multi-repo
    # projects use non-empty subpaths like "geny-executor", "vendor",
    # etc. Validated at write-time: must be a single path segment
    # (no slashes), unique within a project.
    subpath: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=""
    )
    # Human-friendly label for the GitPanel tree node. Defaults to
    # the repo's short name (last segment of the URL) when not set.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Git URL. NULL is reserved for the "empty subdir, git init
    # later" case — not exposed in the create flow yet but the
    # column allows it so we can add empty-repo support without a
    # migration later.
    git_remote_url: Mapped[str | None] = mapped_column(Text)
    git_provider: Mapped[GitProvider | None] = mapped_column(
        _pg_enum(GitProvider, "git_provider_enum")
    )
    # Per-repo auth. NULL → fall back to Project.git_auth_secret_ref
    # (still readable post-migration via the legacy column or a
    # future ProjectDefault row). Lets OSS + private repos coexist
    # in one project with different GitHub accounts.
    git_auth_secret_ref: Mapped[str | None] = mapped_column(Text)
    # Per-repo compose paths. Each repo deploys as its own stack
    # (decision: "each repo deploys independently"). Empty array =
    # "this repo is not deployable on its own" (e.g. a vendored
    # library that ships inside another repo's image).
    default_compose_paths: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    compose_profile_dev: Mapped[str | None] = mapped_column(String(80))
    compose_profile_prod: Mapped[str | None] = mapped_column(String(80))
    # Branch the workspace clones initially. NULL → server's HEAD.
    # Per-repo branches let one workspace hold (e.g.) main of one
    # repo + a feature branch of another — the VS Code multi-root
    # use case.
    default_branch: Mapped[str | None] = mapped_column(String(255))
    # Display order in the GitPanel tree + the "first match" default
    # for empty deploy-target pickers. Lower comes first. Not unique
    # — operators are free to assign whatever order they like.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = _created_at()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "project_id", "subpath",
            name="uq_project_repositories_subpath",
        ),
        Index(
            "ix_project_repositories_project_sort",
            "project_id", "sort_order",
        ),
    )


# ─────────────────────────────────────────────────────── environments ──


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Phase N.4 — which of the project's repositories supplies the
    # compose files / deploy target for this env. NULL = legacy
    # behaviour (project-wide compose paths). Set on env creation;
    # the auto-migration fills it with the project's primary repo
    # for every existing row so behaviour is preserved on upgrade.
    repository_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN),
        ForeignKey("project_repositories.id", ondelete="SET NULL"),
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

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_environments_project_name"),
        Index("ix_environments_repository_id", "repository_id"),
    )


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
    """Phase N.5 — a workspace is a logical workspace, not a branch.

    Pre-N.5 every workspace was identified by ``(project, branch)`` so
    creating a workspace meant "open branch X", which collapsed in the
    multi-repo world where each repo can be on a different branch. The
    new model:

    - ``name`` is the user-facing identity (operator types it, or we
      auto-generate "workspace-N"). Idempotency is now keyed on
      ``(project, name)`` so reopening "workspace-1" twice is a no-op.
    - Which repositories get cloned + which branch each lands on is
      recorded in the ``workspace_repositories`` join rows below. The
      old ``branch`` column is gone — branch is per-repo now.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = _pk()
    project_id: Mapped[str] = mapped_column(
        String(ULID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Phase N.5 — workspace identity. Replaces ``branch`` as the
    # primary user-facing label. The migration backfills this from
    # the legacy branch column for existing rows so URLs / audit
    # trails stay intelligible.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
        # Phase N.5: one *active* workspace per (project, name).
        # Partial index — archived rows can pile up freely so the audit
        # value is preserved, and re-creating a workspace named X after
        # its prior row was archived still works.
        Index(
            "ix_workspaces_project_name_active",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("status != 'archived'"),
        ),
        Index("ix_workspaces_sandbox_id", "sandbox_id"),
    )


# ─────────────────────────────────────────────── workspace_repositories ──


class WorkspaceRepository(Base):
    """Phase N.5 — per-workspace repo selection + branch.

    Workspace creation now takes a list of these: "clone repo R at
    branch B into the workspace's worktree." This decouples branch
    selection from workspace identity (workspace ≠ branch any more)
    and lets one workspace hold (say) repo A on ``main`` next to
    repo B on ``feature/x`` — the VS Code multi-root case.

    Rows are immutable for the workspace's lifetime: edits to which
    repos are linked require recreating the workspace (operator can
    delete + create fresh from the GitPanel). Archived workspaces
    leave their rows behind for audit value; the FK uses
    ``ondelete='CASCADE'`` so dropping the workspace cleans them up.
    """

    __tablename__ = "workspace_repositories"

    id: Mapped[str] = _pk()
    workspace_id: Mapped[str] = mapped_column(
        String(ULID_LEN),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Which of the project's repos is included. FK so a deleted /
    # archived ProjectRepository row doesn't dangle here — the
    # workspace's view of that selection becomes ``NULL`` and the
    # GitPanel skips the entry.
    project_repository_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN),
        ForeignKey("project_repositories.id", ondelete="SET NULL"),
    )
    # The branch the workspace clones this repo at. Distinct from
    # ``ProjectRepository.default_branch`` so each workspace can pin
    # its own. Empty string is reserved for the "empty repo / git
    # init candidate" case where no clone happens.
    branch: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "project_repository_id",
            name="uq_workspace_repositories_pair",
        ),
        Index(
            "ix_workspace_repositories_workspace",
            "workspace_id",
        ),
        Index(
            "ix_workspace_repositories_project_repository_id",
            "project_repository_id",
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

    __table_args__ = (
        Index("ix_sandboxes_project_id", "project_id"),
        Index("ix_sandboxes_workspace_id", "workspace_id"),
    )


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
    # Phase N.3 — per-session USD cap enforced by GAPT before each
    # invoke turn. NULL = no cap (free mode). When set, the invoke
    # handler 402s with `session.budget_exhausted` once `cost_usd`
    # crosses this number — geny-executor's own `--max-budget-usd`
    # flag is no longer wired up, so the agent never sees budget
    # metadata in its prompt context.
    cost_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    created_at: Mapped[datetime] = _created_at()
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_agent_sessions_project_status", "project_id", "status"),
        Index("ix_agent_sessions_workspace_id", "workspace_id"),
    )


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


# ───────────────────────────────────────────────────────────── snapshots ──


class Snapshot(Base):
    """A git-grade, AI-first workspace checkpoint.

    Captures three things at a point in time so a workspace can be restored
    *and* inspected later:

    1. **File state + build artifacts** — a commit object on the reserved ref
       ``refs/snapshots/<id>`` (``git_sha``). For ``tool_save`` snapshots the
       capture force-includes normally-ignored artifacts (venv / build output)
       so a cold restore reproduces a *working* environment.
    2. **Agent activity** — the chat dialog + tool calls/results that produced
       this state, pinned by the ``session_events`` seq range
       (``event_start_seq``..``event_end_seq``) and stored compactly in
       ``activity`` (durable against future event pruning).
    3. **A graph** — ``parent_id`` chains snapshots into a DAG (git-like
       history; the diff vs parent is computed on demand from the commits).

    This is the durable "sandbox" half of a Sandbox Tool Pack: a saved tool
    references a snapshot, and reuse restores the workspace from it.
    """

    __tablename__ = "snapshots"

    id: Mapped[str] = _pk()
    workspace_id: Mapped[str] = mapped_column(
        String(ULID_LEN),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable: snapshots can be session-scoped (tool_save / auto) or manual
    # (operator pressed the button with no live agent session).
    session_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN),
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
    )
    # The DAG edge. SET NULL so deleting a mid-history snapshot doesn't cascade
    # away its descendants — they just lose the back-link.
    parent_id: Mapped[str | None] = mapped_column(
        String(ULID_LEN),
        ForeignKey("snapshots.id", ondelete="SET NULL"),
    )
    kind: Mapped[SnapshotKind] = mapped_column(
        _pg_enum(SnapshotKind, "snapshot_kind_enum"),
        nullable=False,
        server_default=SnapshotKind.MANUAL.value,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # refs/snapshots/<id> + the commit sha it points at.
    git_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    # session_events range whose activity this snapshot pins (inclusive).
    event_start_seq: Mapped[int | None] = mapped_column(Integer)
    event_end_seq: Mapped[int | None] = mapped_column(Integer)
    # Cheap summary for list views: {files,additions,deletions,turns,tool_calls}.
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Compact transcript {turns:[{user,assistant,tool_uses,cost_usd}]} — the AI
    # activity baked into the snapshot, durable independent of session_events.
    activity: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        Index("ix_snapshots_workspace_created", "workspace_id", "created_at"),
        Index("ix_snapshots_session_id", "session_id"),
        Index("ix_snapshots_parent_id", "parent_id"),
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
