from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import math
import re
import secrets
import sqlite3
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from aiohttp import BodyPartReader, web

from takt.fleet_actions import ALLOWED_ACTIONS
from takt.protocol import PROTOCOL_VERSION
from takt.registry.auth import COOKIE_NAME, AdminAuth, CsrfError, SessionError
from takt.registry.bundled_release import ReleaseValidationError, validate_release_archive
from takt.registry.deployment import (
    DeploymentCredentials,
    DeploymentManager,
    validate_hostname,
    validate_registry_url,
)
from takt.registry.storage import RegistryStore, utc_iso
from takt.static_assets import require_static_assets

STATIC_ROOT = Path(__file__).with_name("static")
STORE_KEY = web.AppKey("registry_store", RegistryStore)
AUTH_KEY = web.AppKey("registry_auth", AdminAuth)
DEPLOYMENTS_KEY = web.AppKey("registry_deployments", DeploymentManager)
SECURE_COOKIES_KEY = web.AppKey("registry_secure_cookies", bool)
LOGIN_LIMITER_KEY = web.AppKey("registry_login_limiter", object)
LOGIN_SEMAPHORE_KEY = web.AppKey("registry_login_semaphore", asyncio.Semaphore)
MIRROR_SEMAPHORE_KEY = web.AppKey("registry_mirror_semaphore", asyncio.Semaphore)
MIRROR_ACTIVE_KEY = web.AppKey("registry_mirror_active", set)
MIRROR_LAST_ATTEMPT_KEY = web.AppKey("registry_mirror_last_attempt", dict)
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f-]{16,64}$")
DEVICE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
JSON_LIMIT = 64 * 1024
MAX_DIAGNOSTICS_BYTES = 8 * 1024 * 1024
DEPLOYMENT_EVENT_POLL_SECONDS = 2
LOGGER = logging.getLogger(__name__)


class LoginLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def failed(self, address: str) -> float:
        now = time.monotonic()
        failures = self._failures[address]
        while failures and now - failures[0] > 300:
            failures.popleft()
        failures.append(now)
        while len(self._failures) > 1024:
            self._failures.pop(next(iter(self._failures)))
        return self.delay(address)

    def delay(self, address: str) -> float:
        now = time.monotonic()
        failures = self._failures.get(address)
        if not failures:
            return 0.0
        while failures and now - failures[0] > 300:
            failures.popleft()
        if not failures:
            self._failures.pop(address, None)
            return 0.0
        return min(2.0, 0.05 * (2 ** min(len(failures) - 1, 6)))

    def succeeded(self, address: str) -> None:
        self._failures.pop(address, None)


@web.middleware
async def security_headers(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        response = await handler(request)
    except web.HTTPException as error:
        _set_security_headers(request, error)
        raise
    _set_security_headers(request, response)
    return response


def create_registry_app(
    store: RegistryStore, auth: AdminAuth, *, secure_cookies: bool = False
) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024, middlewares=[security_headers])
    app[STORE_KEY] = store
    app[AUTH_KEY] = auth
    app[DEPLOYMENTS_KEY] = DeploymentManager(store)
    app[SECURE_COOKIES_KEY] = secure_cookies
    app[LOGIN_LIMITER_KEY] = LoginLimiter()
    app[LOGIN_SEMAPHORE_KEY] = asyncio.Semaphore(2)
    app[MIRROR_SEMAPHORE_KEY] = asyncio.Semaphore(2)
    app[MIRROR_ACTIVE_KEY] = set()
    app[MIRROR_LAST_ATTEMPT_KEY] = {}
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/session", session_status)
    app.router.add_post("/api/session", login)
    app.router.add_delete("/api/session", logout)
    app.router.add_get("/api/devices", devices)
    app.router.add_get("/api/deployments", deployments)
    app.router.add_post("/api/deployments", create_deployment)
    app.router.add_get("/api/deployments/{deployment_id}", deployment)
    app.router.add_get("/api/deployments/{deployment_id}/events", deployment_events)
    app.router.add_post("/api/deployments/{deployment_id}/host-key", deployment_host_key)
    app.router.add_post("/api/deployments/{deployment_id}/credentials", deployment_credentials)
    app.router.add_post("/api/deployments/{deployment_id}/retry", deployment_retry)
    app.router.add_post("/api/deployments/{deployment_id}/cancel", deployment_cancel)
    app.router.add_post("/api/enrollment-codes", enrollment_code)
    app.router.add_get("/api/releases", releases)
    app.router.add_post("/api/releases", upload_release)
    app.router.add_get("/api/jobs", jobs)
    app.router.add_get("/api/jobs/{job_id}/events", job_events)
    app.router.add_post("/api/jobs/{job_id}/cancel", cancel_job)
    app.router.add_post("/api/jobs/{job_id}/retry", retry_job)
    app.router.add_post("/api/devices/{device_id}/jobs", create_job)
    app.router.add_post("/api/devices/{device_id}/wifi-networks", create_wifi_network)
    app.router.add_post("/api/devices/{device_id}/revoke", revoke_device)
    app.router.add_post(
        "/api/devices/{device_id}/acknowledge-recovery", acknowledge_recovery
    )
    app.router.add_get("/api/devices/{device_id}/mirror", download_mirror)
    app.router.add_get("/api/devices/{device_id}/diagnostics", device_diagnostics)
    app.router.add_get(
        "/api/devices/{device_id}/diagnostics/{diagnostics_id}", download_diagnostics
    )
    app.router.add_post("/agent/enroll", agent_enroll)
    app.router.add_post("/agent/heartbeat", agent_heartbeat)
    app.router.add_post("/agent/status", agent_status)
    app.router.add_post("/agent/jobs/{job_id}", agent_job_update)
    app.router.add_get("/agent/jobs/{job_id}/artifact", agent_artifact)
    app.router.add_put("/agent/jobs/{job_id}/artifact", agent_diagnostics_upload)
    app.router.add_post("/agent/mirror", agent_mirror)
    if (STATIC_ROOT / "assets").is_dir():
        app.router.add_static("/assets", STATIC_ROOT / "assets", append_version=True)
    if STATIC_ROOT.is_dir():
        app.router.add_static("/static", STATIC_ROOT, append_version=True)
    app.cleanup_ctx.append(_registry_maintenance)
    app.cleanup_ctx.append(_deployment_cleanup)
    return app


