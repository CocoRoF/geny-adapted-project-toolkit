"""DeployTarget Protocol + shared value types.

Three implementations land in this cycle:

- `LocalComposeTarget` — same-host *prod-sandbox* (separate Sysbox
  container) gets `docker compose up`.
- `RemoteSshTarget` — registered SSH key → remote host runs the same
  compose commands; short-lived ssh-agent so the private key never
  touches host disk past the run.
- `WebhookTarget` — HMAC-signed POST to a user-defined URL.

The Protocol is intentionally `deploy / status / rollback` — the same
three verbs documented in `docs/07_cicd_and_preview.md` §7.4.2. The
`Orchestrator` (Cycle 4.2) wires PolicyEngine + 2FA + audit *around*
these adapters; the adapters themselves stay focused on the transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class DeployTargetError(RuntimeError):
    """Stable code suffix surfaces to the router as HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeployStatusKind(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class DeployRequest:
    """The caller's view of a deploy intent.

    `version` is freeform — adapters that care about specific image
    digests / git refs interpret it. `env_secrets` is the *plaintext*
    secret bundle the orchestrator just read from Secret Vault; the
    adapter consumes it and is expected to zeroize on completion.
    `compose_path` is the deployment unit (file path inside the
    workspace) — different targets handle it differently."""

    project_id: str
    environment: str  # "dev" | "staging" | "prod" | user-defined
    version: str
    compose_path: str = "docker-compose.yml"
    # When non-empty, the target chains all paths via `-f a -f b` so
    # multi-file projects (e.g. Geny's dev/dev-core split) work
    # without flattening upstream. `compose_path` is the fallback
    # when this list is empty.
    compose_paths: list[str] = field(default_factory=list)
    env_secrets: dict[str, str] = field(default_factory=dict)
    # Free-form per-target options (e.g. ssh host, webhook URL). The
    # adapter validates its own subset.
    target_options: dict[str, object] = field(default_factory=dict)

    def resolved_compose_paths(self) -> list[str]:
        """Return the effective list — `compose_paths` if set,
        otherwise a single-element list around `compose_path`."""
        return list(self.compose_paths) if self.compose_paths else [self.compose_path]


@dataclass(frozen=True)
class DeployContext:
    """Mutable per-deploy context the Protocol methods receive.

    `run_id` is assigned by the Orchestrator (ULID) and is what the
    UI uses to poll status / pull logs / trigger rollback. `request`
    is the immutable user intent. `started_at` is set when `deploy()`
    enters the running state — the adapter doesn't fill it before."""

    run_id: str
    request: DeployRequest
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True)
class DeployResult:
    """Returned by `deploy()` on terminal completion.

    A *non-final* status (RUNNING / PENDING) lives in the `status()`
    return — `deploy()` blocks until the run finishes (or fails), at
    which point it returns this. The orchestrator forwards each
    interim status line via SSE to the UI."""

    run_id: str
    status: DeployStatusKind
    log: str = ""  # combined stdout/stderr tail (truncated)
    exec_code: str | None = None  # only on FAILED / non-success
    # Public URL the target exposed for the deployed stack, if any.
    # For LocalComposeTarget this is the Caddy preview subdomain that
    # was auto-registered after a successful `up -d`. None when no
    # routing happened (no SubdomainManager wired / no published port
    # to expose / non-local target without an external URL convention).
    bound_url: str | None = None


@dataclass(frozen=True)
class DeployStatus:
    """One-shot snapshot of an in-progress or completed deploy."""

    run_id: str
    status: DeployStatusKind
    log_tail: str = ""
    exec_code: str | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class RollbackResult:
    """Returned by `rollback()`. `restored_version` is what the
    target ended up at; for compose targets this is the prior image
    digest if the adapter snapshots one, otherwise a freeform string
    the adapter chose."""

    run_id: str
    status: DeployStatusKind  # SUCCESS / FAILED / ROLLED_BACK
    restored_version: str | None = None
    log: str = ""
    exec_code: str | None = None


class DeployTarget(Protocol):
    """Three-verb deploy contract.

    Implementations are *stateful* per target instance (e.g. the
    SSH target holds the agent socket) but *stateless across runs*.
    The orchestrator passes a fresh `DeployContext` per deploy.
    """

    name: str

    async def deploy(self, ctx: DeployContext) -> DeployResult: ...

    async def status(self, ctx: DeployContext) -> DeployStatus: ...

    async def rollback(self, ctx: DeployContext, *, to_version: str) -> RollbackResult: ...
