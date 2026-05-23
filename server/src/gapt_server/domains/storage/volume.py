"""VolumeManager protocol + 2 implementations.

`VolumeRef` carries the bits the runtime needs to mount the volume —
filer URL + bucket-relative path. Workspace creation hands these to
``SandboxCreateSpec.env`` so the runtime's entrypoint script can wire
the FUSE mount.

Invariants enforced inside the manager (no config can weaken them):
- Workspace ID must be a 26-char ULID — no path traversal sneaks.
- The bucket-relative path never escapes the configured bucket root.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class VolumeManagerError(RuntimeError):
    """Operational failure (or an invariant violation that callers
    should treat as a hard stop). Carries a stable code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VolumeRef:
    workspace_id: str
    bucket: str
    path: str  # bucket-relative, leading slash
    filer_url: str

    def to_env(self) -> dict[str, str]:
        """Variables the runtime entrypoint reads to mount the volume."""
        return {
            "GAPT_SEAWEED_FILER_URL": self.filer_url,
            "GAPT_SEAWEED_BUCKET": self.bucket,
            "GAPT_SEAWEED_PATH": self.path,
            "GAPT_SEAWEED_WORKSPACE": self.workspace_id,
        }


class VolumeManager(Protocol):
    name: str

    async def create(self, *, workspace_id: str) -> VolumeRef: ...

    async def delete(self, ref: VolumeRef) -> None: ...

    async def exists(self, ref: VolumeRef) -> bool: ...


# ──────────────────────────────────────────────────── helpers ──


def _validate_workspace_id(workspace_id: str) -> None:
    if not _ULID_RE.match(workspace_id):
        raise VolumeManagerError(
            "volume.invalid_workspace_id",
            f"workspace_id={workspace_id!r} is not a valid 26-char ULID",
        )


# ─────────────────────────────────────────── in-memory manager ──


@dataclass
class _MemEntry:
    ref: VolumeRef
    created_at: float


class InMemoryVolumeManager:
    name = "memory"

    def __init__(self, *, filer_url: str = "memory://", bucket: str = "gapt") -> None:
        self._filer_url = filer_url
        self._bucket = bucket
        self._entries: dict[str, _MemEntry] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, workspace_id: str) -> VolumeRef:
        _validate_workspace_id(workspace_id)
        async with self._lock:
            if workspace_id in self._entries:
                raise VolumeManagerError(
                    "volume.already_exists",
                    f"workspace_id={workspace_id} already has a volume",
                )
            ref = VolumeRef(
                workspace_id=workspace_id,
                bucket=self._bucket,
                path=f"/{workspace_id}",
                filer_url=self._filer_url,
            )
            self._entries[workspace_id] = _MemEntry(ref=ref, created_at=time.time())
        logger.info("volume.created", workspace_id=workspace_id, backend=self.name)
        return ref

    async def delete(self, ref: VolumeRef) -> None:
        async with self._lock:
            self._entries.pop(ref.workspace_id, None)
        logger.info("volume.deleted", workspace_id=ref.workspace_id, backend=self.name)

    async def exists(self, ref: VolumeRef) -> bool:
        async with self._lock:
            return ref.workspace_id in self._entries


# ─────────────────────────────────────────── filer (HTTP) manager ──


class FilerVolumeManager:
    """Backed by the SeaweedFS filer HTTP API.

    A 'volume' is just a top-level directory under
    ``/buckets/<bucket>/`` keyed by the workspace ULID. Filer's
    `PUT ?op=mkdir` / `DELETE ?recursive=true` cover create/delete.
    """

    name = "seaweed_filer"

    def __init__(
        self,
        *,
        filer_url: str,
        bucket: str = "gapt",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if not filer_url:
            raise VolumeManagerError(
                "volume.filer_url_missing",
                "FilerVolumeManager needs a non-empty filer_url",
            )
        self._filer = filer_url.rstrip("/")
        self._bucket = bucket
        self._timeout = timeout_s
        self._client_factory = client_factory

    def _make_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=self._timeout)

    def _ref(self, workspace_id: str) -> VolumeRef:
        return VolumeRef(
            workspace_id=workspace_id,
            bucket=self._bucket,
            path=f"/{workspace_id}",
            filer_url=self._filer,
        )

    def _path_url(self, ref: VolumeRef) -> str:
        # Filer treats the path verbatim; we namespace per-bucket so
        # multiple GAPT installs can share a SeaweedFS cluster.
        return f"{self._filer}/buckets/{ref.bucket}{ref.path}/"

    async def create(self, *, workspace_id: str) -> VolumeRef:
        _validate_workspace_id(workspace_id)
        ref = self._ref(workspace_id)
        async with self._make_client() as client:
            response = await client.post(
                self._path_url(ref),
                params={"op": "mkdir"},
            )
        if response.status_code >= 400:
            raise VolumeManagerError(
                "volume.filer_failed",
                f"filer mkdir {self._path_url(ref)} returned {response.status_code}: "
                f"{response.text[:200]}",
            )
        logger.info(
            "volume.created",
            workspace_id=workspace_id,
            backend=self.name,
            path=ref.path,
        )
        return ref

    async def delete(self, ref: VolumeRef) -> None:
        async with self._make_client() as client:
            response = await client.delete(
                self._path_url(ref),
                params={"recursive": "true"},
            )
        if response.status_code >= 400 and response.status_code != 404:
            raise VolumeManagerError(
                "volume.filer_failed",
                f"filer delete {self._path_url(ref)} returned {response.status_code}: "
                f"{response.text[:200]}",
            )
        logger.info(
            "volume.deleted",
            workspace_id=ref.workspace_id,
            backend=self.name,
            path=ref.path,
        )

    async def exists(self, ref: VolumeRef) -> bool:
        async with self._make_client() as client:
            response = await client.get(self._path_url(ref))
        return 200 <= response.status_code < 300
