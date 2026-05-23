"""Agent domain — the glue between control plane and geny-executor.

`GaptEnvironmentService` resolves a manifest id to an
`EnvironmentManifest`, then hands it to `Pipeline.from_manifest_async`
along with a `CredentialBundle`. Sessions, credentials, and
ProjectAwareSessionManager land in later cycles (2.2 / 2.8 / 2.10).
"""

from gapt_server.agent.environment_service import (
    GaptEnvironmentService,
    ManifestNotFoundError,
    ManifestResolution,
)

__all__ = ["GaptEnvironmentService", "ManifestNotFoundError", "ManifestResolution"]
