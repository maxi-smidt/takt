"""Response headers, same-origin enforcement, and loopback-only guards."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from aiohttp import web


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


def _require_loopback(request: web.Request) -> None:
    transport = request.transport
    peer = transport.get_extra_info("peername") if transport is not None else None
    address = peer[0] if isinstance(peer, tuple) and peer else None
    if not isinstance(address, str) or not _is_loopback_address(address):
        raise web.HTTPForbidden(text="Maintenance control is only available on localhost.")


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.partition("%")[0]).is_loopback
    except ValueError:
        return False
