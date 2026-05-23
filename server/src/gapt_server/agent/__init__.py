"""Agent domain — the glue between control plane and geny-executor.

`GaptEnvironmentService` resolves a manifest id to an
`EnvironmentManifest`, then hands it to `Pipeline.from_manifest_async`
along with a `CredentialBundle`. Sessions, credentials, and
ProjectAwareSessionManager land in later cycles (2.2 / 2.8 / 2.10).
"""

from gapt_server.agent.credentials import (
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
from gapt_server.agent.session_manager import (
    AgentSessionHandle,
    ProjectAwareSessionManager,
    SessionManagerError,
)

__all__ = [
    "AgentSessionHandle",
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
