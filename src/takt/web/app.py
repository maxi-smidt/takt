from __future__ import annotations

from pathlib import Path

from aiohttp import WSMsgType, web

from takt.web.runtime import WebRuntime, json_body, parse_chart_days

STATIC_ROOT = Path(__file__).with_name("static")
RUNTIME_KEY = web.AppKey("runtime", WebRuntime)


def create_web_app(runtime: WebRuntime) -> web.Application:
    app = web.Application(client_max_size=128 * 1024)
    app[RUNTIME_KEY] = runtime
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/action", action)
    app.router.add_post("/api/audio/settings", audio_settings)
    app.router.add_post("/api/audio/scan", audio_scan)
    app.router.add_post("/api/audio/connect", audio_connect)
    app.router.add_post("/api/audio/test", audio_test)
    app.router.add_post("/api/confirmations", prepare_confirmation)
    app.router.add_post("/api/confirmations/{token}", confirm)
    app.router.add_static("/assets", STATIC_ROOT / "assets", append_version=True)
    app.router.add_static("/static", STATIC_ROOT, append_version=True)
    return app


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_ROOT / "index.html")


async def health(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    return web.json_response(
        {
            "ok": True,
            "state": runtime.controller.state.value,
            "hardware_available": runtime.hardware_available,
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


async def history(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    chart_days = parse_chart_days(
        request.query.get("days"),
        runtime.config.display.chart_default_days,
    )
    return web.json_response(runtime.history_payload(chart_days))


async def events(request: web.Request) -> web.WebSocketResponse:
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


async def audio_settings(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    enabled = body.get("enabled")
    output = body.get("output")
    delay_seconds = body.get("delay_seconds")
    device_address = body.get("device_address")
    device_name = body.get("device_name")
    if not isinstance(enabled, bool) or not isinstance(output, str):
        raise web.HTTPBadRequest(text="Ungültige Audio-Einstellung.")
    if not isinstance(delay_seconds, int | float):
        raise web.HTTPBadRequest(text="Ungültige Wartezeit.")
    if device_address is not None and not isinstance(device_address, str):
        raise web.HTTPBadRequest(text="Ungültiges Bluetooth-Gerät.")
    if device_name is not None and not isinstance(device_name, str):
        raise web.HTTPBadRequest(text="Ungültiger Gerätename.")
    try:
        system = await runtime.update_audio_settings(
            enabled=enabled,
            output=output,
            delay_seconds=float(delay_seconds),
            device_address=device_address,
            device_name=device_name,
        )
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"ok": True, "system": system})


async def audio_scan(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    try:
        system = await runtime.scan_audio_devices()
    except RuntimeError as error:
        raise web.HTTPServiceUnavailable(text=str(error)) from error
    return web.json_response({"ok": True, "system": system})


async def audio_connect(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    address = body.get("address")
    if not isinstance(address, str):
        raise web.HTTPBadRequest(text="Bluetooth-Gerät fehlt.")
    try:
        system = await runtime.connect_audio_device(address)
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    except RuntimeError as error:
        raise web.HTTPServiceUnavailable(text=str(error)) from error
    return web.json_response({"ok": True, "system": system})


async def audio_test(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    try:
        system = await runtime.test_audio()
    except RuntimeError as error:
        raise web.HTTPServiceUnavailable(text=str(error)) from error
    return web.json_response({"ok": True, "system": system})


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
