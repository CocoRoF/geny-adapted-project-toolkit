"""Agent domain — the glue between control plane and geny-executor.

`GaptEnvironmentService` resolves a manifest id to an
`EnvironmentManifest`, then hands it to `Pipeline.from_manifest_async`
along with a `CredentialBundle`.

geny-executor 2.2.0 note: the `executor_patches.py` monkey-patch layer
(private `_call_streaming` / `StreamJsonAccumulator.feed` /
`CLIProcessRunner._spawn` forks) is gone — chunk forwarding and
tool_result echoes are built into Stage 6's event stream, and docker
sandbox routing uses the supported
`ClaudeCodeCLIClient(runner_factory=...)` seam (see `sandbox_runner.py`).
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
