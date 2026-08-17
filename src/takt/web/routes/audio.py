"""Bluetooth audio device settings, scanning, and pairing endpoints."""

from __future__ import annotations

from aiohttp import web

from takt.web.routes.common import RUNTIME_KEY
from takt.web.runtime import json_body


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
