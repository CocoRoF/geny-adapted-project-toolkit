"""Sandbox domain — D4.

`SandboxBackend` is the only thing the rest of the control plane sees
when it asks for an isolated workspace. M1-E1 Cycle 1.7a ships the
protocol + an in-memory `MockSandboxBackend` for tests and a
mount-whitelist guard that *every* implementation must use; Cycle 1.7b
adds the real `SysboxBackend` against the host docker daemon.

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
from gapt_server.domains.sandbox.mock_backend import MockSandboxBackend
from gapt_server.domains.sandbox.sysbox_backend import (
    SysboxBackend,
    SysboxBackendConfig,
    make_default_client,
)

__all__ = [
    "ExecResult",
    "MockSandboxBackend",
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
