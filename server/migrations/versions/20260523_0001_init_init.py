"""init — 11 control-plane tables + 8 native enums

Revision ID: 0001_init
Revises:
Create Date: 2026-05-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─────────────────────────────────────────────────────────── enum types ──

_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("role_enum", ("viewer", "editor", "admin", "owner")),
    ("git_provider_enum", ("github", "gitlab", "gitea", "generic")),
    ("deploy_target_kind_enum", ("local", "remote_ssh", "webhook", "k8s")),
    (
        "workspace_status_enum",
        ("creating", "running", "paused", "stopped", "failed", "archived"),
    ),
    ("sandbox_status_enum", ("creating", "running", "paused", "stopped", "failed")),
    (
        "agent_session_status_enum",
        ("active", "stale_idle", "stale_compact", "archived"),
    ),
    ("secret_owner_scope_enum", ("user", "project", "environment", "org")),
    ("secret_backend_enum", ("keyring", "encrypted_sqlite", "sops", "infisical")),
    ("audit_actor_type_enum", ("user", "agent_session", "system")),
    ("audit_outcome_enum", ("ok", "error", "denied")),
)


def _enum(name: str) -> postgresql.ENUM:
    values = next(v for n, v in _ENUMS if n == name)
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    # Create enum types first — Postgres won't allow tables to reference
    # them otherwise.
    for name, values in _ENUMS:
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=False)

    # ──────────── users ────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ──────────── orgs ────────────
    op.create_table(
        "orgs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_orgs_owner_id_users"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ──────────── org_memberships ────────────
    op.create_table(
        "org_memberships",
        sa.Column(
            "org_id",
            sa.String(26),
            sa.ForeignKey(
                "orgs.id", ondelete="CASCADE", name="fk_org_memberships_org_id_orgs"
            ),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_org_memberships_user_id_users"
            ),
            primary_key=True,
        ),
        sa.Column("role", _enum("role_enum"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ──────────── projects ────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column(
            "org_id",
            sa.String(26),
            sa.ForeignKey("orgs.id", ondelete="RESTRICT", name="fk_projects_org_id_orgs"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.String(26),
            sa.ForeignKey(
                "users.id", ondelete="RESTRICT", name="fk_projects_owner_id_users"
            ),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("git_remote_url", sa.Text(), nullable=False),
        sa.Column("git_provider", _enum("git_provider_enum"), nullable=False),
        sa.Column("git_auth_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "default_compose_paths",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("compose_profile_dev", sa.String(80), nullable=True),
        sa.Column("compose_profile_prod", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "slug", name="uq_projects_org_slug"),
    )

    # ──────────── project_memberships ────────────
    op.create_table(
        "project_memberships",
        sa.Column(
            "project_id",
            sa.String(26),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_project_memberships_project_id_projects",
            ),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
                name="fk_project_memberships_user_id_users",
            ),
            primary_key=True,
        ),
        sa.Column("role", _enum("role_enum"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ──────────── environments ────────────
    op.create_table(
        "environments",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(26),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_environments_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("deploy_target_kind", _enum("deploy_target_kind_enum"), nullable=False),
        sa.Column(
            "deploy_target_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "require_2fa",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "secret_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "cost_multiplier",
            sa.Numeric(8, 4),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "hooks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_environments_project_name"),
    )

    # ──────────── sandboxes ────────────
    # Note: workspaces.sandbox_id and sandboxes.workspace_id form a cycle.
    # We create sandboxes first WITHOUT the workspace_id FK, create
    # workspaces (FK to sandboxes), then add the workspace_id FK on
    # sandboxes via ALTER TABLE.
    op.create_table(
        "sandboxes",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(26),
            sa.ForeignKey(
                "projects.id", ondelete="CASCADE", name="fk_sandboxes_project_id_projects"
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(26), nullable=True),
        sa.Column(
            "status",
            _enum("sandbox_status_enum"),
            nullable=False,
            server_default="creating",
        ),
        sa.Column("container_id", sa.String(128), nullable=True),
        sa.Column("image_tag", sa.String(255), nullable=False),
        sa.Column(
            "resource_limits",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ──────────── workspaces ────────────
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(26),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_workspaces_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=False),
        sa.Column(
            "sandbox_id",
            sa.String(26),
            sa.ForeignKey(
                "sandboxes.id",
                ondelete="SET NULL",
                name="fk_workspaces_sandbox_id_sandboxes",
            ),
            nullable=True,
        ),
        sa.Column(
            "status",
            _enum("workspace_status_enum"),
            nullable=False,
            server_default="creating",
        ),
        sa.Column(
            "port_assignments",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_foreign_key(
        "fk_sandboxes_workspace_id_workspaces",
        "sandboxes",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_workspaces_project_status", "workspaces", ["project_id", "status"]
    )

    # ──────────── agent_sessions ────────────
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(26),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_agent_sessions_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(26),
            sa.ForeignKey(
                "workspaces.id",
                ondelete="CASCADE",
                name="fk_agent_sessions_workspace_id_workspaces",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
                name="fk_agent_sessions_user_id_users",
            ),
            nullable=False,
        ),
        sa.Column("env_manifest_id", sa.String(120), nullable=False),
        sa.Column(
            "status",
            _enum("agent_session_status_enum"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_agent_sessions_project_status",
        "agent_sessions",
        ["project_id", "status"],
    )

    # ──────────── secrets ────────────
    op.create_table(
        "secrets",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("owner_scope", _enum("secret_owner_scope_enum"), nullable=False),
        sa.Column("owner_id", sa.String(26), nullable=False),
        sa.Column("key_name", sa.String(200), nullable=False),
        sa.Column("backend", _enum("secret_backend_enum"), nullable=False),
        sa.Column("backend_ref", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "owner_scope",
            "owner_id",
            "key_name",
            name="uq_secrets_scope_owner_key",
        ),
    )

    # ──────────── audit_events ────────────
    # Monthly partitioning deferred (see progress card Drift). Plain table
    # with `(action, ts)` and `(ts)` indexes for the M1 audit feed query
    # patterns.
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actor_type", _enum("audit_actor_type_enum"), nullable=False),
        sa.Column("actor_id", sa.String(26), nullable=True),
        sa.Column(
            "scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column(
            "subject",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("outcome", _enum("audit_outcome_enum"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("exec_code", sa.String(120), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])
    op.create_index("ix_audit_events_action_ts", "audit_events", ["action", "ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_action_ts", table_name="audit_events")
    op.drop_index("ix_audit_events_ts", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("secrets")
    op.drop_index("ix_agent_sessions_project_status", table_name="agent_sessions")
    op.drop_table("agent_sessions")

    op.drop_constraint(
        "fk_sandboxes_workspace_id_workspaces", "sandboxes", type_="foreignkey"
    )
    op.drop_index("ix_workspaces_project_status", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("sandboxes")

    op.drop_table("environments")
    op.drop_table("project_memberships")
    op.drop_table("projects")
    op.drop_table("org_memberships")
    op.drop_table("orgs")
    op.drop_table("users")

    for name, _ in reversed(_ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=False)