async def index(request: web.Request) -> web.FileResponse:
    try:
        require_static_assets(STATIC_ROOT, "fleet.html", "scripts/build_registry_ui.sh")
    except RuntimeError as error:
        raise web.HTTPInternalServerError(text=str(error)) from error
    return web.FileResponse(STATIC_ROOT / "fleet.html")


async def health(request: web.Request) -> web.Response:
    status = request.app[STORE_KEY].health()
    status["protocol_version"] = PROTOCOL_VERSION
    return web.json_response(status, status=200 if status["ok"] else 503)


async def session_status(request: web.Request) -> web.Response:
    try:
        session = request.app[AUTH_KEY].verify_session(request.cookies.get(COOKIE_NAME, ""))
    except SessionError:
        return web.json_response({"authenticated": False})
    return web.json_response({"authenticated": True, "csrf_token": session["csrf"]})


async def login(request: web.Request) -> web.Response:
    address = request.remote or "unknown"
    limiter = request.app[LOGIN_LIMITER_KEY]
    assert isinstance(limiter, LoginLimiter)
    body = await _json(request, max_bytes=8 * 1024)
    password = body.get("password")
    if not isinstance(password, str):
        raise web.HTTPBadRequest(text="Password is required.")
    delay = limiter.delay(address)
    if delay:
        await asyncio.sleep(delay)
    semaphore = request.app[LOGIN_SEMAPHORE_KEY]
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.25)
    except TimeoutError as error:
        raise web.HTTPTooManyRequests(
            text="Login service is busy. Try again shortly.", headers={"Retry-After": "1"}
        ) from error
    try:
        token = await asyncio.to_thread(request.app[AUTH_KEY].authenticate, password)
    finally:
        semaphore.release()
    if token is None:
        limiter.failed(address)
        raise web.HTTPUnauthorized(text="Incorrect password.")
    limiter.succeeded(address)
    response = web.json_response({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=request.app[SECURE_COOKIES_KEY] or request.secure,
        samesite="Strict",
        max_age=12 * 60 * 60,
        path="/",
    )
    return response


async def logout(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE_NAME)
    return response


async def devices(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"devices": request.app[STORE_KEY].list_devices()})


