"""`CredentialBundle` builder — single source for every provider's auth.

`claude_code_cli` is GAPT's primary provider; SDK providers
(anthropic / openai / google / vllm) plug in *only* when a manifest
asks for them via stages[6].config.provider, and *only* when the
project has a mapped secret_ref for that key.

Plaintext lifetime is intentionally short:
1. `build_for_session` reads the relevant secrets from `SecretVault`
   one-shot, builds the bundle, and discards the plaintext.
2. The bundle is consumed by ``Pipeline.from_manifest_async`` inside
   the same async stack frame so it doesn't leak to history.

Python doesn't actually let us zeroize bytes, but we minimise the
exposure window and never persist the plaintext to disk / DB.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from geny_executor import CredentialBundle, ProviderCredentials

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.domains.secrets.vault import SecretVault

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SecretRefMap:
    """Project-level mapping from provider name to a secret_id."""

    anthropic: str | None = None
    openai: str | None = None
    google: str | None = None
    vllm: str | None = None


# Module-level singleton for the "no mappings" default — keeps the
# function signature simple while satisfying ruff's B008 (no factory
# call in default-arg position).
_NO_SECRET_REFS = SecretRefMap()


def claude_binary(*, override: str | None = None) -> str:
    """Resolve the `claude` binary path the spawned subprocess uses.

    Priority: explicit override → `CLAUDE_BIN` env → PATH lookup.
    """
    if override:
        return override
    env_override = os.environ.get("CLAUDE_BIN")
    if env_override:
        return env_override
    bin_path = shutil.which("claude")
    if bin_path is None:
        raise FileNotFoundError("`claude` CLI not on PATH. Install Claude Code or set CLAUDE_BIN.")
    return bin_path


def build_claude_code_cli_creds(
    *,
    binary_path: str,
    api_key: str = "",
    workspace_root: str | None = None,
    mcp_config: dict[str, Any] | None = None,
    settings_path: str | None = None,
    timeout_s: float = 180.0,
    max_budget_usd: float | None = 1.0,
    default_permission_mode: str = "default",
    extra_args: tuple[str, ...] = (),
) -> ProviderCredentials:
    """`ProviderCredentials` for the `claude_code_cli` provider.

    `bare_mode=True` is the safe default — geny-executor strips it
    automatically on the OAuth subscription path (see
    reference_geny_executor_v2_1). When ANTHROPIC_API_KEY is set,
    the CLI uses that key instead of the host's OAuth credentials.
    """
    extras: dict[str, Any] = {
        "bare_mode": True,
        "default_permission_mode": default_permission_mode,
        "timeout_s": timeout_s,
    }
    if workspace_root is not None:
        extras["workspace_root"] = workspace_root
    if max_budget_usd is not None:
        extras["max_budget_usd"] = max_budget_usd
    if settings_path is not None:
        extras["settings_path"] = settings_path
    if mcp_config is not None:
        extras["mcp_config"] = mcp_config
    if extra_args:
        extras["extra_args"] = extra_args
    return ProviderCredentials(
        api_key=api_key,
        binary_path=binary_path,
        extras=extras,
    )


async def build_for_session(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    secret_refs: SecretRefMap = _NO_SECRET_REFS,
    binary_path: str | None = None,
    workspace_root: str | None = None,
    mcp_config: dict[str, Any] | None = None,
    settings_path: str | None = None,
    timeout_s: float = 180.0,
    max_budget_usd: float | None = 1.0,
) -> CredentialBundle:
    """Build a `CredentialBundle` for an agent session.

    `secret_refs` carries the project's secret_ids for each SDK
    provider. We read *only* the ones that are mapped — unused
    providers stay absent from the bundle (geny-executor's strict
    load won't complain since the manifest only references the
    provider it actually uses).

    `actor_id` is the user id; it lands in the ``secret.read`` audit
    event the vault emits.
    """
    bundle_map: dict[str, ProviderCredentials] = {}

    # Primary: claude_code_cli always present. It still works without
    # any explicit API key because the spawned `claude` CLI can use
    # the host's OAuth subscription.
    bundle_map["claude_code_cli"] = build_claude_code_cli_creds(
        binary_path=binary_path or claude_binary(),
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        workspace_root=workspace_root,
        mcp_config=mcp_config,
        settings_path=settings_path,
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
    )

    # SDK providers — only included when the project has a mapped
    # secret_ref. Read plaintext, stuff into bundle, drop reference.
    for provider, secret_id in (
        ("anthropic", secret_refs.anthropic),
        ("openai", secret_refs.openai),
        ("google", secret_refs.google),
        ("vllm", secret_refs.vllm),
    ):
        if secret_id is None:
            continue
        plaintext = await vault.read(
            db,
            secret_id=secret_id,
            purpose=f"agent_session.{provider}",
            actor_id=actor_id,
        )
        bundle_map[provider] = ProviderCredentials(api_key=plaintext)
        # Drop the plaintext reference. Python can't truly zeroize a
        # str, but rebinding the name shrinks the exposure window.
        del plaintext

    logger.info(
        "agent.credentials.built",
        providers=sorted(bundle_map.keys()),
        has_mcp_config=mcp_config is not None,
        has_settings_path=settings_path is not None,
    )
    return CredentialBundle(by_provider=bundle_map)
