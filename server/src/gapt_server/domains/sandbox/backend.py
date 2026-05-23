"""Sandbox protocol + invariants.

Cycle 1.7a establishes the contract; the real `SysboxBackend` lands in
Cycle 1.7b on top of the docker SDK.

The mount-whitelist invariant is *load-bearing*: GAPT must never mount
the host docker socket (or anything that lets an in-sandbox process
break out) into a sandbox container. This guard runs both in
`SandboxBackend.create()` and as a free function so callers can
pre-validate too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


class SandboxBackendError(RuntimeError):
    """Backend-level failure — operational, not a security violation."""


class SecurityInvariantError(RuntimeError):
    """A mount or option was rejected by the security invariants.

    Raising this prevents the sandbox from being created — there is no
    config knob to weaken it. See `docs/09_security_authz_observability.md`
    §9.2.4.
    """


@dataclass(frozen=True)
class SandboxResources:
    cpu_quota_us: int = 200_000  # 2 vCPU
    cpu_period_us: int = 100_000
    mem_bytes: int = 4 * 1024 * 1024 * 1024  # 4 GiB
    pids_limit: int = 4_096


@dataclass(frozen=True)
class MountSpec:
    """Validated bind mount — host path → container target."""

    source: str
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class SandboxCreateSpec:
    project_id: str
    workspace_id: str | None
    image: str
    resources: SandboxResources = field(default_factory=SandboxResources)
    mounts: tuple[MountSpec, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxRef:
    """Opaque handle returned by `create`. The backend may stash extra
    state on it via the `details` mapping."""

    id: str
    container_id: str | None
    backend: str


@dataclass(frozen=True)
class SandboxInfo:
    ref: SandboxRef
    status: str
    image: str
    created_at: float


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int


class SandboxBackend(Protocol):
    name: str

    async def create(self, spec: SandboxCreateSpec) -> SandboxRef: ...

    async def start(self, ref: SandboxRef) -> None: ...

    async def stop(self, ref: SandboxRef) -> None: ...

    async def inspect(self, ref: SandboxRef) -> SandboxInfo: ...

    async def destroy(self, ref: SandboxRef) -> None: ...

    async def exec_in(self, ref: SandboxRef, argv: list[str]) -> ExecResult: ...


# ─────────────────────────────────────────────────── invariants ──


def forbidden_mount_paths() -> tuple[re.Pattern[str], ...]:
    """Paths whose bind mount is *always* refused.

    Sources for the pattern:
    - Host docker socket — escapes the sandbox immediately.
    - `/proc`, `/sys` — kernel surface; Sysbox handles its own virtualised
      copies, anything host-side here defeats it.
    - SSH agent socket / .ssh dirs on the host — credential exfiltration.
    - `/var/run`, `/run` — too broad; refuse and let callers be specific.
    """
    return (
        re.compile(r"^/var/run/docker\.sock$"),
        re.compile(r"^/run/docker\.sock$"),
        re.compile(r"^/var/run(/|$)"),
        re.compile(r"^/run(/|$)"),
        re.compile(r"^/proc(/|$)"),
        re.compile(r"^/sys(/|$)"),
        re.compile(r"^/dev/kmsg$"),
        re.compile(r"^/root/\.ssh(/|$)"),
        re.compile(r"^/home/[^/]+/\.ssh(/|$)"),
        re.compile(r".*ssh-agent\.sock$"),
        re.compile(r".*/\.aws(/|$)"),
        re.compile(r".*/\.kube(/|$)"),
    )


def validate_mounts(mounts: tuple[MountSpec, ...]) -> None:
    forbid = forbidden_mount_paths()
    for mount in mounts:
        for pattern in forbid:
            if pattern.match(mount.source):
                raise SecurityInvariantError(
                    f"refusing to bind-mount {mount.source!r} into sandbox "
                    f"(matched {pattern.pattern!r})"
                )
