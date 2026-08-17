"""The local Pi/desktop web app: routing only.

Handlers live in `takt.web.routes`, split by concern (core status/realtime,
run history/export, maintenance leases, audio devices, confirmations) plus
a shared `security` module for the same-origin/loopback guards. This module
wires them together and stays a thin router.
"""

from __future__ import annotations

from aiohttp import web

from takt.web.routes import audio, confirmations, core, maintenance, runs
from takt.web.routes.common import RUNTIME_KEY, STATIC_ROOT
from takt.web.routes.security import (
    _is_loopback_address,
    _set_security_headers,
    same_origin_requests,
)
from takt.web.runtime import WebRuntime

__all__ = [
    "RUNTIME_KEY",
    "STATIC_ROOT",
    "WebRuntime",
    "_is_loopback_address",
    "create_web_app",
]


def create_web_app(runtime: WebRuntime) -> web.Application:
    app = web.Application(client_max_size=128 * 1024, middlewares=[same_origin_requests])
    app[RUNTIME_KEY] = runtime
    app.on_response_prepare.append(_set_security_headers)
    app.router.add_get("/", core.index)
    app.router.add_get("/health", core.health)
    app.router.add_get("/api/bootstrap", core.bootstrap)
    app.router.add_get("/api/history", runs.history)
    app.router.add_get("/api/database/export", runs.database_export, allow_head=False)
    app.router.add_get("/api/events", core.events)
    app.router.add_post("/api/action", core.action)
    app.router.add_get("/internal/maintenance", maintenance.maintenance_status)
    app.router.add_post("/internal/maintenance/acquire", maintenance.maintenance_acquire)
    app.router.add_post("/internal/maintenance/release", maintenance.maintenance_release)
    app.router.add_post("/internal/run-curation", runs.run_curation)
    app.router.add_post("/api/audio/settings", audio.audio_settings)
    app.router.add_post("/api/audio/scan", audio.audio_scan)
    app.router.add_post("/api/audio/connect", audio.audio_connect)
    app.router.add_post("/api/audio/forget", audio.audio_forget)
    app.router.add_post("/api/audio/test", audio.audio_test)
    app.router.add_post("/api/confirmations", confirmations.prepare_confirmation)
    app.router.add_post("/api/confirmations/{token}", confirmations.confirm)
    if (STATIC_ROOT / "assets").is_dir():
        app.router.add_static("/assets", STATIC_ROOT / "assets", append_version=True)
    if STATIC_ROOT.is_dir():
        app.router.add_static("/static", STATIC_ROOT, append_version=True)
    return app
