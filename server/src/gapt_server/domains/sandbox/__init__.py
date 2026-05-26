"""Sandbox domain — D4.

`SandboxBackend` is the only thing the rest of the control plane sees
when it asks for an isolated workspace. The single implementation —
`SysboxBackend` — drives the host docker daemon. The earlier in-memory
mock backend was retired in 2026-05; tests that need a sandbox stub
should provide their own narrow fake at the protocol boundary.

The mount-whitelist invariant is enforced *inside* the backend
construction so a future programmer can't accidentally bypass it by
adding a path to a CreateSpec — see [09](docs/09_security_authz_observability.md) §9.2.4.
"""

from gapt_server.domains.sandbox.backend import (
    ExecResult,
    MountSpec,
    SandboxBackend,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxInfo,
    SandboxRef,
    SandboxResources,
    SecurityInvariantError,
    forbidden_mount_paths,
    validate_mounts,
)
from gapt_server.domains.sandbox.sysbox_backend import (
    SysboxBackend,
    SysboxBackendConfig,
    make_default_client,
)

__all__ = [
    "ExecResult",
    "MountSpec",
    "SandboxBackend",
    "SandboxBackendError",
    "SandboxCreateSpec",
    "SandboxInfo",
    "SandboxRef",
    "SandboxResources",
    "SecurityInvariantError",
    "SysboxBackend",
    "SysboxBackendConfig",
    "forbidden_mount_paths",
    "make_default_client",
    "validate_mounts",
]
