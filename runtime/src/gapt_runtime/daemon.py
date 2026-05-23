import structlog
from aiohttp import web

from gapt_runtime import __version__
from gapt_runtime.auth import jwt_middleware
from gapt_runtime.handlers import (
    handle_exec,
    handle_readfile,
    handle_writefile,
)
from gapt_runtime.handlers_pty import (
    PTY_MANAGER_KEY,
    handle_close_pty,
    handle_open_pty,
    handle_pty_ws,
)
from gapt_runtime.pty_manager import PtyManager
from gapt_runtime.settings import DaemonSettings

logger = structlog.get_logger(__name__)

SETTINGS_KEY: web.AppKey[DaemonSettings] = web.AppKey("settings", DaemonSettings)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": __version__})


async def handle_info(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    return web.json_response(
        {
            "version": __version__,
            "project_id": settings.project_id,
            "workspace_id": settings.workspace_id,
            "session_id": settings.session_id,
            "workspace_root": str(settings.workspace_root),
        }
    )


def create_app(settings: DaemonSettings) -> web.Application:
    app = web.Application(middlewares=[jwt_middleware])
    app[SETTINGS_KEY] = settings
    app[PTY_MANAGER_KEY] = PtyManager()

    app.router.add_get("/health", handle_health)
    app.router.add_get("/info", handle_info)
    app.router.add_post("/exec", handle_exec)
    app.router.add_post("/readfile", handle_readfile)
    app.router.add_post("/writefile", handle_writefile)
    app.router.add_post("/open_pty", handle_open_pty)
    app.router.add_post("/pty/{session_id}/close", handle_close_pty)
    app.router.add_get("/pty/{session_id}", handle_pty_ws)

    async def _shutdown_pty(_: web.Application) -> None:
        await app[PTY_MANAGER_KEY].aclose()

    app.on_shutdown.append(_shutdown_pty)
    return app
