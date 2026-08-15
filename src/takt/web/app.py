from __future__ import annotations

import asyncio
import csv
import ipaddress
import os
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from takt import __version__
from takt.static_assets import require_static_assets
from takt.web.runtime import (
    MaintenanceLeaseMismatch,
    MaintenanceUnavailable,
    WebRuntime,
    json_body,
    parse_chart_days,
)

STATIC_ROOT = Path(__file__).with_name("static")
RUNTIME_KEY = web.AppKey("runtime", WebRuntime)
EXPORT_CHUNK_SIZE = 64 * 1024


def create_web_app(runtime: WebRuntime) -> web.Application:
    app = web.Application(client_max_size=128 * 1024, middlewares=[same_origin_requests])
    app[RUNTIME_KEY] = runtime
    app.on_response_prepare.append(_set_security_headers)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/database/export", database_export, allow_head=False)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/action", action)
    app.router.add_get("/internal/maintenance", maintenance_status)
    app.router.add_post("/internal/maintenance/acquire", maintenance_acquire)
    app.router.add_post("/internal/maintenance/release", maintenance_release)
    app.router.add_post("/internal/run-curation", run_curation)
    app.router.add_post("/api/audio/settings", audio_settings)
    app.router.add_post("/api/audio/scan", audio_scan)
    app.router.add_post("/api/audio/connect", audio_connect)
    app.router.add_post("/api/audio/forget", audio_forget)
    app.router.add_post("/api/audio/test", audio_test)
    app.router.add_post("/api/confirmations", prepare_confirmation)
    app.router.add_post("/api/confirmations/{token}", confirm)
    if (STATIC_ROOT / "assets").is_dir():
        app.router.add_static("/assets", STATIC_ROOT / "assets", append_version=True)
    if STATIC_ROOT.is_dir():
        app.router.add_static("/static", STATIC_ROOT, append_version=True)
    return app


async def _set_security_headers(
    request: web.Request,
    response: web.StreamResponse,
) -> None:
    del request
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"


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


async def history(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    chart_days = parse_chart_days(
        request.query.get("days"),
        runtime.config.display.chart_default_days,
    )
    return web.json_response(runtime.history_payload(chart_days))


async def database_export(request: web.Request) -> web.StreamResponse:
    _require_same_origin(request)
    runtime = request.app[RUNTIME_KEY]

    export_format = request.query.get("format")
    if export_format not in {"db", "csv"}:
        raise web.HTTPBadRequest(text="Exportformat muss db oder csv sein.")
    if runtime.data_export_blocked:
        raise web.HTTPConflict(
            text="Datenexport ist während einer laufenden Zeitmessung nicht möglich."
        )
    if runtime.maintenance_status()["held"]:
        raise web.HTTPConflict(text="Datenexport ist während der Wartung nicht möglich.")

    suffix = ".db" if export_format == "db" else ".csv"
    prefix = "takt-" if export_format == "db" else "takt-runs-"
    filename = f"{prefix}{date.today().isoformat()}{suffix}"
    content_type = (
        "application/vnd.sqlite3"
        if export_format == "db"
        else "text/csv; charset=utf-8"
    )
    with TemporaryDirectory(prefix="takt-export-") as directory:
        artifact = Path(directory) / filename
        try:
            if export_format == "db":
                await asyncio.to_thread(runtime.repository.backup_to, artifact)
            else:
                await asyncio.to_thread(runtime.repository.export_runs_csv, artifact)
        except (OSError, csv.Error, sqlite3.Error) as error:
            raise web.HTTPInternalServerError(
                text="Datenexport konnte nicht erstellt werden."
            ) from error
        return await _stream_export(request, artifact, filename, content_type)


async def _stream_export(
    request: web.Request,
    artifact: Path,
    filename: str,
    content_type: str,
) -> web.StreamResponse:
    response = web.StreamResponse(
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Content-Type": content_type,
        }
    )
    response.content_length = (await asyncio.to_thread(artifact.stat)).st_size
    await response.prepare(request)
    try:
        stream = await asyncio.to_thread(artifact.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, EXPORT_CHUNK_SIZE):
                await response.write(chunk)
            await response.write_eof()
        finally:
            await asyncio.to_thread(stream.close)
    except ConnectionResetError:
        # The temporary directory is still cleaned by database_export().
        return response
    return response

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


