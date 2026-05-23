"""Deploy domain — DeployTarget Protocol + LocalCompose / RemoteSsh / Webhook
adapters. Wired into `routers/deploy.py` (Cycle 4.2)."""

from gapt_server.domains.deploy.local import LocalComposeTarget
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
from gapt_server.domains.deploy.webhook import WebhookTarget

__all__ = [
    "DeployContext",
    "DeployRequest",
    "DeployResult",
    "DeployStatus",
    "DeployStatusKind",
    "DeployTarget",
    "DeployTargetError",
    "LocalComposeTarget",
    "RemoteSshTarget",
    "RollbackResult",
    "SshConnectionSpec",
    "WebhookTarget",
]