def _deployment_payload(
    body: dict[str, Any], store: RegistryStore
) -> dict[str, Any]:
    target = body.get("target")
    ssh_user = body.get("ssh_user")
    device_name = body.get("device_name")
    requested_hostname = body.get("hostname", "")
    hostname_change_confirmed = body.get("confirm_hostname_change", False)
    registry_url = body.get("registry_url")
    release_id = body.get("release_id")
    port = body.get("port", 22)
    allow_insecure_http = body.get("allow_insecure_http", False)
    if (
        not isinstance(target, str)
        or not isinstance(ssh_user, str)
        or not isinstance(device_name, str)
        or not isinstance(registry_url, str)
        or not isinstance(release_id, str)
    ):
        raise web.HTTPBadRequest(
            text="Target, SSH user, device name, registry URL, and release are required."
        )
    target = target.strip()
    if not target or len(target) > 253 or any(
        ord(character) < 33 for character in target
    ) or any(character in "/\\@" for character in target):
        raise web.HTTPBadRequest(text="Target must be a hostname or IP address.")
    try:
        ipaddress.ip_address(target)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", target):
            raise web.HTTPBadRequest(text="Target must be a hostname or IP address.") from None
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise web.HTTPBadRequest(text="SSH port must be between 1 and 65535.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", ssh_user):
        raise web.HTTPBadRequest(text="SSH user is invalid.")
    device_name = device_name.strip()
    if not re.fullmatch(r"[A-Za-z0-9ÄÖÜäöüß._ -]{1,80}", device_name):
        raise web.HTTPBadRequest(text="Device name is invalid.")
    if not isinstance(requested_hostname, str):
        raise web.HTTPBadRequest(text="Hostname is invalid.")
    try:
        validate_hostname(requested_hostname, allow_empty=True)
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    if not isinstance(hostname_change_confirmed, bool):
        raise web.HTTPBadRequest(text="Hostname confirmation must be boolean.")
    if bool(requested_hostname) != hostname_change_confirmed:
        raise web.HTTPBadRequest(
            text="An explicit hostname requires confirmation, and preservation cannot be confirmed."
        )
    if not isinstance(allow_insecure_http, bool):
        raise web.HTTPBadRequest(text="HTTP acknowledgement must be boolean.")
    try:
        registry_url = validate_registry_url(registry_url, allow_insecure_http)
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    if not release_id or store.get_release(release_id) is None:
        raise web.HTTPBadRequest(text="Release does not exist.")
    return {
        "target": target,
        "port": port,
        "ssh_user": ssh_user,
        "device_name": device_name.strip(),
        "requested_hostname": requested_hostname,
        "hostname_change_confirmed": hostname_change_confirmed,
        "registry_url": registry_url,
        "allow_insecure_http": allow_insecure_http,
        "release_id": release_id,
    }


async def deployments(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"deployments": request.app[STORE_KEY].list_deployments()})


async def create_deployment(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=16 * 1024)
    values = _deployment_payload(body, request.app[STORE_KEY])
    try:
        item = request.app[STORE_KEY].create_deployment(**values)
    except ValueError as error:
        raise web.HTTPConflict(text=str(error)) from error
    request.app[DEPLOYMENTS_KEY].start_discovery(item["id"])
    return web.json_response({"deployment": item}, status=202)


async def deployment(request: web.Request) -> web.Response:
    _admin(request)
    item = request.app[STORE_KEY].get_deployment(request.match_info["deployment_id"])
    if item is None:
        raise web.HTTPNotFound(text="Deployment does not exist.")
    return web.json_response({"deployment": item})


async def deployment_events(request: web.Request) -> web.StreamResponse:
    _admin(request)
    deployment_id = request.match_info["deployment_id"]
    if request.app[STORE_KEY].get_deployment(deployment_id) is None:
        raise web.HTTPNotFound(text="Deployment does not exist.")
    try:
        after = int(request.headers.get("Last-Event-ID") or request.query.get("after", "0"))
    except ValueError as error:
        raise web.HTTPBadRequest(text="Event cursor is invalid.") from error
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
        }
    )
    await response.prepare(request)
    item = request.app[STORE_KEY].get_deployment(deployment_id)
    try:
        while True:
            events = request.app[STORE_KEY].list_deployment_events(deployment_id, after)
            if events:
                item = request.app[STORE_KEY].get_deployment(deployment_id)
            for event in events:
                event["deployment"] = item
                payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
                await response.write(
                    b"id: " + str(event["id"]).encode() + b"\ndata: " + payload + b"\n\n"
                )
                after = event["id"]
            if item is None or (
                item["status"] in {"succeeded", "failed", "cancelled", "interrupted"}
                and not events
            ):
                break
            await asyncio.sleep(DEPLOYMENT_EVENT_POLL_SECONDS)
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    finally:
        with contextlib.suppress(Exception):
            await response.write_eof()
    return response


async def deployment_host_key(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=8 * 1024)
    fingerprint = body.get("fingerprint")
    replace = body.get("replace", False)
    if not isinstance(fingerprint, str) or not isinstance(replace, bool):
        raise web.HTTPBadRequest(text="Fingerprint and replacement flag are required.")
    try:
        item = await request.app[DEPLOYMENTS_KEY].confirm_host_key(
            request.match_info["deployment_id"], fingerprint, replace=replace
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"deployment": item})


async def deployment_credentials(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=128 * 1024)
    values = {}
    for name, limit in (
        ("ssh_password", 1024),
        ("ssh_private_key", 64 * 1024),
        ("ssh_key_passphrase", 1024),
        ("sudo_password", 1024),
    ):
        value = body.get(name, "")
        if not isinstance(value, str) or len(value) > limit:
            raise web.HTTPBadRequest(text=f"{name} is invalid.")
        values[name] = value
    credentials = DeploymentCredentials(**values)
    try:
        request.app[DEPLOYMENTS_KEY].submit_credentials(
            request.match_info["deployment_id"], credentials
        )
    except LookupError as error:
        credentials.clear()
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        credentials.clear()
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response(
        {"deployment": request.app[STORE_KEY].get_deployment(request.match_info["deployment_id"])},
        status=202,
    )


async def deployment_retry(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    try:
        item = request.app[DEPLOYMENTS_KEY].retry(request.match_info["deployment_id"])
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"deployment": item}, status=202)


