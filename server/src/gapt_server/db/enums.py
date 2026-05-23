"""Strong enums used by ORM models + API schemas.

Stored as native PostgreSQL enums (Alembic emits `CREATE TYPE … AS ENUM`).
String values are the on-the-wire identifiers (snake_case) — never rename
once published, only add new values via migration.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Generic membership role. Used by both `org_memberships` and
    `project_memberships`.

    Order is meaningful: `OWNER > ADMIN > EDITOR > VIEWER`.
    """

    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"


class GitProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"
    GENERIC = "generic"


class DeployTargetKind(StrEnum):
    LOCAL = "local"
    REMOTE_SSH = "remote_ssh"
    WEBHOOK = "webhook"
    K8S = "k8s"


class WorkspaceStatus(StrEnum):
    CREATING = "creating"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    ARCHIVED = "archived"


class SandboxStatus(StrEnum):
    CREATING = "creating"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    STALE_IDLE = "stale_idle"
    STALE_COMPACT = "stale_compact"
    ARCHIVED = "archived"


class SecretOwnerScope(StrEnum):
    USER = "user"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    ORG = "org"


class SecretBackend(StrEnum):
    KEYRING = "keyring"
    ENCRYPTED_SQLITE = "encrypted_sqlite"
    SOPS = "sops"
    INFISICAL = "infisical"


class AuditActorType(StrEnum):
    USER = "user"
    AGENT_SESSION = "agent_session"
    SYSTEM = "system"


class AuditOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
