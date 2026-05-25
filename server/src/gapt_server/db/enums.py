"""Strong enums used by ORM models + API schemas.

Stored as native PostgreSQL enums (Alembic emits `CREATE TYPE … AS ENUM`).
String values are the on-the-wire identifiers (snake_case) — never rename
once published, only add new values via migration.
"""

from __future__ import annotations

from enum import StrEnum


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
    # Single-admin model: there is no user/org tier. SYSTEM is the
    # admin-global scope (most secrets); PROJECT / ENVIRONMENT remain
    # for per-project / per-env overrides.
    SYSTEM = "system"
    PROJECT = "project"
    ENVIRONMENT = "environment"


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