async def deployment_cancel(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    try:
        item = await request.app[DEPLOYMENTS_KEY].cancel(request.match_info["deployment_id"])
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    return web.json_response({"deployment": item})

async def enrollment_code(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=8 * 1024)
    label = str(body.get("label") or "")[:80]
    code = request.app[STORE_KEY].create_enrollment_code(label)
    return web.json_response({"code": code, "expires_in_minutes": 60}, status=201)


async def releases(request: web.Request) -> web.Response:
    _admin(request)
    store = request.app[STORE_KEY]
    return web.json_response(
        {"releases": store.list_releases(), "bundled_release": store.bundled_release_status}
    )


async def upload_release(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    reader = await request.multipart()
    version = ""
    original_filename = ""
    temp_path: Path | None = None
    digest = hashlib.sha256()
    size = 0
    version_seen = False
    artifact_seen = False
    try:
        async for field in reader:
            if not isinstance(field, BodyPartReader):
                raise web.HTTPBadRequest(text="Nested multipart parts are not supported.")
            if field.name == "version":
                if version_seen:
                    raise web.HTTPBadRequest(text="Version field may only be supplied once.")
                version_seen = True
                version = (await field.text()).strip()
                if len(version) > 64:
                    raise web.HTTPBadRequest(text="Version is too long.")
            elif field.name == "artifact":
                if artifact_seen:
                    raise web.HTTPBadRequest(text="Only one release artifact may be uploaded.")
                artifact_seen = True
                original_filename = Path(field.filename or "takt-release.tar.gz").name
                with tempfile.NamedTemporaryFile(
                    dir=request.app[STORE_KEY].data_directory, delete=False
                ) as temporary:
                    temp_path = Path(temporary.name)
                    while chunk := await field.read_chunk(size=256 * 1024):
                        size += len(chunk)
                        if size > 250 * 1024 * 1024:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=250 * 1024 * 1024, actual_size=size
                            )
                        digest.update(chunk)
                        temporary.write(chunk)
        if not VERSION_PATTERN.fullmatch(version) or ".." in version:
            raise web.HTTPBadRequest(text="Version may only contain letters, numbers, . _ + and -.")
        if temp_path is None or size == 0:
            raise web.HTTPBadRequest(text="A release .tar.gz is required.")
        if not original_filename.endswith(".tar.gz"):
            raise web.HTTPBadRequest(text="Release must be a .tar.gz archive.")
        await asyncio.to_thread(_validate_release_archive, temp_path, version)
        try:
            release = request.app[STORE_KEY].add_release(
                version=version,
                filename=original_filename,
                sha256=digest.hexdigest(),
                size=size,
                source=temp_path,
            )
        except sqlite3.IntegrityError as error:
            raise web.HTTPConflict(text="That version already exists.") from error
        temp_path = None
        return web.json_response({"release": release}, status=201)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


async def jobs(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"jobs": request.app[STORE_KEY].list_jobs()})


async def create_job(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=16 * 1024)
    action = body.get("action")
    if action not in ALLOWED_ACTIONS:
        raise web.HTTPBadRequest(text="Unsupported action.")
    payload = body.get("payload", {})
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Job payload must be an object.")
    override = body.get("override", False)
    if not isinstance(override, bool):
        raise web.HTTPBadRequest(text="Job override must be a boolean.")
    try:
        job = request.app[STORE_KEY].create_job(
            request.match_info["device_id"], str(action), payload, override=override
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job}, status=201)


async def create_wifi_network(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    ssid, password = _wifi_network_body(await _json(request, max_bytes=8 * 1024))
    try:
        job = request.app[STORE_KEY].create_wifi_job(
            request.match_info["device_id"], ssid, password
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job}, status=201)


async def revoke_device(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    try:
        device = request.app[STORE_KEY].revoke_device(request.match_info["device_id"])
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    return web.json_response({"device": device})


async def acknowledge_recovery(request: web.Request) -> web.Response:
    session = _admin(request, csrf=True)
    actor = str(session.get("username") or "admin")
    try:
        device = request.app[STORE_KEY].acknowledge_update_recovery(
            request.match_info["device_id"], actor=actor
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"device": device})


async def download_mirror(request: web.Request) -> web.StreamResponse:
    _admin(request)
    device_id = request.match_info["device_id"]
    device = request.app[STORE_KEY].get_device(device_id)
    path = request.app[STORE_KEY].mirror_path(device_id)
    if device is None or not path.exists():
        raise web.HTTPNotFound(text="No mirrored database is available.")
    expected_size = int(device["mirror_size"] or 0)
    if path.stat().st_size != expected_size:
        raise web.HTTPServiceUnavailable(
            text="The latest mirror size is invalid; request a new mirror."
        )
    return web.FileResponse(
        path,
        headers={
            "Content-Disposition": f'attachment; filename="takt-{device_id}.sqlite3"',
            "ETag": f'"{device["mirror_sha256"]}"',
            "Cache-Control": "no-store, private",
        },
    )


