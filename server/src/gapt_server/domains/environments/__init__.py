"""Environments domain — schema validation for `deploy_target_config`.

Read [`docs/plan/m2_phase_h.md`](../../../../../docs/plan/m2_phase_h.md)
for the design context. The router layer calls
`validate_target_config(kind, raw_dict)` on every POST/PATCH so the
DB only ever stores rows that the deploy orchestrator can actually
consume. Read-side is intentionally untouched — legacy rows with
stale fields keep listing fine, edit modal handles the cleanup."""

from gapt_server.domains.environments.target_config import (
    KindNotSupportedError,
    TargetConfigInvalidError,
    validate_target_config,
)

__all__ = [
    "KindNotSupportedError",
    "TargetConfigInvalidError",
    "validate_target_config",
]
