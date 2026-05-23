"""Secret-backend protocol + `EncryptedSqliteBackend`.

Plaintext NEVER leaves a backend — callers exchange `SecretRef`
handles and decryption only happens inside `store/read`. The reference
serialises to a plain string so it can be stored on the `secrets`
table without revealing anything about the underlying material.

Fernet (AES-128-CBC + HMAC-SHA256) is fine for the M1 single-node case
— the master key is derived from `settings.vault_master_key` via
PBKDF2-HMAC-SHA256 at the module's startup. M2+ deployments swap this
for an OS keyring or SOPS-backed backend by implementing the same
protocol.
"""

from __future__ import annotations

import base64
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Static salt — paired with the master key. Acceptable for the M1
# single-node deployment because both halves are colocated; per-secret
# salts are added if/when we promote to remote vaults.
_KDF_SALT = b"gapt-vault-pbkdf2-salt-v1"
_KDF_ITERATIONS = 480_000


def derive_fernet_key(master_key: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    key_material = kdf.derive(master_key.encode("utf-8"))
    return base64.urlsafe_b64encode(key_material)


@dataclass(frozen=True)
class SecretRef:
    """Opaque handle a caller stores on the `secrets` table."""

    backend: str
    locator: str

    def to_str(self) -> str:
        return f"{self.backend}:{self.locator}"

    @classmethod
    def parse(cls, raw: str) -> SecretRef:
        backend, _, locator = raw.partition(":")
        if not locator:
            raise ValueError(f"malformed SecretRef: {raw!r}")
        return cls(backend=backend, locator=locator)


class SecretBackend(Protocol):
    name: str

    async def store(self, plaintext: str) -> SecretRef: ...

    async def read(self, ref: SecretRef) -> str: ...

    async def delete(self, ref: SecretRef) -> None: ...


# ─────────────────────────────────────────────────────── encrypted-sqlite ──


class EncryptedSqliteBackend:
    """Stores ciphertext rows in a local SQLite file.

    Suitable for single-node deployments. The file should sit outside
    of Postgres' backup scope so plaintext recovery isn't easier than
    the threat model expects. Path is `settings.vault_sqlite_path`.
    """

    name = "encrypted_sqlite"

    def __init__(self, *, db_path: Path | str, master_key: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(derive_fernet_key(master_key))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secret_blobs (
                    id        TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )

    async def store(self, plaintext: str) -> SecretRef:
        locator = uuid.uuid4().hex
        ciphertext = self._fernet.encrypt(plaintext.encode("utf-8"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO secret_blobs (id, ciphertext) VALUES (?, ?)",
                (locator, ciphertext),
            )
        return SecretRef(backend=self.name, locator=locator)

    async def read(self, ref: SecretRef) -> str:
        if ref.backend != self.name:
            raise SecretBackendError(f"ref.backend={ref.backend!r} does not match {self.name!r}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM secret_blobs WHERE id = ?",
                (ref.locator,),
            ).fetchone()
        if row is None:
            raise SecretBackendError("secret not found")
        try:
            return self._fernet.decrypt(row[0]).decode("utf-8")
        except InvalidToken as exc:
            raise SecretBackendError("decryption failed (wrong master key?)") from exc

    async def delete(self, ref: SecretRef) -> None:
        if ref.backend != self.name:
            raise SecretBackendError(f"ref.backend={ref.backend!r} does not match {self.name!r}")
        with self._connect() as conn:
            conn.execute("DELETE FROM secret_blobs WHERE id = ?", (ref.locator,))


class SecretBackendError(RuntimeError):
    """Backend-level failure — bubbles up wrapped in `SecretVaultError`."""
