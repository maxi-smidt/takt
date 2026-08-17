"""Two-step confirmation endpoints (used for destructive run edits and shutdown)."""

from __future__ import annotations

from aiohttp import web

from takt.web.routes.common import RUNTIME_KEY
from takt.web.runtime import json_body


async def prepare_confirmation(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    operation = body.get("operation")
    run_id = body.get("run_id")
    delta_ms = body.get("delta_ms", 0)
    if not isinstance(operation, str):
        raise web.HTTPBadRequest(text="Bestätigung fehlt.")
    if run_id is not None and not isinstance(run_id, int):
        raise web.HTTPBadRequest(text="Ungültiger Lauf.")
    if not isinstance(delta_ms, int):
        raise web.HTTPBadRequest(text="Ungültige Korrektur.")
    try:
        response = runtime.prepare_confirmation(
            operation,
            run_id=run_id,
            delta_ms=delta_ms,
        )
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response(response)


async def confirm(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    try:
        response = await runtime.confirm(request.match_info["token"])
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    except RuntimeError as error:
        raise web.HTTPInternalServerError(text=str(error)) from error
    return web.json_response(response)
