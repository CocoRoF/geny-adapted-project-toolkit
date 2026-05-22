from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="dev",
        log_level="WARNING",
        log_format="console",
        session_secret="test-secret",
        daemon_jwt_secret="test-daemon",
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
