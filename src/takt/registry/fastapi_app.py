"""FastAPI/ASGI Registry application.

This adapter deliberately reuses the existing RegistryStore and deployment manager
until their aggregate repositories are extracted.  The HTTP boundary is already
typed and framework-independent authentication is shared with the characterization
application, which lets the migration proceed one vertical slice at a time.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.staticfiles import StaticFiles

from takt.protocol import PROTOCOL_VERSION
from takt.registry.api_models import (
    AgentHeartbeatResponse,
    AgentStatusResponse,
    DeploymentCreateRequest,
    DeploymentCredentialsRequest,
    DeploymentHostKeyRequest,
    DeviceTokenResponse,
    EnrollmentCodeResponse,
    EnrollmentRequest,
    HealthResponse,
    HeartbeatRequest,
    JobCreateRequest,
    JobOverrideRequest,
    JobUpdateRequest,
    LabelRequest,
    LoginRequest,
    LoginResponse,
    SessionStatusResponse,
    WifiNetworkRequest,
)
from takt.registry.auth import COOKIE_NAME, AdminAuth, CsrfError, SessionError
from takt.registry.bundled_release import ReleaseValidationError, validate_release_archive
from takt.registry.deployment import DeploymentCredentials, DeploymentManager
from takt.registry.storage import RegistryStore, utc_iso
from takt.static_assets import require_static_assets

STATIC_ROOT = Path(__file__).with_name("static")
JSON_LIMIT = 64 * 1024
MAX_RELEASE_BYTES = 250 * 1024 * 1024
MAX_RELEASE_REQUEST_BYTES = 256 * 1024 * 1024
MAX_MIRROR_BYTES = 128 * 1024 * 1024
MAX_DIAGNOSTICS_BYTES = 8 * 1024 * 1024
DEPLOYMENT_EVENT_POLL_SECONDS = 2
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
LOGGER = logging.getLogger(__name__)


class LoginLimiter:
    def __init__(self) -> None:
        from collections import defaultdict, deque

        self._failures: dict[str, Any] = defaultdict(deque)

    def failed(self, address: str) -> float:
        now = time.monotonic()
        failures = self._failures[address]
        failures.append(now)
        while failures and now - failures[0] > 300:
            failures.popleft()
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


def _store(request: Request) -> RegistryStore:
    return request.app.state.store


def _auth(request: Request) -> AdminAuth:
    return request.app.state.auth


def _verify_session(request: Request, *, csrf: bool) -> dict[str, object]:
    token = request.cookies.get(COOKIE_NAME, "")
    accounts = getattr(request.app.state, "accounts", None)
    if accounts is not None and accounts.has_users():
        session = accounts.verify_session(token)
        if session is None:
            raise HTTPException(status_code=401, detail="Login required.")
        if bool(session.get("must_change_password")) and request.url.path not in {
            "/api/session",
            "/api/session/password",
        }:
            raise HTTPException(status_code=403, detail="Password change required.")
        if csrf and not secrets.compare_digest(
            str(session["csrf"]), request.headers.get("X-CSRF-Token", "")
        ):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
        return session
    try:
        return _auth(request).verify_session(
            token,
            csrf_token=request.headers.get("X-CSRF-Token") if csrf else None,
            require_csrf=csrf,
        )
    except CsrfError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except SessionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


def _verify_admin(request: Request, *, csrf: bool) -> dict[str, object]:
    session = _verify_session(request, csrf=csrf)
    if "is_admin" in session and not bool(session["is_admin"]):
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return session


def _verify_user(request: Request) -> dict[str, object]:
    return _verify_session(request, csrf=False)


def _admin(request: Request) -> dict[str, object]:
    return _verify_admin(request, csrf=False)


def _admin_csrf(request: Request) -> dict[str, object]:
    return _verify_admin(request, csrf=True)


_ADMIN_DEPENDENCY = Depends(_admin)
_ADMIN_CSRF_DEPENDENCY = Depends(_admin_csrf)
_USER_DEPENDENCY = Depends(_verify_user)


def _user_csrf(request: Request) -> dict[str, object]:
    return _verify_session(request, csrf=True)


_USER_CSRF_DEPENDENCY = Depends(_user_csrf)


def _device(request: Request) -> str:
    device_id = request.headers.get("X-Device-ID", "")
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not device_id or not token or not _store(request).authenticate_device(device_id, token):
        raise HTTPException(status_code=401, detail="Device authentication failed.")
    return device_id


def _json_error_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "A JSON object is required."
    message = errors[0].get("msg", "Request validation failed.")
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return str(message)


def _set_security_headers(request: Request, response: Any) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    if request.url.path.startswith(("/api/", "/agent/")):
        response.headers["Cache-Control"] = "no-store"


_JSON_BODY_PATHS = {
    ("POST", "/api/session"),
    ("POST", "/api/enrollment-codes"),
    ("POST", "/api/session/password"),
    ("POST", "/api/admin/users"),
    ("PATCH", "/api/admin/users/{user_id}"),
    ("POST", "/api/admin/users/{user_id}/reset-password"),
    ("PUT", "/api/admin/users/{user_id}/devices/{device_id}"),
    ("POST", "/api/portal/devices/{device_id}/runs/{run_id}/commands"),
    ("POST", "/api/deployments"),
    ("POST", "/api/devices/{device_id}/jobs"),
    ("POST", "/api/devices/{device_id}/wifi-networks"),
    ("POST", "/agent/enroll"),
    ("POST", "/agent/heartbeat"),
    ("POST", "/agent/status"),
    ("POST", "/agent/jobs/{job_id}"),
    ("POST", "/api/jobs/{job_id}/retry"),
}


def _route_path(app: FastAPI, method: str, path: str) -> str | None:
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if methods and method not in methods:
            continue
        path_regex = getattr(route, "path_regex", None)
        if path_regex is not None and path_regex.match(path):
            return getattr(route, "path", None)
    return None


def _requires_json_body(method: str, route_path: str | None) -> bool:
    return route_path is not None and (method, route_path) in _JSON_BODY_PATHS


def _json_limit(route_path: str) -> int:
    if route_path in {"/api/session", "/api/enrollment-codes"} or route_path.endswith(
        "/host-key"
    ):
        return 8 * 1024
    if route_path.endswith("/credentials"):
        return 128 * 1024
    if route_path in {
        "/api/deployments",
        "/api/devices/{device_id}/jobs",
        "/api/devices/{device_id}/wifi-networks",
        "/agent/jobs/{job_id}",
        "/api/jobs/{job_id}/retry",
    }:
        return 16 * 1024
    return JSON_LIMIT


class RequestBodyTooLarge(Exception):
    pass


class MultipartBodyLimitMiddleware:
    def __init__(self, app: Any, route_lookup: Callable[[str, str], str | None]) -> None:
        self.app = app
        self.route_lookup = route_lookup

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        content_type = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-type"
            ),
            "",
        ).split(";", 1)[0].lower()
        route_path = self.route_lookup(scope.get("method", ""), scope.get("path", ""))
        if (
            scope.get("method") != "POST"
            or route_path != "/api/releases"
            or content_type != "multipart/form-data"
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length < 0:
                await _send_body_error(scope, receive, send, "Invalid Content-Length.", 400)
                return
            if declared_length > MAX_RELEASE_REQUEST_BYTES:
                await _send_body_error(scope, receive, send, "Request body is too large.", 413)
                return

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_RELEASE_REQUEST_BYTES:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await _send_body_error(scope, receive, send, "Request body is too large.", 413)


async def _send_body_error(
    scope: dict[str, Any], receive: Any, send: Any, message: str, status_code: int
) -> None:
    request = Request(scope, receive)
    response = PlainTextResponse(message, status_code=status_code)
    _set_security_headers(request, response)
    await response(scope, receive, send)


def _secrets_compare(left: str, right: str) -> bool:
    return bool(left and right) and secrets.compare_digest(left, right)


def _file_matches(path: Path, expected_sha256: str, expected_size: int) -> bool:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def _health_report_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("checks"), list):
        return None
    checks: list[dict[str, Any]] = []
    for item in value["checks"][:40]:
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
    counts = dict.fromkeys(("ok", "warn", "fail", "skipped"), 0)
    for check in checks:
        counts[str(check["status"])] += 1
    return {
        "schema": 1,
        "collected_at": utc_iso(),
        "summary": {"healthy": counts["fail"] == 0, **counts},
        "checks": checks,
    }


def _validate_mirror(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(16)
    if path.stat().st_size < 100 or header != b"SQLite format 3\x00":
        raise HTTPException(status_code=400, detail="Mirror is not a SQLite database.")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            deadline = time.monotonic() + 15
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 10_000
            )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise HTTPException(
                    status_code=400, detail="Mirrored database failed its integrity check."
                )
            tables = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE name IN ('runs', 'schema_version')"
                )
            }
            if tables != {"runs": "table", "schema_version": "table"}:
                raise HTTPException(
                    status_code=400, detail="Mirror does not contain TAKT data tables."
                )
            run_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            required_columns = {
                "id",
                "run_number",
                "started_at",
                "stopped_at",
                "saved_at",
                "actual_time_ms",
                "total_time_ms",
                "session_date",
            }
            if not required_columns.issubset(run_columns):
                raise HTTPException(
                    status_code=400, detail="Mirror has an incompatible runs table."
                )
            row = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            return int(row[0])
        finally:
            connection.close()
    except HTTPException:
        raise
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=400, detail="Mirror does not contain a valid TAKT database."
        ) from error


async def _stream_upload(
    request: Request,
    directory: Path,
    *,
    maximum: int,
    timeout_seconds: int,
    label: str,
) -> tuple[Path, str, int]:
    digest = hashlib.sha256()
    size = 0
    temporary_path: Path | None = None
    deadline = time.monotonic() + timeout_seconds
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            iterator = request.stream().__aiter__()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HTTPException(status_code=408, detail=f"{label} upload timed out.")
                try:
                    chunk = await asyncio.wait_for(
                        iterator.__anext__(), timeout=min(30, remaining)
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError as error:
                    raise HTTPException(
                        status_code=408, detail=f"{label} upload stalled."
                    ) from error
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(status_code=413, detail=f"{label} is too large.")
                digest.update(chunk)
                temporary.write(chunk)
        assert temporary_path is not None
        completed_path = temporary_path
        temporary_path = None
        return completed_path, digest.hexdigest(), size
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _portal_access(
    store: RegistryStore, session: dict[str, object], device_id: str, *, write: bool = False
) -> str:
    if bool(session.get("is_admin")):
        return "write"
    level = store.accounts.access_level(str(session.get("user_id") or ""), device_id)
    if level is None or (write and level != "write"):
        raise HTTPException(status_code=404, detail="Dieses Gerät existiert nicht.")
    return level


def _mirror_connection(
    store: RegistryStore, device_id: str
) -> tuple[sqlite3.Connection, dict[str, Any]]:
    device = store.get_device(device_id)
    path = store.mirror_path(device_id)
    if device is None or not path.is_file() or not device.get("mirror_sha256"):
        raise HTTPException(
            status_code=503, detail="Es liegt noch keine gespiegelte Datenbank vor."
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
        required = {
            "id",
            "run_number",
            "started_at",
            "stopped_at",
            "saved_at",
            "actual_time_ms",
            "added_time_ms",
            "total_time_ms",
            "session_date",
            "updated_at",
        }
        if not required.issubset(columns):
            raise ValueError("incompatible runs table")
    except (OSError, sqlite3.Error, ValueError) as error:
        with contextlib.suppress(Exception):
            if connection is not None:
                connection.close()
        raise HTTPException(
            status_code=503, detail="Die gespiegelte Datenbank ist nicht verfügbar."
        ) from error
    assert connection is not None
    return connection, device


def _portal_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "run_number": int(row["run_number"]),
        "session_date": row["session_date"],
        "started_at": row["started_at"],
        "stopped_at": row["stopped_at"],
        "saved_at": row["saved_at"],
        "actual_time_ms": int(row["actual_time_ms"]),
        "added_time_ms": int(row["added_time_ms"]),
        "total_time_ms": int(row["total_time_ms"]),
        # sqlite3.Row's `in` checks values, not column names -- `.keys()` is required.
        "note": row["note"] if "note" in row.keys() else None,  # noqa: SIM118
        "updated_at": row["updated_at"],
    }


def _encode_portal_cursor(started_at: str, run_id: int) -> str:
    return base64.urlsafe_b64encode(f"{started_at}\0{run_id}".encode()).decode().rstrip("=")


def _decode_portal_cursor(value: str) -> tuple[str, int]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        started_at, run_id = raw.split("\0", 1)
        return started_at, int(run_id)
    except (ValueError, UnicodeError) as error:
        raise HTTPException(status_code=400, detail="Der Lauf-Cursor ist ungültig.") from error


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    store: RegistryStore = app.state.store
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
        await app.state.deployments.close()


def create_fastapi_app(
    store: RegistryStore,
    auth: AdminAuth,
    *,
    secure_cookies: bool = False,
) -> FastAPI:
    app = FastAPI(
        title="TAKT Fleet Registry",
        description="Fleet management and mirror API for TAKT devices.",
        version="0.3.0",
        lifespan=_lifespan,
    )
    app.state.store = store
    app.state.accounts = store.accounts
    app.state.auth = auth
    app.state.deployments = DeploymentManager(store)
    app.state.secure_cookies = secure_cookies
    app.state.login_limiter = LoginLimiter()
    app.state.login_semaphore = asyncio.Semaphore(2)
    app.state.mirror_semaphore = asyncio.Semaphore(2)
    mirror_active: set[str] = set()
    mirror_last_attempt: dict[str, float] = {}
    app.state.mirror_active = mirror_active
    app.state.mirror_last_attempt = mirror_last_attempt

    @app.middleware("http")
    async def registry_middleware(request: Request, call_next: Any) -> Any:
        route_path = _route_path(app, request.method, request.url.path)
        if route_path is not None and _requires_json_body(request.method, route_path):
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != (
                "application/json"
            ):
                response = PlainTextResponse(
                    "Content-Type application/json is required.", status_code=415
                )
                _set_security_headers(request, response)
                return response
            limit = _json_limit(route_path)
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    response = PlainTextResponse("Invalid Content-Length.", status_code=400)
                    _set_security_headers(request, response)
                    return response
                if declared_length < 0:
                    response = PlainTextResponse("Invalid Content-Length.", status_code=400)
                    _set_security_headers(request, response)
                    return response
                if declared_length > limit:
                    response = PlainTextResponse("Request body is too large.", status_code=413)
                    _set_security_headers(request, response)
                    return response
            body = await request.body()
            if len(body) > limit:
                response = PlainTextResponse("Request body is too large.", status_code=413)
                _set_security_headers(request, response)
                return response
        response = await call_next(request)
        _set_security_headers(request, response)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> PlainTextResponse:
        response = PlainTextResponse(
            str(exc.detail), status_code=exc.status_code, headers=exc.headers
        )
        _set_security_headers(request, response)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> PlainTextResponse:
        response = PlainTextResponse(_json_error_message(exc), status_code=400)
        _set_security_headers(request, response)
        return response

    @app.exception_handler(MultiPartException)
    async def multipart_exception_handler(
        request: Request, exc: MultiPartException
    ) -> PlainTextResponse:
        response = PlainTextResponse(str(exc), status_code=400)
        _set_security_headers(request, response)
        return response

    @app.exception_handler(MultipartParseError)
    async def multipart_parse_error_handler(
        request: Request, exc: MultipartParseError
    ) -> PlainTextResponse:
        response = PlainTextResponse(str(exc), status_code=400)
        _set_security_headers(request, response)
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        try:
            require_static_assets(STATIC_ROOT, "fleet.html", "scripts/build_registry_ui.sh")
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return FileResponse(STATIC_ROOT / "fleet.html")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> JSONResponse:
        status = store.health()
        status["protocol_version"] = PROTOCOL_VERSION
        return JSONResponse(status, status_code=200 if status["ok"] else 503)

    @app.get("/api/session", response_model=SessionStatusResponse)
    async def session_status(request: Request) -> dict[str, object]:
        accounts = app.state.accounts
        if accounts.has_users():
            session = accounts.verify_session(request.cookies.get(COOKIE_NAME, ""))
            if session is None:
                return {"authenticated": False}
            return {
                "authenticated": True,
                "csrf_token": session["csrf"],
                "user": {
                    "id": session["user_id"],
                    "username": session["username"],
                    "is_admin": session["is_admin"],
                    "must_change_password": session["must_change_password"],
                },
            }
        try:
            session = auth.verify_session(request.cookies.get(COOKIE_NAME, ""))
        except SessionError:
            return {"authenticated": False}
        return {"authenticated": True, "csrf_token": session["csrf"]}

    @app.post("/api/session", response_model=LoginResponse)
    async def login(request: Request, body: LoginRequest) -> JSONResponse:
        address = request.client.host if request.client else "unknown"
        limiter: LoginLimiter = app.state.login_limiter
        delay = limiter.delay(address)
        if delay:
            await asyncio.sleep(delay)
        semaphore: asyncio.Semaphore = app.state.login_semaphore
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.25)
        except TimeoutError as error:
            raise HTTPException(
                status_code=429,
                detail="Login service is busy. Try again shortly.",
                headers={"Retry-After": "1"},
            ) from error
        accounts = app.state.accounts
        try:
            if accounts.has_users():
                user = await asyncio.to_thread(
                    accounts.authenticate, body.username, body.password.get_secret_value()
                )
                if user is None:
                    limiter.failed(address)
                    raise HTTPException(status_code=401, detail="Incorrect username or password.")
                token, _session = await asyncio.to_thread(accounts.create_session, user["id"])
                response_body = {"ok": True, "user": user}
            else:
                token = await asyncio.to_thread(auth.authenticate, body.password.get_secret_value())
                if token is None:
                    limiter.failed(address)
                    raise HTTPException(status_code=401, detail="Incorrect password.")
                response_body = {"ok": True}
        finally:
            semaphore.release()
        limiter.succeeded(address)
        response = JSONResponse(response_body)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=app.state.secure_cookies or request.url.scheme == "https",
            samesite="strict",
            max_age=12 * 60 * 60,
            path="/",
        )
        return response

    @app.delete("/api/session")
    async def logout(request: Request) -> JSONResponse:
        session = _verify_session(request, csrf=True)
        if app.state.accounts.has_users():
            app.state.accounts.revoke_session(
                request.cookies.get(COOKIE_NAME, ""), actor_user_id=str(session["user_id"])
            )
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/admin/users")
    async def admin_users(_: dict[str, object] = _ADMIN_DEPENDENCY) -> dict[str, Any]:
        return {"users": store.accounts.list_users()}

    @app.post("/api/admin/users", status_code=201)
    async def admin_create_user(
        request: Request, body: dict[str, Any], session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY
    ) -> JSONResponse:
        username = str(body.get("username") or "").strip()
        temporary_password = str(body.get("password") or secrets.token_urlsafe(18))
        try:
            user = store.accounts.create_user(
                username,
                temporary_password,
                is_admin=bool(body.get("is_admin", False)),
                must_change_password=True,
                actor_user_id=str(session.get("user_id") or ""),
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse(
            {"user": user, "temporary_password": temporary_password}, status_code=201
        )

    @app.patch("/api/admin/users/{user_id}")
    async def admin_update_user(
        user_id: str, body: dict[str, Any], session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY
    ) -> dict[str, Any]:
        try:
            user = store.accounts.set_user_state(
                user_id,
                disabled=body.get("disabled") if "disabled" in body else None,
                is_admin=body.get("is_admin") if "is_admin" in body else None,
                actor_user_id=str(session.get("user_id") or ""),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"user": user}

    @app.post("/api/admin/users/{user_id}/reset-password")
    async def admin_reset_password(
        user_id: str, session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY
    ) -> dict[str, Any]:
        temporary_password = secrets.token_urlsafe(18)
        try:
            user = store.accounts.reset_password(
                user_id, temporary_password, actor_user_id=str(session.get("user_id") or "")
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"user": user, "temporary_password": temporary_password}

    @app.put("/api/admin/users/{user_id}/devices/{device_id}")
    async def admin_grant_device(
        user_id: str,
        device_id: str,
        body: dict[str, Any],
        session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        try:
            access = store.accounts.grant_access(
                user_id,
                device_id,
                str(body.get("access") or ""),
                actor_user_id=str(session.get("user_id") or ""),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"access": access}

    @app.delete("/api/admin/users/{user_id}/devices/{device_id}")
    async def admin_revoke_device(
        user_id: str, device_id: str, session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY
    ) -> dict[str, Any]:
        if not store.accounts.revoke_access(
            user_id, device_id, actor_user_id=str(session.get("user_id") or "")
        ):
            raise HTTPException(status_code=404, detail="Access assignment does not exist.")
        return {"ok": True}

    @app.post("/api/session/password")
    async def change_password(
        request: Request, body: dict[str, Any], session: dict[str, object] = _USER_CSRF_DEPENDENCY
    ) -> dict[str, Any]:
        if not store.accounts.has_users():
            raise HTTPException(status_code=404, detail="Password changes require user accounts.")
        try:
            store.accounts.change_password(
                str(session["user_id"]),
                str(body.get("current_password") or ""),
                str(body.get("new_password") or ""),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True}

    @app.get("/api/portal/devices")
    async def portal_devices(session: dict[str, object] = _USER_DEPENDENCY) -> dict[str, Any]:
        visible = []
        for device in store.list_devices():
            try:
                access = _portal_access(store, session, str(device["id"]))
            except HTTPException:
                continue
            status = device.get("status") or {}
            if not device.get("last_mirror_at"):
                mirror_state = "missing"
            elif not device.get("online"):
                mirror_state = "offline"
            elif bool(status.get("mirror_pending")):
                mirror_state = "pending"
            else:
                mirror_state = "fresh"
            visible.append(
                {
                    "id": device["id"],
                    "name": device["name"],
                    "hostname": device["hostname"],
                    "online": device["online"],
                    "access": access,
                    "run_count": device.get("run_count"),
                    "last_mirrored_at": device.get("last_mirror_at"),
                    "mirror_state": mirror_state,
                }
            )
        return {"devices": visible}

    @app.get("/api/portal/devices/{device_id}/runs")
    async def portal_runs(
        request: Request, device_id: str, session: dict[str, object] = _USER_DEPENDENCY
    ) -> dict[str, Any]:
        _portal_access(store, session, device_id)
        connection, device = _mirror_connection(store, device_id)
        try:
            limit = min(max(int(request.query_params.get("limit", "50")), 1), 100)
            date_from = request.query_params.get("from")
            date_to = request.query_params.get("to")
            clauses = ["1 = 1"]
            params: list[Any] = []
            if date_from:
                clauses.append("session_date >= ?")
                params.append(date_from)
            if date_to:
                clauses.append("session_date <= ?")
                params.append(date_to)
            summary_where = " AND ".join(clauses)
            summary_params = tuple(params)
            cursor = request.query_params.get("cursor")
            if cursor:
                started_at, run_id = _decode_portal_cursor(cursor)
                clauses.append("(started_at < ? OR (started_at = ? AND id < ?))")
                params.extend([started_at, started_at, run_id])
            where = " AND ".join(clauses)
            rows = connection.execute(
                f"SELECT * FROM runs WHERE {where} ORDER BY started_at DESC, id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            summary = connection.execute(
                f"SELECT COUNT(*) AS count, MIN(total_time_ms) AS best_total_ms, "
                f"AVG(total_time_ms) AS average_total_ms, SUM(added_time_ms) AS added_time_ms "
                f"FROM runs WHERE {summary_where}",
                summary_params,
            ).fetchone()
            page = rows[:limit]
            next_cursor = (
                _encode_portal_cursor(str(page[-1]["started_at"]), int(page[-1]["id"]))
                if len(rows) > limit
                else None
            )
            return {
                "device": {"id": device["id"], "name": device["name"]},
                "mirror": {
                    "sha256": device["mirror_sha256"],
                    "last_mirrored_at": device["last_mirror_at"],
                    "state": "offline"
                    if not device.get("online")
                    else (
                        "pending" if (device.get("status") or {}).get("mirror_pending") else "fresh"
                    ),
                },
                "summary": {
                    "count": int(summary["count"] or 0),
                    "best_total_ms": summary["best_total_ms"],
                    "average_total_ms": summary["average_total_ms"],
                    "added_time_ms": int(summary["added_time_ms"] or 0),
                },
                "runs": [_portal_run(row) for row in page],
                "next_cursor": next_cursor,
            }
        except (ValueError, OverflowError) as error:
            raise HTTPException(status_code=400, detail="Die Filterwerte sind ungültig.") from error
        finally:
            connection.close()

    @app.get("/api/portal/devices/{device_id}/runs/{run_id}")
    async def portal_run_detail(
        device_id: str, run_id: int, session: dict[str, object] = _USER_DEPENDENCY
    ) -> dict[str, Any]:
        _portal_access(store, session, device_id)
        connection, device = _mirror_connection(store, device_id)
        try:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Dieser Lauf existiert nicht.")
            return {
                "device": {"id": device["id"], "name": device["name"]},
                "run": _portal_run(row),
                "mirror_sha256": device["mirror_sha256"],
            }
        finally:
            connection.close()

    @app.post("/api/portal/devices/{device_id}/runs/{run_id}/commands", status_code=202)
    async def portal_command(
        device_id: str,
        run_id: int,
        body: dict[str, Any],
        session: dict[str, object] = _USER_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        _portal_access(store, session, device_id, write=True)
        if body.get("confirmed") is not True:
            raise HTTPException(
                status_code=400, detail="Eine ausdrückliche Bestätigung ist erforderlich."
            )
        operation = str(body.get("operation") or "")
        if operation not in {"adjust_added_time", "delete"}:
            raise HTTPException(
                status_code=400, detail="Diese Laufkorrektur wird nicht unterstützt."
            )
        expected_updated_at = body.get("expected_updated_at")
        expected_sha256 = body.get("mirror_sha256")
        desired = body.get("desired_added_time_ms")
        if not isinstance(expected_updated_at, str) or not isinstance(expected_sha256, str):
            raise HTTPException(
                status_code=400, detail="Spiegel- und Laufversion sind erforderlich."
            )
        if operation == "adjust_added_time" and (
            not isinstance(desired, int) or isinstance(desired, bool) or desired < 0
        ):
            raise HTTPException(status_code=400, detail="Eine gültige Zusatzzeit ist erforderlich.")
        connection, device = _mirror_connection(store, device_id)
        try:
            if expected_sha256 != device.get("mirror_sha256"):
                raise HTTPException(
                    status_code=409,
                    detail="Der Spiegel hat sich geändert; bitte den Lauf neu laden.",
                )
            row = connection.execute(
                "SELECT updated_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None or row["updated_at"] != expected_updated_at:
                raise HTTPException(
                    status_code=409, detail="Der Lauf hat sich geändert; bitte neu laden."
                )
            payload = {
                "operation": operation,
                "run_id": run_id,
                "expected_updated_at": expected_updated_at,
                "desired_added_time_ms": desired,
                "mirror_sha256": expected_sha256,
            }
        finally:
            connection.close()
        try:
            job = store.create_job(
                device_id,
                "curate_run",
                payload,
                requested_by_user_id=str(session.get("user_id") or "unknown"),
            )
        except (LookupError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse({"job": job}, status_code=202)

    @app.get("/api/portal/commands/{job_id}")
    async def portal_command_status(
        job_id: str, session: dict[str, object] = _USER_DEPENDENCY
    ) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None or (
            not session.get("is_admin")
            and job.get("requested_by_user_id") != session.get("user_id")
        ):
            raise HTTPException(status_code=404, detail="Dieser Befehl existiert nicht.")
        if not session.get("is_admin"):
            _portal_access(store, session, str(job.get("device_id") or ""))
        return {"job": job}

    @app.get("/api/devices")
    async def devices(request: Request, _: dict[str, object] = _ADMIN_DEPENDENCY) -> dict[str, Any]:
        return {"devices": store.list_devices()}

    @app.get("/api/deployments")
    async def deployments(
        request: Request, _: dict[str, object] = _ADMIN_DEPENDENCY
    ) -> dict[str, Any]:
        return {"deployments": store.list_deployments()}

    @app.post("/api/deployments", status_code=202)
    async def create_deployment(
        request: Request,
        body: DeploymentCreateRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        values = body.store_values()
        if store.get_release(values["release_id"]) is None:
            raise HTTPException(status_code=400, detail="Release does not exist.")
        try:
            item = store.create_deployment(**values)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        app.state.deployments.start_discovery(item["id"])
        return JSONResponse({"deployment": item}, status_code=202)

    @app.get("/api/deployments/{deployment_id}")
    async def deployment(
        deployment_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> dict[str, Any]:
        item = store.get_deployment(deployment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Deployment does not exist.")
        return {"deployment": item}

    @app.get("/api/deployments/{deployment_id}/events")
    async def deployment_events(
        request: Request,
        deployment_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> StreamingResponse:
        if store.get_deployment(deployment_id) is None:
            raise HTTPException(status_code=404, detail="Deployment does not exist.")
        try:
            after = int(
                request.headers.get("Last-Event-ID") or request.query_params.get("after", "0")
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Event cursor is invalid.") from error

        async def stream() -> AsyncIterator[bytes]:
            nonlocal after
            current = store.get_deployment(deployment_id)
            try:
                while True:
                    events = store.list_deployment_events(deployment_id, after)
                    if events:
                        current = store.get_deployment(deployment_id)
                    for event in events:
                        event["deployment"] = current
                        yield (
                            f"id: {event['id']}\ndata: "
                            f"{json.dumps(event, separators=(',', ':'))}\n\n"
                        ).encode()
                        after = event["id"]
                    if current is None or (
                        current["status"] in {"succeeded", "failed", "cancelled", "interrupted"}
                        and not events
                    ):
                        break
                    await asyncio.sleep(DEPLOYMENT_EVENT_POLL_SECONDS)
            except asyncio.CancelledError:
                raise

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive"},
        )

    @app.post("/api/deployments/{deployment_id}/host-key")
    async def deployment_host_key(
        deployment_id: str,
        body: DeploymentHostKeyRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        try:
            item = await app.state.deployments.confirm_host_key(
                deployment_id, body.fingerprint, replace=body.replace
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"deployment": item}

    @app.post("/api/deployments/{deployment_id}/credentials", status_code=202)
    async def deployment_credentials(
        deployment_id: str,
        body: DeploymentCredentialsRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        credentials = DeploymentCredentials(**body.values())
        try:
            app.state.deployments.submit_credentials(deployment_id, credentials)
        except LookupError as error:
            credentials.clear()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            credentials.clear()
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse({"deployment": store.get_deployment(deployment_id)}, status_code=202)

    @app.post("/api/deployments/{deployment_id}/retry", status_code=202)
    async def deployment_retry(
        deployment_id: str,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        try:
            item = app.state.deployments.retry(deployment_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse({"deployment": item}, status_code=202)

    @app.post("/api/deployments/{deployment_id}/cancel")
    async def deployment_cancel(
        deployment_id: str,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        try:
            item = await app.state.deployments.cancel(deployment_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"deployment": item}

    @app.post("/api/enrollment-codes", status_code=201, response_model=EnrollmentCodeResponse)
    async def enrollment_code(
        body: LabelRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        code = store.create_enrollment_code(body.label)
        return JSONResponse({"code": code, "expires_in_minutes": 60}, status_code=201)

    @app.get("/api/releases")
    async def releases(
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> dict[str, Any]:
        return {
            "releases": store.list_releases(),
            "bundled_release": store.bundled_release_status,
        }

    @app.post("/api/releases", status_code=201)
    async def upload_release(
        request: Request,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != (
            "multipart/form-data"
        ):
            raise HTTPException(
                status_code=415,
                detail="Content-Type multipart/form-data is required.",
            )
        version = ""
        original_filename = ""
        temp_path: Path | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            form = await request.form(max_part_size=MAX_RELEASE_BYTES)
            version = str(form.get("version") or "").strip()
            artifact = form.get("artifact")
            if not isinstance(artifact, UploadFile):
                raise HTTPException(status_code=400, detail="A release .tar.gz is required.")
            original_filename = Path(artifact.filename or "takt-release.tar.gz").name
            with tempfile.NamedTemporaryFile(dir=store.data_directory, delete=False) as temporary:
                temp_path = Path(temporary.name)
                while chunk := await artifact.read(256 * 1024):
                    size += len(chunk)
                    if size > MAX_RELEASE_BYTES:
                        raise HTTPException(status_code=413, detail="Release is too large.")
                    digest.update(chunk)
                    temporary.write(chunk)
            if not VERSION_PATTERN.fullmatch(version) or ".." in version:
                raise HTTPException(
                    status_code=400,
                    detail="Version may only contain letters, numbers, . _ + and -.",
                )
            if temp_path is None or size == 0:
                raise HTTPException(status_code=400, detail="A release .tar.gz is required.")
            if not original_filename.endswith(".tar.gz"):
                raise HTTPException(status_code=400, detail="Release must be a .tar.gz archive.")
            try:
                await asyncio.to_thread(validate_release_archive, temp_path, version)
            except ReleaseValidationError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            try:
                release = store.add_release(
                    version=version,
                    filename=original_filename,
                    sha256=digest.hexdigest(),
                    size=size,
                    source=temp_path,
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409, detail="That version already exists."
                ) from error
            temp_path = None
            return JSONResponse({"release": release}, status_code=201)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @app.get("/api/jobs")
    async def jobs(_: dict[str, object] = _ADMIN_DEPENDENCY) -> dict[str, Any]:
        return {"jobs": store.list_jobs()}

    @app.post("/api/devices/{device_id}/jobs", status_code=201)
    async def create_job(
        device_id: str,
        body: JobCreateRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        try:
            job = store.create_job(device_id, body.action, body.payload, override=body.override)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse({"job": job}, status_code=201)

    @app.post("/api/devices/{device_id}/wifi-networks", status_code=201)
    async def create_wifi_network(
        device_id: str,
        body: WifiNetworkRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        ssid, password = body.values()
        try:
            job = store.create_wifi_job(device_id, ssid, password)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse({"job": job}, status_code=201)

    @app.post("/api/devices/{device_id}/revoke")
    async def revoke_device(
        device_id: str,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        try:
            device = store.revoke_device(device_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"device": device}

    @app.post("/api/devices/{device_id}/acknowledge-recovery")
    async def acknowledge_recovery(
        device_id: str,
        session: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        actor = str(session.get("username") or "admin")
        try:
            device = store.acknowledge_update_recovery(device_id, actor=actor)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"device": device}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(
        job_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job does not exist.")
        return {"job": job, "events": store.list_job_events(job["id"])}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> dict[str, Any]:
        try:
            job = store.cancel_job(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"job": job}

    @app.post("/api/jobs/{job_id}/retry", status_code=201)
    async def retry_job(
        job_id: str,
        body: JobOverrideRequest,
        _: dict[str, object] = _ADMIN_CSRF_DEPENDENCY,
    ) -> JSONResponse:
        try:
            job = store.retry_job(job_id, override=body.override)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse({"job": job}, status_code=201)

    @app.get("/api/devices/{device_id}/mirror")
    async def download_mirror(
        device_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> FileResponse:
        device = store.get_device(device_id)
        path = store.mirror_path(device_id)
        if device is None or not path.exists():
            raise HTTPException(status_code=404, detail="No mirrored database is available.")
        expected_size = int(device["mirror_size"] or 0)
        if path.stat().st_size != expected_size:
            raise HTTPException(
                status_code=503,
                detail="The latest mirror size is invalid; request a new mirror.",
            )
        return FileResponse(
            path,
            headers={
                "Content-Disposition": f'attachment; filename="takt-{device_id}.sqlite3"',
                "ETag": f'"{device["mirror_sha256"]}"',
                "Cache-Control": "no-store, private",
            },
        )

    @app.get("/api/devices/{device_id}/diagnostics")
    async def device_diagnostics(
        device_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> dict[str, Any]:
        return {"diagnostics": store.list_diagnostics(device_id)}

    @app.get("/api/devices/{device_id}/diagnostics/{diagnostics_id}")
    async def download_diagnostics(
        device_id: str,
        diagnostics_id: str,
        _: dict[str, object] = _ADMIN_DEPENDENCY,
    ) -> FileResponse:
        bundle = store.get_diagnostics(diagnostics_id)
        if bundle is None or bundle["device_id"] != device_id:
            raise HTTPException(status_code=404, detail="Diagnostics bundle does not exist.")
        path = store.data_directory / bundle["relative_path"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Diagnostics bundle is no longer stored.")
        return FileResponse(
            path,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="takt-diagnostics-{bundle["id"]}.tar.gz"'
                ),
                "Cache-Control": "no-store, private",
            },
        )

    @app.post("/agent/enroll", status_code=201, response_model=DeviceTokenResponse)
    async def agent_enroll(body: EnrollmentRequest) -> JSONResponse:
        if any(
            not value for value in (body.enrollment_code, body.device_id, body.name, body.hostname)
        ):
            raise HTTPException(status_code=400, detail="Enrollment data is incomplete.")
        try:
            token = store.enroll_device(
                code=body.enrollment_code,
                device_id=body.device_id,
                name=body.name,
                hostname=body.hostname,
                token=body.device_token,
            )
        except ValueError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        return JSONResponse({"device_token": token}, status_code=201)

    @app.post("/agent/heartbeat", response_model=AgentHeartbeatResponse)
    async def agent_heartbeat(
        request: Request,
        body: HeartbeatRequest,
    ) -> dict[str, Any]:
        device_id = _device(request)
        payload = body.payload()
        store.update_heartbeat(device_id, payload)
        job = store.claim_next_job(device_id, str(payload.get("agent_session_id") or ""))
        if job and job["action"] == "install_release":
            release = store.get_release(job["payload"]["release_id"])
            job["release"] = release
        return {
            "job": job,
            "protocol_version": PROTOCOL_VERSION,
            "server_time": utc_iso(),
        }

    @app.post("/agent/status", response_model=AgentStatusResponse)
    async def agent_status(request: Request, body: HeartbeatRequest) -> dict[str, Any]:
        device_id = _device(request)
        store.update_heartbeat(device_id, body.payload())
        return {"protocol_version": PROTOCOL_VERSION, "server_time": utc_iso()}

    @app.post("/agent/jobs/{job_id}")
    async def agent_job_update(
        request: Request,
        job_id: str,
        body: JobUpdateRequest,
    ) -> dict[str, Any]:
        device_id = _device(request)
        try:
            job = store.update_job(
                job_id,
                device_id,
                body.status,
                body.progress,
                body.message,
                body.lease_id,
                stage=body.stage,
                bytes_downloaded=body.bytes_downloaded,
                bytes_total=body.bytes_total,
                result=body.result,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (TypeError, ValueError) as error:
            if any(
                marker in str(error).lower()
                for marker in ("lease", "completed jobs", "job transition", "expected version")
            ):
                raise HTTPException(status_code=409, detail=str(error)) from error
            raise HTTPException(status_code=400, detail=str(error)) from error
        if job["action"] == "run_health_checks" and job["status"] == "succeeded":
            report = _health_report_payload(body.result)
            if report is not None:
                store.record_health_checks(device_id, report)
        return {"job": job}

    @app.get("/agent/jobs/{job_id}/artifact")
    async def agent_artifact(request: Request, job_id: str) -> FileResponse:
        device_id = _device(request)
        job = store.job_for_device(job_id, device_id)
        if (
            job is None
            or job["action"] != "install_release"
            or job["status"] not in {"claimed", "running"}
            or not request.headers.get("X-Job-Lease")
            or not _secrets_compare(request.headers["X-Job-Lease"], str(job["lease_id"] or ""))
        ):
            raise HTTPException(status_code=404, detail="Release job does not exist.")
        release = store.get_release(job["payload"]["release_id"])
        if release is None:
            raise HTTPException(status_code=404, detail="Release does not exist.")
        return FileResponse(
            store.release_path(release["id"]),
            headers={
                "Content-Disposition": f'attachment; filename="takt-{release["version"]}.tar.gz"',
                "X-TAKT-SHA256": release["sha256"],
            },
        )

    @app.put("/agent/jobs/{job_id}/artifact")
    async def agent_diagnostics_upload(request: Request, job_id: str) -> JSONResponse:
        device_id = _device(request)
        job = store.job_for_device(job_id, device_id)
        if (
            job is None
            or job["action"] != "collect_diagnostics"
            or job["status"] not in {"claimed", "running"}
            or not request.headers.get("X-Job-Lease")
            or not _secrets_compare(request.headers["X-Job-Lease"], str(job["lease_id"] or ""))
        ):
            raise HTTPException(status_code=404, detail="Diagnostics job does not exist.")
        expected_sha = request.headers.get("X-TAKT-SHA256", "")
        temp_path, actual_sha, size = await _stream_upload(
            request,
            store.data_directory,
            maximum=MAX_DIAGNOSTICS_BYTES,
            timeout_seconds=5 * 60,
            label="Diagnostics",
        )
        try:
            if not expected_sha or actual_sha != expected_sha:
                raise HTTPException(status_code=400, detail="Diagnostics checksum does not match.")
            bundle = store.record_diagnostics(
                device_id, str(job["id"]), temp_path, actual_sha, size
            )
            return JSONResponse({"ok": True, "diagnostics_id": bundle["id"]})
        finally:
            temp_path.unlink(missing_ok=True)

    @app.post("/agent/mirror")
    async def agent_mirror(request: Request) -> JSONResponse:
        device_id = _device(request)
        active: set[str] = app.state.mirror_active
        attempts: dict[str, float] = app.state.mirror_last_attempt
        now = time.monotonic()
        if device_id in active:
            raise HTTPException(
                status_code=409, detail="A mirror upload is already active for this device."
            )
        if now - float(attempts.get(device_id, 0.0)) < 10:
            raise HTTPException(
                status_code=429,
                detail="Mirror uploads are limited to one attempt every 10 seconds.",
                headers={"Retry-After": "10"},
            )
        attempts[device_id] = now
        active.add(device_id)
        try:
            async with app.state.mirror_semaphore:
                expected_sha = request.headers.get("X-TAKT-SHA256", "")
                temp_path, actual_sha, size = await _stream_upload(
                    request,
                    store.data_directory,
                    maximum=MAX_MIRROR_BYTES,
                    timeout_seconds=10 * 60,
                    label="Mirror",
                )
                try:
                    if not expected_sha or actual_sha != expected_sha:
                        raise HTTPException(
                            status_code=400, detail="Database checksum does not match."
                        )
                    run_count = await asyncio.to_thread(_validate_mirror, temp_path)
                    existing_blob = store.mirror_blob_path(device_id, actual_sha)
                    existing_blob_valid = None
                    if existing_blob is not None:
                        existing_blob_valid = await asyncio.to_thread(
                            _file_matches, existing_blob, actual_sha, size
                        )
                    _device(request)
                    store.record_mirror(
                        device_id,
                        temp_path,
                        actual_sha,
                        size,
                        run_count,
                        existing_blob_valid=existing_blob_valid,
                    )
                    return JSONResponse({"ok": True, "run_count": run_count})
                finally:
                    temp_path.unlink(missing_ok=True)
        finally:
            active.discard(device_id)

    if (STATIC_ROOT / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=STATIC_ROOT / "assets"),
            name="assets",
        )
    if STATIC_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    app.add_middleware(
        MultipartBodyLimitMiddleware,
        route_lookup=lambda method, path: _route_path(app, method, path),
    )
    return app
