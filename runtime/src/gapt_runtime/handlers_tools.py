"""Tool-dispatch HTTP endpoints (added in M1-E2 Cycle 2.4).

- ``GET  /tools/list``   — manifest of registered tools.
- ``POST /tools/call``   — invoke a tool by name.

Both are JWT-protected by the existing ``jwt_middleware``. The
response shape mirrors what the MCP bridge expects (Cycle 2.3's
``DaemonClient``):

- ``GET /tools/list``  → ``{"tools": [{"name", "description", "input_schema"}, …]}``
- ``POST /tools/call`` → ``{"ok": bool, "result"?, "error"?}``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from aiohttp import web
from pydantic import BaseModel, Field, ValidationError

from gapt_runtime.tools import (
    ToolError,
    ToolInvocation,
    ToolRegistry,
    build_default_registry,
)

if TYPE_CHECKING:
    from gapt_runtime.settings import DaemonSettings

logger = structlog.get_logger(__name__)

# Pinned in daemon.py at startup; tests can swap their own instance.
REGISTRY_KEY = web.AppKey[ToolRegistry]("gapt.tool_registry")


class CallToolRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _registry(request: web.Request) -> ToolRegistry:
    registry = request.app.get(REGISTRY_KEY)
    if registry is None:
        registry = build_default_registry()
        request.app[REGISTRY_KEY] = registry
    return registry


def _settings(request: web.Request) -> DaemonSettings:
    # Imported lazily to avoid a cycle with daemon.py at module load
    # time (daemon.py imports this module).
    from gapt_runtime.daemon import SETTINGS_KEY  # noqa: PLC0415

    return request.app[SETTINGS_KEY]


async def handle_tools_list(request: web.Request) -> web.Response:
    registry = _registry(request)
    return web.json_response({"tools": registry.list_specs()})


async def handle_tools_call(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return web.json_response(
            {
                "ok": False,
                "error": {
                    "code": "exec.tool.invalid_input",
                    "message": "request body must be JSON",
                },
            },
            status=400,
        )
    try:
        req = CallToolRequest.model_validate(body)
    except ValidationError as exc:
        return web.json_response(
            {
                "ok": False,
                "error": {
                    "code": "exec.tool.invalid_input",
                    "message": exc.errors()[0]["msg"],
                },
            },
            status=400,
        )

    registry = _registry(request)
    tool = registry.get(req.name)
    if tool is None:
        return web.json_response(
            {
                "ok": False,
                "error": {
                    "code": "exec.tool.unknown",
                    "message": f"tool {req.name!r} not registered",
                },
            },
            status=404,
        )

    settings = _settings(request)
    invocation = ToolInvocation(
        name=req.name,
        arguments=req.arguments,
        workspace_root=str(settings.workspace_root),
    )

    try:
        result = await tool.execute(invocation)
    except ToolError as exc:
        logger.info(
            "tools.call.denied",
            name=req.name,
            code=exc.code,
            message=str(exc)[:200],
        )
        # ToolError is a domain-level refusal, not a transport bug —
        # return 200 with ok=false so the bridge keeps the
        # `isError=true` formatting flow (Cycle 2.3 contract).
        return web.json_response(
            {
                "ok": False,
                "error": {"code": exc.code, "message": str(exc)},
            }
        )
    except Exception:
        logger.exception("tools.call.crashed", name=req.name)
        return web.json_response(
            {
                "ok": False,
                "error": {
                    "code": "exec.tool.crashed",
                    "message": f"tool {req.name!r} crashed; see daemon log",
                },
            },
            status=500,
        )

    logger.info("tools.call.ok", name=req.name)
    return web.json_response(
        {
            "ok": True,
            "result": result.content,
            "metadata": result.metadata or {},
        }
    )
