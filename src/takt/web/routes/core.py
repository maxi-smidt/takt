"""Status, bootstrap, realtime, and timer-action endpoints."""

from __future__ import annotations

import os

from aiohttp import WSMsgType, web

from takt import __version__
from takt.static_assets import require_static_assets
from takt.web.routes.common import RUNTIME_KEY, STATIC_ROOT
from takt.web.routes.security import _require_same_origin
from takt.web.runtime import json_body, parse_chart_days


async def index(request: web.Request) -> web.FileResponse:
    try:
        require_static_assets(STATIC_ROOT, "index.html", "scripts/build_web_ui.sh")
    except RuntimeError as error:
        raise web.HTTPInternalServerError(text=str(error)) from error
    return web.FileResponse(STATIC_ROOT / "index.html")


async def health(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    maintenance = runtime.maintenance_status()
    schema_row = runtime.repository.connection.execute(
        "SELECT version FROM schema_version LIMIT 1"
    ).fetchone()
    return web.json_response(
        {
            "ok": True,
            "ready": not maintenance["held"],
            "version": os.environ.get("TAKT_RELEASE_VERSION", __version__),
            "database_schema_version": int(schema_row[0]) if schema_row else None,
            "state": runtime.controller.state.value,
            "hardware_available": runtime.hardware_available,
            "maintenance": maintenance,
        }
    )


async def bootstrap(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    chart_days = parse_chart_days(
        request.query.get("days"),
        runtime.config.display.chart_default_days,
    )
    return web.json_response(
        {
            "state": runtime.state_payload(),
            "history": runtime.history_payload(chart_days),
            "system": runtime.system_payload(),
        }
    )


async def events(request: web.Request) -> web.WebSocketResponse:
    _require_same_origin(request)
    runtime = request.app[RUNTIME_KEY]
    socket = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 1024)
    await socket.prepare(request)
    runtime.add_client(socket)
    await socket.send_json({"type": "state", "data": runtime.state_payload()})
    try:
        async for message in socket:
            if message.type is WSMsgType.TEXT and message.data == "ping":
                await socket.send_str("pong")
            elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        runtime.remove_client(socket)
    return socket


async def action(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    name = body.get("action")
    if not isinstance(name, str):
        raise web.HTTPBadRequest(text="Aktion fehlt.")
    try:
        changed = runtime.dispatch_action(name)
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"ok": changed, "state": runtime.state_payload()})
