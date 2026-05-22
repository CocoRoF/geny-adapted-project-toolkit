import structlog
from aiohttp import web

from gapt_runtime import __version__
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
    app = web.Application()
    app[SETTINGS_KEY] = settings
    app.router.add_get("/health", handle_health)
    app.router.add_get("/info", handle_info)
    return app
