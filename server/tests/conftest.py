from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.settings import Settings


@pytest.fixture(autouse=True, scope="session")
def _override_bare_root_for_tests(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Phase M.9 — point `Settings.workspace_bare_root` away from the
    `/var/lib/gapt-bare` default for every test process.

    The production default lives outside `/workspace` on purpose
    (untracked-files bug fix, 2026-05-29) but `/var/lib` needs root to
    write. CI + local dev test runs as the regular user, so any
    pipeline that lands in `ensure_bare()` (workspace clone, e2e
    fixtures, caddy preview routes) hits `PermissionError`. We can't
    write a `tmp_path` per-test because the path is read at
    `Settings()` construction time and gets baked into the container
    at app boot — by the time a fixture would override it, the
    workspace service has already cached the wrong location.

    Solution: a session-scoped autouse fixture that sets
    `GAPT_WORKSPACE_BARE_ROOT` BEFORE any test code imports
    `Settings` (since autouse session fixtures run before parameter
    resolution). The dev wrapper `scripts/dev/server.sh` does the
    same thing for the live dev server.
    """
    import os  # noqa: PLC0415 — only imported here

    # Tests must not read the developer's `server/.env` (the dev
    # server's persistent config — DSN, Caddy URLs, log format...).
    # pydantic-settings resolves `env_file=".env"` relative to the
    # cwd, which IS `server/` when pytest runs here, so without this
    # the dev box's settings leak into every Settings() the suite
    # builds (first symptom: log_format=console breaking the
    # defaults test). Mutating model_config before any instantiation
    # disables the file for the whole session; real env vars still
    # apply.
    Settings.model_config["env_file"] = None

    bare = tmp_path_factory.mktemp("gapt-bare", numbered=False)
    os.environ["GAPT_WORKSPACE_BARE_ROOT"] = str(bare)
    # `Settings` caches via `lru_cache` on `get_settings()`. The cache
    # only matters when something imports `get_settings`; the test
    # fixtures all construct `Settings()` directly with explicit
    # kwargs. But clear it anyway so any importer post-fixture-load
    # sees the override.
    from gapt_server.settings import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return bare


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
