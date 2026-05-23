"""SeaweedFS volume lifecycle — invariants + in-memory + filer HTTP."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from gapt_server.db.ulid import new_ulid
from gapt_server.domains.storage import (
    FilerVolumeManager,
    InMemoryVolumeManager,
    VolumeManagerError,
    VolumeRef,
)

# ─────────────────────────────────────────────────── invariants ──


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "too-short",
        "01KS90000000000000000000",  # 24 chars — too short
        "../../etc/passwd",
        "01KS9000000000000000000000-suffix",  # too long
        "01ks900000000000000000000a",  # mixed case — Crockford is upper only
        "01KS9000000000000000000I00",  # contains 'I' (excluded from Crockford)
    ],
)
def test_invalid_workspace_id_refused(bad: str) -> None:
    mgr = InMemoryVolumeManager()
    with pytest.raises(VolumeManagerError) as exc:
        asyncio.run(mgr.create(workspace_id=bad))
    assert exc.value.code == "volume.invalid_workspace_id"


# ───────────────────────────────────────────── in-memory backend ──


@pytest.mark.asyncio
async def test_inmemory_create_and_delete() -> None:
    mgr = InMemoryVolumeManager()
    workspace_id = new_ulid()
    ref = await mgr.create(workspace_id=workspace_id)
    assert ref.workspace_id == workspace_id
    assert ref.path == f"/{workspace_id}"
    assert await mgr.exists(ref) is True

    env = ref.to_env()
    assert env["GAPT_SEAWEED_WORKSPACE"] == workspace_id
    assert env["GAPT_SEAWEED_BUCKET"] == "gapt"

    await mgr.delete(ref)
    assert await mgr.exists(ref) is False


@pytest.mark.asyncio
async def test_inmemory_duplicate_create_rejected() -> None:
    mgr = InMemoryVolumeManager()
    workspace_id = new_ulid()
    await mgr.create(workspace_id=workspace_id)
    with pytest.raises(VolumeManagerError) as exc:
        await mgr.create(workspace_id=workspace_id)
    assert exc.value.code == "volume.already_exists"


# ──────────────────────────────────────── filer (HTTP) backend ──


@pytest.mark.asyncio
async def test_filer_create_uses_mkdir_op() -> None:

    workspace_id = new_ulid()
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text="OK")

    transport = httpx.MockTransport(_handler)
    mgr = FilerVolumeManager(
        filer_url="http://filer.local:8888",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    ref = await mgr.create(workspace_id=workspace_id)
    assert ref.bucket == "gapt"

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.params["op"] == "mkdir"
    assert workspace_id in str(request.url)


@pytest.mark.asyncio
async def test_filer_create_failure_raises_with_code() -> None:

    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    mgr = FilerVolumeManager(
        filer_url="http://filer.local:8888",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(VolumeManagerError) as exc:
        await mgr.create(workspace_id=new_ulid())
    assert exc.value.code == "volume.filer_failed"


@pytest.mark.asyncio
async def test_filer_delete_recursive() -> None:

    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)
    mgr = FilerVolumeManager(
        filer_url="http://filer.local:8888",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    ref = VolumeRef(
        workspace_id=new_ulid(),
        bucket="gapt",
        path="/01HX",
        filer_url="http://filer.local:8888",
    )
    await mgr.delete(ref)
    assert requests[0].method == "DELETE"
    assert requests[0].url.params["recursive"] == "true"


@pytest.mark.asyncio
async def test_filer_delete_404_is_idempotent() -> None:

    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="not found"))
    mgr = FilerVolumeManager(
        filer_url="http://filer.local:8888",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    # Should NOT raise — deleting an already-gone volume is OK.
    await mgr.delete(
        VolumeRef(
            workspace_id=new_ulid(),
            bucket="gapt",
            path="/x",
            filer_url="http://filer.local:8888",
        )
    )


@pytest.mark.asyncio
async def test_filer_exists() -> None:

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if "alive" in str(req.url) else 404)

    transport = httpx.MockTransport(_handler)
    mgr = FilerVolumeManager(
        filer_url="http://filer.local:8888",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    alive = VolumeRef(
        workspace_id=new_ulid(),
        bucket="gapt",
        path="/alive",
        filer_url="http://filer.local:8888",
    )
    dead = VolumeRef(
        workspace_id=new_ulid(),
        bucket="gapt",
        path="/dead",
        filer_url="http://filer.local:8888",
    )
    assert await mgr.exists(alive) is True
    assert await mgr.exists(dead) is False


def test_filer_constructor_rejects_empty_url() -> None:
    with pytest.raises(VolumeManagerError) as exc:
        FilerVolumeManager(filer_url="")
    assert exc.value.code == "volume.filer_url_missing"
