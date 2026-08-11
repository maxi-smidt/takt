from __future__ import annotations

import hashlib
import re
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import web

from takt.registry.auth import COOKIE_NAME, AdminAuth
from takt.registry.storage import RegistryStore

STATIC_ROOT = Path(__file__).with_name("static")
STORE_KEY = web.AppKey("registry_store", RegistryStore)
AUTH_KEY = web.AppKey("registry_auth", AdminAuth)
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f-]{16,64}$")
ALLOWED_ACTIONS = {"install_release", "mirror_now", "restart_takt", "reboot"}


def create_registry_app(store: RegistryStore, auth: AdminAuth) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app[STORE_KEY] = store
    app[AUTH_KEY] = auth
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/session", session_status)
    app.router.add_post("/api/session", login)
    app.router.add_delete("/api/session", logout)
    app.router.add_get("/api/devices", devices)
    app.router.add_post("/api/enrollment-codes", enrollment_code)
    app.router.add_get("/api/releases", releases)
    app.router.add_post("/api/releases", upload_release)
    app.router.add_get("/api/jobs", jobs)
    app.router.add_post("/api/devices/{device_id}/jobs", create_job)
    app.router.add_get("/api/devices/{device_id}/mirror", download_mirror)
    app.router.add_post("/agent/enroll", agent_enroll)
    app.router.add_post("/agent/heartbeat", agent_heartbeat)
    app.router.add_post("/agent/jobs/{job_id}", agent_job_update)
    app.router.add_get("/agent/jobs/{job_id}/artifact", agent_artifact)
    app.router.add_post("/agent/mirror", agent_mirror)
    app.router.add_static("/assets", STATIC_ROOT / "assets", append_version=True)
    app.router.add_static("/static", STATIC_ROOT, append_version=True)
    return app


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_ROOT / "fleet.html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "takt-registry"})


async def session_status(request: web.Request) -> web.Response:
    try:
        session = request.app[AUTH_KEY].session(request)
    except web.HTTPUnauthorized:
        return web.json_response({"authenticated": False})
    return web.json_response({"authenticated": True, "csrf_token": session["csrf"]})


async def login(request: web.Request) -> web.Response:
    body = await _json(request)
    password = body.get("password")
    if not isinstance(password, str):
        raise web.HTTPBadRequest(text="Password is required.")
    token = request.app[AUTH_KEY].authenticate(password)
    if token is None:
        raise web.HTTPUnauthorized(text="Incorrect password.")
    response = web.json_response({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=request.secure,
        samesite="Strict",
        max_age=12 * 60 * 60,
    )
    return response


async def logout(request: web.Request) -> web.Response:
    request.app[AUTH_KEY].session(request, csrf=True)
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE_NAME)
    return response


async def devices(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"devices": request.app[STORE_KEY].list_devices()})


async def enrollment_code(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    body = await _json(request)
    label = str(body.get("label") or "")[:80]
    code = request.app[STORE_KEY].create_enrollment_code(label)
    return web.json_response({"code": code, "expires_in_minutes": 15}, status=201)


async def releases(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"releases": request.app[STORE_KEY].list_releases()})


async def upload_release(request: web.Request) -> web.Response:
    _admin(request, csrf=True)
    reader = await request.multipart()
    version = ""
    original_filename = ""
    temp_path: Path | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        async for field in reader:
            if field.name == "version":
                version = (await field.text()).strip()
            elif field.name == "artifact":
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
        _validate_release_archive(temp_path)
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
    body = await _json(request)
    action = body.get("action")
    if action not in ALLOWED_ACTIONS:
        raise web.HTTPBadRequest(text="Unsupported action.")
    payload = body.get("payload", {})
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Job payload must be an object.")
    try:
        job = request.app[STORE_KEY].create_job(
            request.match_info["device_id"], str(action), payload
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job}, status=201)


async def download_mirror(request: web.Request) -> web.StreamResponse:
    _admin(request)
    device_id = request.match_info["device_id"]
    device = request.app[STORE_KEY].get_device(device_id)
    path = request.app[STORE_KEY].mirror_path(device_id)
    if device is None or not path.exists():
        raise web.HTTPNotFound(text="No mirrored database is available.")
    return web.FileResponse(
        path,
        headers={"Content-Disposition": f'attachment; filename="takt-{device_id}.sqlite3"'},
    )