async def agent_enroll(request: web.Request) -> web.Response:
    body = await _json(request, max_bytes=16 * 1024)
    required = ("enrollment_code", "device_id", "name", "hostname")
    if any(not isinstance(body.get(key), str) or not body[key] for key in required):
        raise web.HTTPBadRequest(text="Enrollment data is incomplete.")
    if not DEVICE_ID_PATTERN.fullmatch(body["device_id"]):
        raise web.HTTPBadRequest(text="Device ID is invalid.")
    proposed_token = body.get("device_token")
    if proposed_token is not None and (
        not isinstance(proposed_token, str) or not DEVICE_TOKEN_PATTERN.fullmatch(proposed_token)
    ):
        raise web.HTTPBadRequest(text="Device secret is invalid.")
    try:
        token = request.app[STORE_KEY].enroll_device(
            code=body["enrollment_code"],
            device_id=body["device_id"],
            name=body["name"][:80],
            hostname=body["hostname"][:255],
            token=proposed_token,
        )
    except ValueError as error:
        raise web.HTTPUnauthorized(text=str(error)) from error
    return web.json_response({"device_token": token}, status=201)


async def agent_heartbeat(request: web.Request) -> web.Response:
    device_id = _device(request)
    body = _heartbeat_payload(await _json(request, max_bytes=JSON_LIMIT))
    request.app[STORE_KEY].update_heartbeat(device_id, body)
    job = request.app[STORE_KEY].claim_next_job(device_id, str(body.get("agent_session_id") or ""))
    if job and job["action"] == "install_release":
        release = request.app[STORE_KEY].get_release(job["payload"]["release_id"])
        job["release"] = release
    return web.json_response(
        {
            "job": job,
            "protocol_version": PROTOCOL_VERSION,
            "server_time": utc_iso(),
        }
    )


async def agent_status(request: web.Request) -> web.Response:
    """Record telemetry without leasing work to an agent that cannot execute it."""
    device_id = _device(request)
    body = _heartbeat_payload(await _json(request, max_bytes=JSON_LIMIT))
    request.app[STORE_KEY].update_heartbeat(device_id, body)
    return web.json_response(
        {
            "protocol_version": PROTOCOL_VERSION,
            "server_time": utc_iso(),
        }
    )


async def agent_job_update(request: web.Request) -> web.Response:
    device_id = _device(request)
    body = await _json(request, max_bytes=16 * 1024)
    try:
        job = request.app[STORE_KEY].update_job(
            request.match_info["job_id"],
            device_id,
            str(body.get("status")),
            int(body.get("progress", 0)),
            str(body.get("message", "")),
            str(body.get("lease_id")) if body.get("lease_id") else None,
            stage=str(body.get("stage")) if body.get("stage") else None,
            bytes_downloaded=body.get("bytes_downloaded"),
            bytes_total=body.get("bytes_total"),
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except (TypeError, ValueError) as error:
        if any(
            marker in str(error).lower()
            for marker in ("lease", "completed jobs", "job transition", "expected version")
        ):
            raise web.HTTPConflict(text=str(error)) from error
        raise web.HTTPBadRequest(text=str(error)) from error
    if job["action"] == "run_health_checks" and job["status"] == "succeeded":
        report = _health_report_payload(body.get("result"))
        if report is not None:
            request.app[STORE_KEY].record_health_checks(device_id, report)
    return web.json_response({"job": job})


def _health_report_payload(value: Any) -> dict[str, Any] | None:
    """Normalize an agent-reported health report before it is persisted."""
    if not isinstance(value, dict):
        return None
    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list):
        return None
    checks: list[dict[str, Any]] = []
    for item in raw_checks[:40]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))[:16]
        if status not in {"ok", "warn", "fail", "skipped"}:
            continue
        checks.append(
            {
                "id": str(item.get("id", ""))[:64],
                "label": str(item.get("label", ""))[:80],
                "status": status,
                "detail": str(item.get("detail", ""))[:400],
            }
        )
    if not checks:
        return None
    counts = {status: 0 for status in ("ok", "warn", "fail", "skipped")}
    for check in checks:
        counts[str(check["status"])] += 1
    return {
        "schema": 1,
        "collected_at": utc_iso(),
        # "healthy", not "ok": the per-status counts below already use "ok".
        "summary": {"healthy": counts["fail"] == 0, **counts},
        "checks": checks,
    }


async def agent_artifact(request: web.Request) -> web.StreamResponse:
    device_id = _device(request)
    job = request.app[STORE_KEY].job_for_device(request.match_info["job_id"], device_id)
    if (
        job is None
        or job["action"] != "install_release"
        or job["status"] not in {"claimed", "running"}
        or not request.headers.get("X-Job-Lease")
        or not secrets_compare(request.headers["X-Job-Lease"], str(job["lease_id"] or ""))
    ):
        raise web.HTTPNotFound(text="Release job does not exist.")
    release = request.app[STORE_KEY].get_release(job["payload"]["release_id"])
    if release is None:
        raise web.HTTPNotFound(text="Release does not exist.")
    return web.FileResponse(
        request.app[STORE_KEY].release_path(release["id"]),
        headers={
            "Content-Disposition": f'attachment; filename="takt-{release["version"]}.tar.gz"',
            "X-TAKT-SHA256": release["sha256"],
        },
    )


