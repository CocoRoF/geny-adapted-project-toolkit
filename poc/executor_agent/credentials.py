"""CredentialBundle builder for the M0-P3 PoC.

Picks up the host's claude OAuth subscription (~/.claude/.credentials.json
populated by `claude auth login`) when ANTHROPIC_API_KEY is unset, and
falls back to the API-key path when it's set. Either mode satisfies
geny-executor 2.1.0's claude_code_cli provider — see
reference_geny_executor_v2_1.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from geny_executor import CredentialBundle, ProviderCredentials


def claude_binary() -> str:
    """Resolve the `claude` binary path the spawned subprocess should use."""
    bin_path = shutil.which("claude")
    if bin_path is None:
        raise FileNotFoundError(
            "`claude` CLI not on PATH. Install Claude Code or set CLAUDE_BIN."
        )
    return os.environ.get("CLAUDE_BIN", bin_path)


def build_credentials(
    *,
    mcp_config: dict[str, Any] | None = None,
    settings_path: str | None = None,
    timeout_s: float = 120.0,
    max_budget_usd: float | None = 0.1,
    extra_args: tuple[str, ...] = (),
) -> CredentialBundle:
    """Return a CredentialBundle ready for `Pipeline.from_manifest_async`."""
    extras: dict[str, Any] = {
        "bare_mode": True,            # auto-stripped by executor on OAuth path
        "default_permission_mode": "default",
        "timeout_s": timeout_s,
    }
    if max_budget_usd is not None:
        extras["max_budget_usd"] = max_budget_usd
    if settings_path is not None:
        extras["settings_path"] = settings_path
    if mcp_config is not None:
        extras["mcp_config"] = mcp_config
    if extra_args:
        extras["extra_args"] = extra_args

    return CredentialBundle(
        by_provider={
            "claude_code_cli": ProviderCredentials(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                binary_path=claude_binary(),
                extras=extras,
            ),
        }
    )
