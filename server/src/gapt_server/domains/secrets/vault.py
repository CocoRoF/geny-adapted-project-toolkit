"""`SecretVault` — the only thing routers / services should talk to.

Wraps a `SecretBackend` and the `secrets` ORM table so callers never
see plaintext unless they go through `read()`, which always emits an
audit event (route via the audit hook in M1-E1 Cycle 1.5 — for now
the vault just logs at INFO via structlog).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import (
    AuditEvent,
    AuditSink,
    NullAuditSink,
)
from gapt_server.domains.secrets.backend import (
    SecretBackend,
    SecretBackendError,
    SecretRef,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class SecretVaultError(RuntimeError):
    """Raised when the vault refuses an operation — wraps backend
    errors with a stable code suffix for the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SecretMetadata:
    id: str
    owner_scope: enums.SecretOwnerScope
    owner_id: str
    key_name: str
    backend: enums.SecretBackend
    created_at: datetime
    rotated_at: datetime | None


def _metadata(row: models.Secret) -> SecretMetadata:
    return SecretMetadata(
        id=row.id,
        owner_scope=row.owner_scope,
        owner_id=row.owner_id,
        key_name=row.key_name,
        backend=row.backend,
        created_at=row.created_at,
        rotated_at=row.rotated_at,
    )


class SecretVault:
    """One vault per process. Multiple backends can be supported by
    composition later; M1 ships a single backend."""

    def __init__(
        self,
        backend: SecretBackend,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._backend = backend
        self._audit: AuditSink = audit_sink or NullAuditSink()

    # ─────────────────────────────────────────────────────── write ──

    async def store(
        self,
        db: AsyncSession,
        *,
        scope: enums.SecretOwnerScope,
        owner_id: str,
        key_name: str,
        value: str,
    ) -> SecretMetadata:
        ref = await self._backend.store(value)
        row = models.Secret(
            id=new_ulid(),
            owner_scope=scope,
            owner_id=owner_id,
            key_name=key_name,
            backend=enums.SecretBackend(self._backend.name),
            backend_ref=ref.to_str(),
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError as exc:
            await self._backend.delete(ref)  # don't orphan ciphertext
            raise SecretVaultError(
                "secret.duplicate",
                f"a secret already exists at scope={scope.value} owner={owner_id} key={key_name!r}",
            ) from exc
        logger.info(
            "secret.stored",
            secret_id=row.id,
            scope=scope.value,
            key_name=key_name,
            backend=self._backend.name,
        )
        return _metadata(row)

    async def rotate(self, db: AsyncSession, *, secret_id: str, new_value: str) -> SecretMetadata:
        row = await self._fetch_row(db, secret_id)
        new_ref = await self._backend.store(new_value)
        old_ref = SecretRef.parse(row.backend_ref)
        row.backend_ref = new_ref.to_str()
        row.rotated_at = datetime.now(tz=row.created_at.tzinfo)
        await db.flush()
        try:
            await self._backend.delete(old_ref)
        except SecretBackendError:
            logger.warning(
                "secret.rotate.old_blob_orphaned",
                secret_id=secret_id,
                old_locator=old_ref.locator,
            )
        logger.info("secret.rotated", secret_id=secret_id)
        return _metadata(row)

    async def delete(self, db: AsyncSession, *, secret_id: str) -> None:
        row = await self._fetch_row(db, secret_id)
        ref = SecretRef.parse(row.backend_ref)
        await db.delete(row)
        await db.flush()
        try:
            await self._backend.delete(ref)
        except SecretBackendError:
            logger.warning(
                "secret.delete.blob_orphaned",
                secret_id=secret_id,
                locator=ref.locator,
            )
        logger.info("secret.deleted", secret_id=secret_id)

    # ──────────────────────────────────────────────────────── read ──

    async def read(
        self,
        db: AsyncSession,
        *,
        secret_id: str,
        purpose: str,
        actor_id: str,
    ) -> str:
        row = await self._fetch_row(db, secret_id)
        ref = SecretRef.parse(row.backend_ref)
        try:
            value = await self._backend.read(ref)
        except SecretBackendError as exc:
            raise SecretVaultError("secret.read_failed", str(exc)) from exc

        await self._audit.log(
            AuditEvent(
                action="secret.read",
                actor_type=enums.AuditActorType.AGENT_SESSION,
                actor_id=actor_id,
                outcome=enums.AuditOutcome.OK,
                scope={"secret_id": secret_id},
                subject={"key_name": row.key_name, "owner_scope": row.owner_scope.value},
                payload={"purpose": purpose, "backend": self._backend.name},
            )
        )
        logger.info(
            "secret.read",
            secret_id=secret_id,
            actor_id=actor_id,
            purpose=purpose,
            backend=self._backend.name,
        )
        return value

    async def list(
        self,
        db: AsyncSession,
        *,
        scope: enums.SecretOwnerScope | None = None,
        owner_id: str | None = None,
    ) -> list[SecretMetadata]:
        stmt = select(models.Secret)
        if scope is not None:
            stmt = stmt.where(models.Secret.owner_scope == scope)
        if owner_id is not None:
            stmt = stmt.where(models.Secret.owner_id == owner_id)
        stmt = stmt.order_by(models.Secret.created_at.desc())
        rows = (await db.execute(stmt)).scalars().all()
        return [_metadata(row) for row in rows]

    async def get_metadata(self, db: AsyncSession, *, secret_id: str) -> SecretMetadata:
        return _metadata(await self._fetch_row(db, secret_id))

    # ──────────────────────────────────────────────────── internals ──

    async def _fetch_row(self, db: AsyncSession, secret_id: str) -> models.Secret:
        row = (
            await db.execute(select(models.Secret).where(models.Secret.id == secret_id))
        ).scalar_one_or_none()
        if row is None:
            raise SecretVaultError("secret.not_found", f"secret_id={secret_id}")
        return row