async def agent_diagnostics_upload(request: web.Request) -> web.Response:
    device_id = _device(request)
    store = request.app[STORE_KEY]
    job = store.job_for_device(request.match_info["job_id"], device_id)
    if (
        job is None
        or job["action"] != "collect_diagnostics"
        or job["status"] not in {"claimed", "running"}
        or not request.headers.get("X-Job-Lease")
        or not secrets_compare(request.headers["X-Job-Lease"], str(job["lease_id"] or ""))
    ):
        raise web.HTTPNotFound(text="Diagnostics job does not exist.")
    expected_sha = request.headers.get("X-TAKT-SHA256", "")
    digest = hashlib.sha256()
    size = 0
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=store.data_directory, delete=False) as temporary:
            temp_path = Path(temporary.name)
            deadline = time.monotonic() + 5 * 60
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise web.HTTPRequestTimeout(text="Diagnostics upload timed out.")
                try:
                    chunk = await asyncio.wait_for(
                        request.content.read(256 * 1024), timeout=min(30, remaining)
                    )
                except TimeoutError as error:
                    raise web.HTTPRequestTimeout(text="Diagnostics upload stalled.") from error
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DIAGNOSTICS_BYTES:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=MAX_DIAGNOSTICS_BYTES, actual_size=size
                    )
                digest.update(chunk)
                temporary.write(chunk)
        if not expected_sha or digest.hexdigest() != expected_sha:
            raise web.HTTPBadRequest(text="Diagnostics checksum does not match.")
        bundle = store.record_diagnostics(
            device_id, str(job["id"]), temp_path, digest.hexdigest(), size
        )
        temp_path = None
        return web.json_response({"ok": True, "diagnostics_id": bundle["id"]})
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


async def download_diagnostics(request: web.Request) -> web.StreamResponse:
    _admin(request)
    store = request.app[STORE_KEY]
    bundle = store.get_diagnostics(request.match_info["diagnostics_id"])
    if bundle is None or bundle["device_id"] != request.match_info["device_id"]:
        raise web.HTTPNotFound(text="Diagnostics bundle does not exist.")
    path = store.data_directory / bundle["relative_path"]
    if not path.is_file():
        raise web.HTTPNotFound(text="Diagnostics bundle is no longer stored.")
    return web.FileResponse(
        path,
        headers={
            "Content-Disposition": (
                f'attachment; filename="takt-diagnostics-{bundle["id"]}.tar.gz"'
            ),
            "Cache-Control": "no-store, private",
        },
    )


async def device_diagnostics(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response(
        {"diagnostics": request.app[STORE_KEY].list_diagnostics(request.match_info["device_id"])}
    )


async def agent_mirror(request: web.Request) -> web.Response:
    device_id = _device(request)
    active = request.app[MIRROR_ACTIVE_KEY]
    assert isinstance(active, set)
    attempts = request.app[MIRROR_LAST_ATTEMPT_KEY]
    assert isinstance(attempts, dict)
    now = time.monotonic()
    if device_id in active:
        raise web.HTTPConflict(text="A mirror upload is already active for this device.")
    if now - float(attempts.get(device_id, 0.0)) < 10:
        raise web.HTTPTooManyRequests(
            text="Mirror uploads are limited to one attempt every 10 seconds.",
            headers={"Retry-After": "10"},
        )
    attempts[device_id] = now
    active.add(device_id)
    try:
        async with request.app[MIRROR_SEMAPHORE_KEY]:
            return await _receive_mirror(request, device_id)
    finally:
        active.discard(device_id)


async def _receive_mirror(request: web.Request, device_id: str) -> web.Response:
    expected_sha = request.headers.get("X-TAKT-SHA256", "")
    digest = hashlib.sha256()
    size = 0
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=request.app[STORE_KEY].data_directory, delete=False
        ) as temporary:
            temp_path = Path(temporary.name)
            deadline = time.monotonic() + 10 * 60
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise web.HTTPRequestTimeout(text="Mirror upload timed out.")
                try:
                    chunk = await asyncio.wait_for(
                        request.content.read(256 * 1024), timeout=min(30, remaining)
                    )
                except TimeoutError as error:
                    raise web.HTTPRequestTimeout(text="Mirror upload stalled.") from error
                if not chunk:
                    break
                size += len(chunk)
                if size > 128 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=128 * 1024 * 1024, actual_size=size
                    )
                digest.update(chunk)
                temporary.write(chunk)
        if not expected_sha or digest.hexdigest() != expected_sha:
            raise web.HTTPBadRequest(text="Database checksum does not match.")
        run_count = await asyncio.to_thread(_validate_mirror, temp_path)
        _device(request)
        store = request.app[STORE_KEY]
        sha256 = digest.hexdigest()
        existing_blob = store.mirror_blob_path(device_id, sha256)
        existing_blob_valid = None
        if existing_blob is not None:
            existing_blob_valid = await asyncio.to_thread(
                _file_matches,
                existing_blob,
                sha256,
                size,
            )
        store.record_mirror(
            device_id,
            temp_path,
            sha256,
            size,
            run_count,
            existing_blob_valid=existing_blob_valid,
        )
        temp_path = None
        return web.json_response({"ok": True, "run_count": run_count})
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _validate_mirror(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(16)
    if path.stat().st_size < 100 or header != b"SQLite format 3\x00":
        raise web.HTTPBadRequest(text="Mirror is not a SQLite database.")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            deadline = time.monotonic() + 15
            connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise web.HTTPBadRequest(text="Mirrored database failed its integrity check.")
            tables = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, type FROM sqlite_master WHERE name IN ('runs', 'schema_version')"
                )
            }
            if tables != {"runs": "table", "schema_version": "table"}:
                raise web.HTTPBadRequest(text="Mirror does not contain TAKT data tables.")
            run_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            required_columns = {
                "id",
                "run_number",
                "started_at",
                "actual_time_ms",
                "total_time_ms",
                "session_date",
            }
            if not required_columns.issubset(run_columns):
                raise web.HTTPBadRequest(text="Mirror has an incompatible runs table.")
            row = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            return int(row[0])
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise web.HTTPBadRequest(text="Mirror does not contain a valid TAKT database.") from error