async def run_curation(request: web.Request) -> web.Response:
    _require_loopback(request)
    body = await json_body(request)
    command_id = body.get("command_id")
    operation = body.get("operation")
    run_id = body.get("run_id")
    expected_updated_at = body.get("expected_updated_at")
    desired_added_time_ms = body.get("desired_added_time_ms")
    if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
        raise web.HTTPBadRequest(text="A valid command_id is required.")
    if operation not in {"adjust_added_time", "delete"}:
        raise web.HTTPBadRequest(text="Unsupported run curation operation.")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
        raise web.HTTPBadRequest(text="A valid run_id is required.")
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        raise web.HTTPBadRequest(text="expected_updated_at is required.")
    if desired_added_time_ms is not None and (
        not isinstance(desired_added_time_ms, int) or isinstance(desired_added_time_ms, bool)
    ):
        raise web.HTTPBadRequest(text="desired_added_time_ms must be an integer.")
    try:
        result = request.app[RUNTIME_KEY].apply_remote_curation(
            command_id=command_id,
            operation=operation,
            run_id=run_id,
            expected_updated_at=expected_updated_at,
            desired_added_time_ms=desired_added_time_ms,
        )
    except ValueError as error:
        raise web.HTTPConflict(text=str(error)) from error
    return web.json_response({"ok": True, "result": result})


async def maintenance_status(request: web.Request) -> web.Response:
    _require_loopback(request)
    return web.json_response({"maintenance": request.app[RUNTIME_KEY].maintenance_status()})


async def maintenance_acquire(request: web.Request) -> web.Response:
    _require_loopback(request)
    body = await json_body(request)
    request_id = body.get("request_id")
    owner = body.get("owner")
    reason = body.get("reason", "")
    ttl_seconds = body.get("ttl_seconds", 30)
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise web.HTTPBadRequest(text="A request_id of at most 128 characters is required.")
    if not isinstance(owner, str) or not owner or len(owner) > 80:
        raise web.HTTPBadRequest(text="An owner of at most 80 characters is required.")
    if not isinstance(reason, str) or len(reason) > 200:
        raise web.HTTPBadRequest(text="Maintenance reason may contain at most 200 characters.")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise web.HTTPBadRequest(text="ttl_seconds must be an integer.")
    try:
        result = request.app[RUNTIME_KEY].acquire_maintenance(
            request_id=request_id,
            owner=owner,
            reason=reason,
            ttl_seconds=ttl_seconds,
        )
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    except MaintenanceUnavailable as error:
        return web.json_response(
            {
                "acquired": False,
                "error": str(error),
                "maintenance": request.app[RUNTIME_KEY].maintenance_status(),
            },
            status=409,
        )
    return web.json_response(result)


async def maintenance_release(request: web.Request) -> web.Response:
    _require_loopback(request)
    body = await json_body(request)
    lease_token = body.get("lease_token")
    if not isinstance(lease_token, str) or not lease_token or len(lease_token) > 128:
        raise web.HTTPBadRequest(text="A valid lease_token is required.")
    runtime = request.app[RUNTIME_KEY]
    try:
        released = runtime.release_maintenance(lease_token)
    except MaintenanceLeaseMismatch as error:
        raise web.HTTPForbidden(text=str(error)) from error
    return web.json_response({"released": released, "maintenance": runtime.maintenance_status()})


async def audio_settings(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    enabled = body.get("enabled")
    output = body.get("output")
    delay_milliseconds = body.get("delay_milliseconds")
    device_address = body.get("device_address")
    device_name = body.get("device_name")
    if not isinstance(enabled, bool) or not isinstance(output, str):
        raise web.HTTPBadRequest(text="Ungültige Audio-Einstellung.")
    if not isinstance(delay_milliseconds, int) or isinstance(delay_milliseconds, bool):
        raise web.HTTPBadRequest(text="Ungültige Wartezeit.")
    if device_address is not None and not isinstance(device_address, str):
        raise web.HTTPBadRequest(text="Ungültiges Bluetooth-Gerät.")
    if device_name is not None and not isinstance(device_name, str):
        raise web.HTTPBadRequest(text="Ungültiger Gerätename.")
    try:
        system = await runtime.update_audio_settings(
            enabled=enabled,
            output=output,
            delay_milliseconds=delay_milliseconds,
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


async def audio_forget(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    body = await json_body(request)
    address = body.get("address")
    if not isinstance(address, str):
        raise web.HTTPBadRequest(text="Bluetooth-Gerät fehlt.")
    try:
        system = await runtime.forget_audio_device(address)
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


def _require_loopback(request: web.Request) -> None:
    transport = request.transport
    peer = transport.get_extra_info("peername") if transport is not None else None
    address = peer[0] if isinstance(peer, tuple) and peer else None
    if not isinstance(address, str) or not _is_loopback_address(address):
        raise web.HTTPForbidden(text="Maintenance control is only available on localhost.")


@web.middleware
async def same_origin_requests(request: web.Request, handler) -> web.StreamResponse:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.path.startswith("/api/"):
        _require_same_origin(request)
    return await handler(request)


def _require_same_origin(request: web.Request) -> None:
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        raise web.HTTPForbidden(text="Cross-site requests are not allowed.")
    origin = request.headers.get("Origin")
    if not origin:
        return
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request.host.lower():
        raise web.HTTPForbidden(text="Cross-origin requests are not allowed.")


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.partition("%")[0]).is_loopback
    except ValueError:
        return False