async def agent_enroll(request: web.Request) -> web.Response:
    body = await _json(request)
    required = ("enrollment_code", "device_id", "name", "hostname")
    if any(not isinstance(body.get(key), str) or not body[key] for key in required):
        raise web.HTTPBadRequest(text="Enrollment data is incomplete.")
    if not DEVICE_ID_PATTERN.fullmatch(body["device_id"]):
        raise web.HTTPBadRequest(text="Device ID is invalid.")
    try:
        token = request.app[STORE_KEY].enroll_device(
            code=body["enrollment_code"],
            device_id=body["device_id"],
            name=body["name"][:80],
            hostname=body["hostname"][:255],
        )
    except ValueError as error:
        raise web.HTTPUnauthorized(text=str(error)) from error
    return web.json_response({"device_token": token}, status=201)


async def agent_heartbeat(request: web.Request) -> web.Response:
    device_id = _device(request)
    body = await _json(request)
    request.app[STORE_KEY].update_heartbeat(device_id, body)
    job = request.app[STORE_KEY].claim_next_job(device_id)
    if job and job["action"] == "install_release":
        release = request.app[STORE_KEY].get_release(job["payload"]["release_id"])
        job["release"] = release
    return web.json_response({"job": job})


async def agent_job_update(request: web.Request) -> web.Response:
    device_id = _device(request)
    body = await _json(request)
    try:
        job = request.app[STORE_KEY].update_job(
            request.match_info["job_id"],
            device_id,
            str(body.get("status")),
            int(body.get("progress", 0)),
            str(body.get("message", "")),
        )
    except LookupError as error:
        raise web.HTTPNotFound(text=str(error)) from error
    except (TypeError, ValueError) as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"job": job})


async def agent_artifact(request: web.Request) -> web.StreamResponse:
    device_id = _device(request)
    job = request.app[STORE_KEY].job_for_device(request.match_info["job_id"], device_id)
    if job is None or job["action"] != "install_release":
        raise web.HTTPNotFound(text="Release job does not exist.")
    release = request.app[STORE_KEY].get_release(job["payload"]["release_id"])
    if release is None:
        raise web.HTTPNotFound(text="Release does not exist.")
    return web.FileResponse(
        request.app[STORE_KEY].release_path(release["id"]),
        headers={
            "Content-Disposition": f'attachment; filename="{release["filename"]}"',
            "X-TAKT-SHA256": release["sha256"],
        },
    )


async def agent_mirror(request: web.Request) -> web.Response:
    device_id = _device(request)
    expected_sha = request.headers.get("X-TAKT-SHA256", "")
    digest = hashlib.sha256()
    size = 0
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=request.app[STORE_KEY].data_directory, delete=False
        ) as temporary:
            temp_path = Path(temporary.name)
            async for chunk in request.content.iter_chunked(256 * 1024):
                size += len(chunk)
                if size > 128 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=128 * 1024 * 1024, actual_size=size
                    )
                digest.update(chunk)
                temporary.write(chunk)
        if not expected_sha or digest.hexdigest() != expected_sha:
            raise web.HTTPBadRequest(text="Database checksum does not match.")
        run_count = _validate_mirror(temp_path)
        request.app[STORE_KEY].record_mirror(
            device_id, temp_path, digest.hexdigest(), size, run_count
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
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise web.HTTPBadRequest(text="Mirrored database failed its integrity check.")
            row = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            return int(row[0])
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise web.HTTPBadRequest(text="Mirror does not contain a valid TAKT database.") from error


def _validate_release_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = set()
            expanded_size = 0
            for member in archive.getmembers():
                member_path = Path(member.name)
                expanded_size += max(member.size, 0)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise web.HTTPBadRequest(text="Release contains an unsafe archive path.")
                if expanded_size > 500 * 1024 * 1024:
                    raise web.HTTPBadRequest(text="Expanded release is too large.")
                names.add(member.name.rstrip("/"))
    except web.HTTPException:
        raise
    except (OSError, tarfile.TarError) as error:
        raise web.HTTPBadRequest(text="Release is not a readable gzip tar archive.") from error
    if not any(name.endswith("/pyproject.toml") or name == "pyproject.toml" for name in names):
        raise web.HTTPBadRequest(text="Release does not contain pyproject.toml.")
    if not any(name.endswith("/src/takt/web/static/index.html") for name in names):
        raise web.HTTPBadRequest(text="Release does not contain the built TAKT web interface.")


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as error:
        raise web.HTTPBadRequest(text="A JSON object is required.") from error
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="A JSON object is required.")
    return body


def _admin(request: web.Request, *, csrf: bool = False) -> dict[str, object]:
    return request.app[AUTH_KEY].session(request, csrf=csrf)


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