def _validate_release_archive(path: Path, expected_version: str) -> None:
    try:
        validate_release_archive(path, expected_version)
    except ReleaseValidationError as error:
        raise web.HTTPBadRequest(text=str(error)) from error


async def _json(request: web.Request, *, max_bytes: int = JSON_LIMIT) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Content-Type application/json is required.")
    if request.content_length is not None and request.content_length > max_bytes:
        raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=request.content_length)
    try:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await request.content.read(min(64 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=size)
        body = json.loads(b"".join(chunks), parse_constant=_invalid_json_constant)
    except web.HTTPException:
        raise
    except Exception as error:
        raise web.HTTPBadRequest(text="A JSON object is required.") from error
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="A JSON object is required.")
    return body


def _admin(request: web.Request, *, csrf: bool = False) -> dict[str, object]:
    try:
        return request.app[AUTH_KEY].verify_session(
            request.cookies.get(COOKIE_NAME, ""),
            csrf_token=request.headers.get("X-CSRF-Token") if csrf else None,
            require_csrf=csrf,
        )
    except CsrfError as error:
        raise web.HTTPForbidden(text=str(error)) from error
    except SessionError as error:
        raise web.HTTPUnauthorized(text=str(error)) from error


def _device(request: web.Request) -> str:
    device_id = request.headers.get("X-Device-ID", "")
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if (
        not device_id
        or not token
        or not request.app[STORE_KEY].authenticate_device(device_id, token)
    ):
        raise web.HTTPUnauthorized(text="Device authentication failed.")
    return device_id


