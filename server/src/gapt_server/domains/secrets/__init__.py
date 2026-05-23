"""Secrets domain — D7.

Centralises every operation that touches a secret value.  Plaintext is
**only** read through `SecretVault.read`, which always emits an audit
event. The DB holds the metadata + `backend_ref`; the ciphertext lives
behind a `SecretBackend` (encrypted-sqlite ships in M1-E1; OS keyring
plugs in later).
"""

from gapt_server.domains.secrets.backend import (
    EncryptedSqliteBackend,
    SecretBackend,
    SecretRef,
    derive_fernet_key,
)
from gapt_server.domains.secrets.vault import SecretMetadata, SecretVault, SecretVaultError

__all__ = [
    "EncryptedSqliteBackend",
    "SecretBackend",
    "SecretMetadata",
    "SecretRef",
    "SecretVault",
    "SecretVaultError",
    "derive_fernet_key",
]
