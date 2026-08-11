from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from aiohttp import web

COOKIE_NAME = "takt_registry_session"


class AdminAuth:
    def __init__(self, password: str, data_directory: Path) -> None:
        if len(password) < 10:
            raise ValueError("The registry admin password must contain at least 10 characters.")
        salt_path = data_directory / "session.salt"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = secrets.token_bytes(32)
            salt_path.write_bytes(salt)
            salt_path.chmod(0o600)
        self._password = password
        self._key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000, dklen=32)

    def authenticate(self, password: str) -> str | None:
        if not hmac.compare_digest(password.encode("utf-8"), self._password.encode("utf-8")):
            return None
        csrf = secrets.token_urlsafe(24)
        payload = self._encode(
            json.dumps(
                {"exp": int(time.time()) + 12 * 60 * 60, "csrf": csrf},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = self._encode(hmac.new(self._key, payload, hashlib.sha256).digest())
        return f"{payload.decode()}.{signature.decode()}"

    def session(self, request: web.Request, *, csrf: bool = False) -> dict[str, object]:
        token = request.cookies.get(COOKIE_NAME, "")
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = encoded_payload.encode("ascii")
            supplied_signature = self._decode(encoded_signature)
            expected_signature = hmac.new(self._key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError
            data = json.loads(self._decode(encoded_payload))
            if int(data["exp"]) < int(time.time()):
                raise ValueError
            if csrf and not hmac.compare_digest(
                str(data["csrf"]), request.headers.get("X-CSRF-Token", "")
            ):
                raise web.HTTPForbidden(text="CSRF validation failed.")
            return data
        except web.HTTPException:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise web.HTTPUnauthorized(text="Administrator login required.") from None

    @staticmethod
    def _encode(value: bytes) -> bytes:
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    @staticmethod
    def _decode(value: str) -> bytes:
        encoded = value.encode("ascii")
        return base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
