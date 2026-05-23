import time
from collections.abc import AsyncIterator
from pathlib import Path

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer

from gapt_runtime import __version__
from gapt_runtime.auth import AUDIENCE, ISSUER
from gapt_runtime.daemon import create_app
from gapt_runtime.settings import DaemonSettings

JWT_SECRET = "smoke-test-secret-32-chars-or-more"


@pytest.fixture
def settings() -> DaemonSettings:
    return DaemonSettings(
        socket_path=Path("/tmp/test-agent.sock"),
        jwt_secret=JWT_SECRET,
        project_id="proj-abc",
        workspace_id="ws-def",
        session_id="sess-ghi",
        workspace_root=Path("/workspace"),
    )


@pytest.fixture
async def client(settings: DaemonSettings) -> AsyncIterator[TestClient]:
    app = create_app(settings)
    async with TestClient(TestServer(app)) as cli:
        yield cli


def _auth_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "sess-ghi",
            "iat": now,
            "exp": now + 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


async def test_health_returns_ok(client: TestClient) -> None:
    resp = await client.get("/health")
    assert resp.status == 200

    body = await resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


async def test_info_returns_session_context(client: TestClient) -> None:
    resp = await client.get("/info", headers=_auth_headers())
    assert resp.status == 200

    body = await resp.json()
    assert body["version"] == __version__
    assert body["project_id"] == "proj-abc"
    assert body["workspace_id"] == "ws-def"
    assert body["session_id"] == "sess-ghi"
    assert body["workspace_root"] == "/workspace"


async def test_unknown_route_returns_404(client: TestClient) -> None:
    resp = await client.get("/nope", headers=_auth_headers())
    assert resp.status == 404
