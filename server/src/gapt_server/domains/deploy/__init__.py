"""Deploy domain — DeployTarget Protocol + LocalCompose / RemoteSsh / Webhook
adapters + Orchestrator + 2FA gate. Wired into `routers/deploy.py`."""

from gapt_server.domains.deploy.local import LocalComposeTarget
from gapt_server.domains.deploy.orchestrator import (
    DeployOrchestrator,
    OrchestratorError,
    SecretBundleResolver,
)
from gapt_server.domains.deploy.protocol import (
    DeployContext,
    DeployRequest,
    DeployResult,
    DeployStatus,
    DeployStatusKind,
    DeployTarget,
    DeployTargetError,
    RollbackResult,
)
from gapt_server.domains.deploy.ssh import RemoteSshTarget, SshConnectionSpec
from gapt_server.domains.deploy.two_factor import (
    AcceptAnyCodeVerifier,
    AlwaysDenyVerifier,
    TwoFactorError,
    TwoFactorVerifier,
)
from gapt_server.domains.deploy.webhook import WebhookTarget

__all__ = [
    "AcceptAnyCodeVerifier",
    "AlwaysDenyVerifier",
    "DeployContext",
    "DeployOrchestrator",
    "DeployRequest",
    "DeployResult",
    "DeployStatus",
    "DeployStatusKind",
    "DeployTarget",
    "DeployTargetError",
    "LocalComposeTarget",
    "OrchestratorError",
    "RemoteSshTarget",
    "RollbackResult",
    "SecretBundleResolver",
    "SshConnectionSpec",
    "TwoFactorError",
    "TwoFactorVerifier",
    "WebhookTarget",
]
