"""Phase N.2.2 — listing endpoint smoke."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.settings import Settings


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # auth_enabled=False so we don't need a session cookie — same
    # pattern as `tests/sessions/test_routes.py`.
    settings = Settings(auth_enabled=False)
    app = create_app(settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_scaffolds_returns_all_presets(client: AsyncClient) -> None:
    resp = await client.get("/_gapt/api/scaffolds")
    assert resp.status_code == 200
    body = resp.json()
    assert "presets" in body
    ids = [p["id"] for p in body["presets"]]
    # Same order the registry exposes.
    assert ids == [
        "fullstack_fastapi_nextjs",
        "backend_fastapi",
        "frontend_nextjs",
        "static_vite",
        "empty",
    ]


@pytest.mark.asyncio
async def test_listing_preserves_option_schema_shape(client: AsyncClient) -> None:
    """The frontend renders Step 3 of the wizard against this schema —
    any drift in field naming breaks the form contract."""
    resp = await client.get("/_gapt/api/scaffolds")
    body = resp.json()
    fullstack = next(p for p in body["presets"] if p["id"] == "fullstack_fastapi_nextjs")
    options = fullstack["option_schema"]
    assert {o["id"] for o in options} == {"primary_port", "database"}
    port = next(o for o in options if o["id"] == "primary_port")
    assert port["type"] == "integer"
    assert port["default"] == 80
    assert port["min"] == 1
    assert port["max"] == 65535
    database = next(o for o in options if o["id"] == "database")
    assert database["type"] == "enum"
    assert database["choices"] == ["none", "postgres"]
