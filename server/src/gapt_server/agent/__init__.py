"""Agent domain — the glue between control plane and geny-executor.

`GaptEnvironmentService` resolves a manifest id to an
`EnvironmentManifest`, then hands it to `Pipeline.from_manifest_async`
along with a `CredentialBundle`. Sessions, credentials, and
ProjectAwareSessionManager land in later cycles (2.2 / 2.8 / 2.10).
"""

# Apply executor monkey-patches before anything else imports the
# executor's stage classes. See `executor_patches.py` for the rationale
# (temporary shim; remove once the upstream patch ships).
from gapt_server.agent.executor_patches import apply_executor_patches as _apply_patches

_apply_patches()

from gapt_server.agent.credentials import (  # noqa: E402 — must run after patch
    SecretRefMap,
    build_claude_code_cli_creds,
    build_for_session,
    claude_binary,
)
from gapt_server.agent.environment_service import (
    GaptEnvironmentService,
    ManifestNotFoundError,
    ManifestResolution,
)
from gapt_server.agent.freshness import (
    FreshnessAction,
    FreshnessPolicy,
    FreshnessRunner,
    FreshnessThresholds,
)
from gapt_server.agent.session_manager import (
    AgentSessionHandle,
    ProjectAwareSessionManager,
    SessionManagerError,
)

__all__ = [
    "AgentSessionHandle",
    "FreshnessAction",
    "FreshnessPolicy",
    "FreshnessRunner",
    "FreshnessThresholds",
    "GaptEnvironmentService",
    "ManifestNotFoundError",
    "ManifestResolution",
    "ProjectAwareSessionManager",
    "SecretRefMap",
    "SessionManagerError",
    "build_claude_code_cli_creds",
    "build_for_session",
    "claude_binary",
]