def _heartbeat_payload(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "hostname",
        "app_version",
        "agent_version",
        "health",
        "model",
        "os",
        "architecture",
        "uptime_seconds",
        "disk_free_bytes",
        "temperature_c",
        "protocol_version",
        "capabilities",
        "agent_session_id",
        "boot_id",
        "heartbeat_sequence",
        "poll_seconds",
        "registry_rtt_ms",
        "wifi_signal_dbm",
        "connection_recoveries",
        "registry_transport",
        "update_recovery",
    }
    payload = {key: value for key, value in body.items() if key in allowed}
    for key, limit in (
        ("name", 80),
        ("hostname", 255),
        ("app_version", 64),
        ("agent_version", 64),
        ("model", 255),
        ("os", 255),
        ("architecture", 32),
        ("agent_session_id", 64),
        ("boot_id", 64),
        ("registry_transport", 32),
    ):
        if key in payload and payload[key] is not None:
            payload[key] = str(payload[key])[:limit]
    health = payload.get("health")
    if health is not None and not isinstance(health, dict):
        payload["health"] = {"ok": False, "state": "invalid"}
    elif isinstance(health, dict):
        normalized_health: dict[str, Any] = {}
        if isinstance(health.get("ok"), bool):
            normalized_health["ok"] = health["ok"]
        if isinstance(health.get("ready"), bool):
            normalized_health["ready"] = health["ready"]
        for key, limit in (("state", 64), ("version", 64)):
            if health.get(key) is not None:
                if not isinstance(health[key], (str, int, float)) or isinstance(health[key], bool):
                    raise web.HTTPBadRequest(text=f"Heartbeat health field {key} is invalid.")
                normalized_health[key] = str(health[key])[:limit]
        schema_version = health.get("database_schema_version")
        if schema_version is not None:
            if isinstance(schema_version, bool):
                raise web.HTTPBadRequest(text="Heartbeat database schema is invalid.")
            try:
                normalized_health["database_schema_version"] = int(schema_version)
            except (TypeError, ValueError) as error:
                raise web.HTTPBadRequest(text="Heartbeat database schema is invalid.") from error
        payload["health"] = normalized_health
    capabilities = payload.get("capabilities", [])
    payload["capabilities"] = (
        [str(value)[:64] for value in capabilities[:20]] if isinstance(capabilities, list) else []
    )
    recovery = payload.get("update_recovery")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise web.HTTPBadRequest(text="Heartbeat update recovery is invalid.")
        payload["update_recovery"] = {
            "stuck": bool(recovery.get("stuck")),
            "error": str(recovery.get("error") or "")[:500],
            "phase": str(recovery.get("phase") or "unknown")[:64],
        }
    integer_ranges = {
        "uptime_seconds": (0, 10**10),
        "disk_free_bytes": (0, 10**16),
        "protocol_version": (0, 1000),
        "heartbeat_sequence": (0, 10**15),
        "connection_recoveries": (0, 10**12),
    }
    float_ranges = {
        "temperature_c": (-100.0, 250.0),
        "poll_seconds": (2.0, 3600.0),
        "registry_rtt_ms": (0.0, 3_600_000.0),
        "wifi_signal_dbm": (-200.0, 100.0),
    }
    for key, (minimum, maximum) in integer_ranges.items():
        if key in payload and payload[key] is not None:
            if isinstance(payload[key], bool):
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is invalid.")
            try:
                value = int(payload[key])
            except (TypeError, ValueError) as error:
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is invalid.") from error
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is out of range.")
            payload[key] = value
    for key, (min_float, max_float) in float_ranges.items():
        if key in payload and payload[key] is not None:
            if isinstance(payload[key], bool):
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is invalid.")
            try:
                float_value = float(payload[key])
            except (TypeError, ValueError) as error:
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is invalid.") from error
            if not min_float <= float_value <= max_float:
                raise web.HTTPBadRequest(text=f"Heartbeat field {key} is out of range.")
            payload[key] = float_value
    return payload


def _wifi_network_body(body: dict[str, Any]) -> tuple[str, str]:
    if set(body) != {"ssid", "password"}:
        raise web.HTTPBadRequest(text="SSID and password are required.")
    ssid = body["ssid"]
    password = body["password"]
    if not isinstance(ssid, str) or not isinstance(password, str):
        raise web.HTTPBadRequest(text="SSID and password must be strings.")
    try:
        ssid_size = len(ssid.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise web.HTTPBadRequest(text="SSID must be valid UTF-8.") from error
    if not 1 <= ssid_size <= 32 or any(
        ord(character) < 32 or ord(character) == 127 for character in ssid
    ):
        raise web.HTTPBadRequest(text="SSID must contain 1 to 32 UTF-8 bytes without controls.")
    raw_psk = re.fullmatch(r"[0-9A-Fa-f]{64}", password) is not None
    passphrase = 8 <= len(password) <= 63 and all(
        32 <= ord(character) <= 126 for character in password
    )
    if not raw_psk and not passphrase:
        raise web.HTTPBadRequest(
            text="Password must be 8 to 63 printable ASCII characters or 64 hexadecimal digits."
        )
    return ssid, password


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _file_matches(path: Path, expected_sha256: str, expected_size: int) -> bool:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def secrets_compare(left: str, right: str) -> bool:
    return bool(left and right) and secrets.compare_digest(left, right)


def _set_security_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    if request.path.startswith(("/api/", "/agent/")):
        response.headers["Cache-Control"] = "no-store"


async def _deployment_cleanup(app: web.Application):
    yield
    await app[DEPLOYMENTS_KEY].close()


async def _registry_maintenance(app: web.Application):
    store = app[STORE_KEY]
    await asyncio.to_thread(store.backup_if_due)

    async def maintain() -> None:
        while True:
            await asyncio.sleep(60 * 60)
            try:
                await asyncio.to_thread(store.backup_if_due)
                store.prune()
            except Exception:
                LOGGER.exception("registry_maintenance_failed")

    task = asyncio.create_task(maintain(), name="registry-maintenance")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def job_events(request: web.Request) -> web.Response:
    _admin(request)
    job = request.app[STORE_KEY].get_job(request.match_info["job_id"])
    if job is None:
        raise web.HTTPNotFound(text="Job does not exist.")
    return web.json_response(
        {"job": job, "events": request.app[STORE_KEY].list_job_events(job["id"])}
    )


async def cancel_job(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    try:
        job = request.app[STORE_KEY].cancel_job(request.match_info["job_id"])
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job})


async def retry_job(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request, max_bytes=4 * 1024)
    override = body.get("override", False)
    if not isinstance(override, bool):
        raise web.HTTPBadRequest(text="Job override must be a boolean.")
    try:
        job = request.app[STORE_KEY].retry_job(
            request.match_info["job_id"], override=override
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job}, status=201)
