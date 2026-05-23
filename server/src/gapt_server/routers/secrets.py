"""Secret-vault routes.

- `POST   /api/secrets`              — store
- `GET    /api/secrets`              — list (metadata only)
- `GET    /api/secrets/{sid}`        — single metadata
- `POST   /api/secrets/{sid}/rotate` — replace value
- `DELETE /api/secrets/{sid}`        — delete

Plaintext is NEVER returned by any endpoint. Reads happen inside
`SecretVault.read(...)` from server-side callers (agent sandbox boot,
hook injection, etc.) and always emit an audit event.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  — pydantic resolves at runtime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gapt_server.container import get_app_settings, get_audit_sink, get_db_session
from gapt_server.db import enums, models  # noqa: TC001  — pydantic + FastAPI runtime introspection
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.secrets.backend import EncryptedSqliteBackend
from gapt_server.domains.secrets.vault import (
    SecretMetadata,
    SecretVault,
    SecretVaultError,
)
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


# Module-level singleton; swappable in tests via `set_vault`.
_VAULT: SecretVault | None = None


def get_vault(
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> SecretVault:
    global _VAULT  # noqa: PLW0603
    if _VAULT is None:
        backend = EncryptedSqliteBackend(
            db_path=settings.vault_sqlite_path,
            master_key=settings.vault_master_key,
        )
        _VAULT = SecretVault(backend, audit_sink=audit_sink)
    return _VAULT


def set_vault(vault: SecretVault) -> None:
    global _VAULT  # noqa: PLW0603
    _VAULT = vault


router = APIRouter(prefix="/api/secrets", tags=["secrets"])


# ────────────────────────────────────────────────────── DTOs ──


class StoreSecretRequest(BaseModel):
    scope: enums.SecretOwnerScope
    owner_id: str = Field(min_length=1, max_length=26)
    key_name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)


class RotateSecretRequest(BaseModel):
    value: str = Field(min_length=1)


class SecretView(BaseModel):
    id: str
    scope: enums.SecretOwnerScope
    owner_id: str
    key_name: str
    backend: enums.SecretBackend
    created_at: datetime
    rotated_at: datetime | None = None

    @classmethod
    def from_metadata(cls, md: SecretMetadata) -> SecretView:
        return cls(
            id=md.id,
            scope=md.owner_scope,
            owner_id=md.owner_id,
            key_name=md.key_name,
            backend=md.backend,
            created_at=md.created_at,
            rotated_at=md.rotated_at,
        )


# ─────────────────────────────────────────────────── endpoints ──


@router.post("", response_model=SecretView, status_code=status.HTTP_201_CREATED)
async def store_secret(
    payload: StoreSecretRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: models.User = Depends(get_current_user),  # noqa: B008
) -> SecretView:
    try:
        md = await vault.store(
            db,
            scope=payload.scope,
            owner_id=payload.owner_id,
            key_name=payload.key_name,
            value=payload.value,
        )
        await db.commit()
    except SecretVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    return SecretView.from_metadata(md)


@router.get("", response_model=list[SecretView])
async def list_secrets(
    scope: enums.SecretOwnerScope | None = None,
    owner_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: models.User = Depends(get_current_user),  # noqa: B008
) -> list[SecretView]:
    items = await vault.list(db, scope=scope, owner_id=owner_id)
    return [SecretView.from_metadata(md) for md in items]


@router.get("/{secret_id}", response_model=SecretView)
async def get_secret_metadata(
    secret_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: models.User = Depends(get_current_user),  # noqa: B008
) -> SecretView:
    try:
        md = await vault.get_metadata(db, secret_id=secret_id)
    except SecretVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    return SecretView.from_metadata(md)


@router.post("/{secret_id}/rotate", response_model=SecretView)
async def rotate_secret(
    secret_id: str,
    payload: RotateSecretRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: models.User = Depends(get_current_user),  # noqa: B008
) -> SecretView:
    try:
        md = await vault.rotate(db, secret_id=secret_id, new_value=payload.value)
        await db.commit()
    except SecretVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    return SecretView.from_metadata(md)


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    _user: models.User = Depends(get_current_user),  # noqa: B008
) -> None:
    try:
        await vault.delete(db, secret_id=secret_id)
        await db.commit()
    except SecretVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
