"""Maintenance-lease endpoints (loopback-only, used by the Fleet agent)."""

from __future__ import annotations

from aiohttp import web

from takt.web.routes.common import RUNTIME_KEY
from takt.web.routes.security import _require_loopback
from takt.web.runtime import MaintenanceLeaseMismatch, MaintenanceUnavailable, json_body


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
