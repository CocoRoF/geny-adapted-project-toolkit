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

from gapt_server.db import enums

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.domains.secrets.vault import SecretVault

logger = structlog.get_logger(__name__)


# User-scoped secret keys (matches the Settings UI). Resolved from
# `SecretVault` scoped to the acting user when the project doesn't
# carry an explicit `secret_ref`. Each entry: {vault_key_name: env_alias}.
_USER_SECRET_KEYS: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
}


async def _resolve_user_secret(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    key_name: str,
    purpose: str,
) -> str | None:
    """Look up a user-scoped secret by key_name. Returns plaintext or
    None when the secret isn't stored. Errors are swallowed because a
    missing secret is the normal case — the caller falls back to the
    process env (host-OAuth path)."""
    try:
        metadata = await vault.list(
            db, scope=enums.SecretOwnerScope.USER, owner_id=actor_id
        )
    except Exception:  # noqa: BLE001 — best-effort fallback
        return None
    for md in metadata:
        if md.key_name != key_name:
            continue
        try:
            return await vault.read(
                db, secret_id=md.id, purpose=purpose, actor_id=actor_id
            )
        except Exception:  # noqa: BLE001
            return None
    return None


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
    default_permission_mode: str = "bypassPermissions",
    extra_args: tuple[str, ...] = (),
) -> ProviderCredentials:
    """`ProviderCredentials` for the `claude_code_cli` provider.

    `bare_mode=True` is the safe default — geny-executor strips it
    automatically on the OAuth subscription path (see
    reference_geny_executor_v2_1). When ANTHROPIC_API_KEY is set,
    the CLI uses that key instead of the host's OAuth credentials.

    `default_permission_mode="bypassPermissions"` makes the spawned
    CLI auto-allow every tool call (Read / Edit / Bash / Grep / etc.)
    without prompting. Without this, the CLI runs in `default` mode
    which prompts for each call → in our headless context the
    prompts auto-reject → the agent silently degrades to "just chat,
    no tools." The user reported "agent doesn't seem to look at any
    files" — that was the cause. Users who want stricter behaviour
    can override per-user via the `permission_mode` agent pref
    (`acceptEdits` / `default` / `plan`).
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
    permission_mode: str = "bypassPermissions",
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

    # User-scoped Settings keys (Anthropic/OpenAI/Google) act as a
    # fallback when the project has no explicit secret_ref. Order of
    # precedence per provider: explicit project secret_ref >
    # user-scoped vault secret > process env.
    user_keys: dict[str, str] = {}
    for key_name in _USER_SECRET_KEYS:
        value = await _resolve_user_secret(
            db=db,
            vault=vault,
            actor_id=actor_id,
            key_name=key_name,
            purpose=f"agent_session.{key_name}",
        )
        if value:
            user_keys[key_name] = value

    # Primary: claude_code_cli always present. It still works without
    # any explicit API key because the spawned `claude` CLI can use
    # the host's OAuth subscription. We prefer the user's vault-stored
    # ANTHROPIC_API_KEY when present, then the process env.
    claude_key = user_keys.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    bundle_map["claude_code_cli"] = build_claude_code_cli_creds(
        binary_path=binary_path or claude_binary(),
        api_key=claude_key,
        workspace_root=workspace_root,
        mcp_config=mcp_config,
        settings_path=settings_path,
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
        default_permission_mode=permission_mode,
    )

    # SDK providers — project secret_ref takes precedence; otherwise
    # fall back to the matching user-scoped Settings key.
    for provider, secret_id, user_key_name in (
        ("anthropic", secret_refs.anthropic, "anthropic_api_key"),
        ("openai", secret_refs.openai, "openai_api_key"),
        ("google", secret_refs.google, "google_api_key"),
        ("vllm", secret_refs.vllm, None),
    ):
        plaintext: str | None = None
        if secret_id is not None:
            plaintext = await vault.read(
                db,
                secret_id=secret_id,
                purpose=f"agent_session.{provider}",
                actor_id=actor_id,
            )
        elif user_key_name is not None and user_key_name in user_keys:
            plaintext = user_keys[user_key_name]
        if plaintext is None:
            continue
        bundle_map[provider] = ProviderCredentials(api_key=plaintext)
        # Drop the plaintext reference. Python can't truly zeroize a
        # str, but rebinding the name shrinks the exposure window.
        del plaintext

    # Clear the user-scoped plaintext map.
    user_keys.clear()

    logger.info(
        "agent.credentials.built",
        providers=sorted(bundle_map.keys()),
        has_mcp_config=mcp_config is not None,
        has_settings_path=settings_path is not None,
    )
    return CredentialBundle(by_provider=bundle_map)
