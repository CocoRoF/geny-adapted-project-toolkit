"""Phase N — admin-scope GitHub token lookup for the scaffold pipeline.

Mirrors ``routers/ci.py::_resolve_github_token`` so the two paths
agree on storage shape:

  * scope:    SYSTEM
  * owner_id: admin's user id (we look up by key_name only — scope
              filter is enough since single-admin)
  * key_name: "github_token"

The resolver returns the plaintext token (already decrypted by the
vault) or raises ``ScaffoldError(TOKEN_MISSING)`` so the router can
respond with a 412 + Settings link.

The legacy ``settings.host_github_token`` (gh CLI discovery) is a
secondary fallback. Vault entry always wins when present.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.db import enums
from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError

logger = structlog.get_logger(__name__)

# Stable constant — must match the Settings UI's `key_name` literal
# (see `web/src/routes/Settings.tsx` SecretSpec for "GitHub Personal
# Access Token"). The chain breaks loudly if the two drift.
GITHUB_TOKEN_KEY_NAME = "github_token"


async def resolve_github_token(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    fallback: str | None = None,
    purpose: str = "scaffold.create_repo",
) -> str:
    """Return the GitHub PAT or raise ScaffoldError(TOKEN_MISSING).

    Resolution order:
      1. Newest vault secret with ``key_name=github_token`` scoped to
         ``SYSTEM``. If multiple exist (e.g. operator did "Rotate"
         and the old row is still around), the vault's ``list``
         orders DESC by created_at, so the freshest wins.
      2. ``fallback`` — typically ``settings.host_github_token``
         discovered at container boot (gh auth token). A string here
         doubles as a feature flag: passing None forces "vault only".
    """
    try:
        metadata = await vault.list(
            db, scope=enums.SecretOwnerScope.SYSTEM
        )
    except SecretVaultError as exc:
        logger.warning("scaffold.vault_list_failed", reason=str(exc))
        metadata = []

    for md in metadata:
        if md.key_name != GITHUB_TOKEN_KEY_NAME:
            continue
        try:
            value = await vault.read(
                db, secret_id=md.id, purpose=purpose, actor_id=actor_id
            )
        except SecretVaultError as exc:
            logger.warning(
                "scaffold.vault_read_failed",
                secret_id=md.id,
                reason=str(exc),
            )
            break
        if value and value.strip():
            return value.strip()

    if fallback and fallback.strip():
        logger.info("scaffold.token.fallback", source="host_env_or_gh_cli")
        return fallback.strip()

    raise ScaffoldError(
        ScaffoldErrorCode.TOKEN_MISSING,
        (
            "No GitHub token configured. Open Settings → Credentials and "
            "save a Personal Access Token with `repo` (or `public_repo`) scope."
        ),
    )
