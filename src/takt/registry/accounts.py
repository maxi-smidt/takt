from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
PASSWORD_MIN_LENGTH = 12
SESSION_TTL = timedelta(hours=12)
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_username(username: str) -> str:
    value = unicodedata.normalize("NFKC", username).strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Username must contain 3-64 letters, numbers, dots, underscores, or hyphens."
        )
    return value.casefold()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=64 * 1024 * 1024,
    )
    return f"$scrypt$ln=15,r=8,p=1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = encoded.split("$")
        if len(parts) != 5 or parts[0] != "":
            return False
        _, algorithm, parameters, encoded_salt, encoded_digest = parts
        if algorithm != "scrypt":
            return False
        values = dict(part.split("=", 1) for part in parameters.split(","))
        n = 2 ** int(values["ln"])
        r = int(values["r"])
        p = int(values["p"])
        salt = _unb64(encoded_salt)
        expected = _unb64(encoded_digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, KeyError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


class AccountStore:
    """Persistent Registry accounts, sessions, and device ACLs."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        # Schema is established by the Registry's Alembic migrations (see
        # `takt.registry.migrations`) before a RegistryStore ever constructs
        # an AccountStore, so there is no schema setup to do here.
        self.connection = connection
        self._dummy_password_hash = hash_password(secrets.token_urlsafe(24))

    def has_users(self) -> bool:
        return self.connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def bootstrap_admin(self, username: str, password: str) -> dict[str, Any]:
        if self.has_users():
            raise ValueError("Registry users already exist; bootstrap is no longer available.")
        return self.create_user(username, password, is_admin=True, must_change_password=False)

    def create_user(
        self,
        username: str,
        password: str,
        *,
        is_admin: bool = False,
        must_change_password: bool = False,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        display_name = unicodedata.normalize("NFKC", username).strip()
        username_key = normalize_username(display_name)
        password_hash = hash_password(password)
        user_id = secrets.token_hex(16)
        now = utc_iso()
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO users(
                        id, username, username_key, password_hash, is_admin,
                        must_change_password, created_at, updated_at, password_changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        display_name,
                        username_key,
                        password_hash,
                        int(is_admin),
                        int(must_change_password),
                        now,
                        now,
                        now,
                    ),
                )
                self._audit(
                    "user_created",
                    actor_user_id=actor_user_id,
                    target_user_id=user_id,
                    details={"username": display_name, "is_admin": bool(is_admin)},
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("That username already exists.") from error
        return self.public_user(user_id)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        try:
            key = normalize_username(username)
        except ValueError:
            key = ""
        row = self.connection.execute(
            "SELECT * FROM users WHERE username_key = ?", (key,)
        ).fetchone()
        encoded = row["password_hash"] if row is not None else self._dummy_password_hash
        valid = verify_password(password, encoded)
        if row is None or not valid or row["disabled_at"] is not None:
            return None
        return self.public_user_row(row)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self.public_user_row(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM users ORDER BY username_key").fetchall()
        users = []
        for row in rows:
            user = self.public_user_row(row)
            user["access"] = [
                dict(access)
                for access in self.connection.execute(
                    """
                    SELECT device_id, access_level, granted_at, granted_by
                    FROM device_access WHERE user_id = ? ORDER BY device_id
                    """,
                    (row["id"],),
                ).fetchall()
            ]
            users.append(user)
        return users

    def set_user_state(
        self,
        user_id: str,
        *,
        disabled: bool | None = None,
        is_admin: bool | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        if disabled is not None and not isinstance(disabled, bool):
            raise ValueError("disabled must be a boolean.")
        if is_admin is not None and not isinstance(is_admin, bool):
            raise ValueError("is_admin must be a boolean.")
        user = self.get_user(user_id)
        if user is None:
            raise LookupError("User does not exist.")
        enabled_admins = self._enabled_admin_count(exclude_user_id=user_id)
        if disabled is True and user["is_admin"] and enabled_admins == 0:
            raise ValueError("The last enabled administrator cannot be disabled.")
        if is_admin is False and user["is_admin"] and enabled_admins == 0:
            raise ValueError("The last enabled administrator cannot lose administrator access.")
        changes: dict[str, Any] = {}
        if disabled is not None:
            changes["disabled_at"] = utc_iso() if disabled else None
        if is_admin is not None:
            changes["is_admin"] = int(is_admin)
        if not changes:
            return user
        assignments = [f"{key} = ?" for key in changes]
        now = utc_iso()
        values = [*changes.values(), now, user_id]
        with self.connection:
            self.connection.execute(
                f"UPDATE users SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                values,
            )
            if disabled is True:
                self.revoke_user_sessions(user_id)
            self._audit(
                "user_state_changed",
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                details={"disabled": disabled, "is_admin": is_admin},
            )
        return self.get_user(user_id) or user

    def reset_password(
        self, user_id: str, password: str, *, actor_user_id: str | None = None
    ) -> dict[str, Any]:
        if self.get_user(user_id) is None:
            raise LookupError("User does not exist.")
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE users SET password_hash = ?, must_change_password = 1,
                    password_changed_at = ?, updated_at = ? WHERE id = ?
                """,
                (hash_password(password), now, now, user_id),
            )
            self.revoke_user_sessions(user_id)
            self._audit(
                "user_password_reset",
                actor_user_id=actor_user_id,
                target_user_id=user_id,
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def change_password(self, user_id: str, current: str, new: str) -> None:
        row = self.connection.execute(
            "SELECT password_hash FROM users WHERE id = ? AND disabled_at IS NULL", (user_id,)
        ).fetchone()
        if row is None or not verify_password(current, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE users SET password_hash = ?, must_change_password = 0,
                    password_changed_at = ?, updated_at = ? WHERE id = ?
                """,
                (hash_password(new), now, now, user_id),
            )
            self._audit("user_password_changed", actor_user_id=user_id, target_user_id=user_id)

    def grant_access(
        self,
        user_id: str,
        device_id: str,
        access_level: str,
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        if access_level not in {"read", "write"}:
            raise ValueError("Access must be read or write.")
        if self.get_user(user_id) is None:
            raise LookupError("User does not exist.")
        if (
            self.connection.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone()
            is None
        ):
            raise LookupError("Device does not exist.")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO device_access(user_id, device_id, access_level, granted_at, granted_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_id) DO UPDATE SET
                    access_level = excluded.access_level,
                    granted_at = excluded.granted_at,
                    granted_by = excluded.granted_by
                """,
                (user_id, device_id, access_level, utc_iso(), actor_user_id),
            )
            self._audit(
                "device_access_changed",
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                device_id=device_id,
                details={"access": access_level},
            )
        return {"user_id": user_id, "device_id": device_id, "access_level": access_level}

    def revoke_access(
        self, user_id: str, device_id: str, *, actor_user_id: str | None = None
    ) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM device_access WHERE user_id = ? AND device_id = ?",
                (user_id, device_id),
            )
            if cursor.rowcount:
                self._audit(
                    "device_access_revoked",
                    actor_user_id=actor_user_id,
                    target_user_id=user_id,
                    device_id=device_id,
                )
        return bool(cursor.rowcount)

    def access_level(self, user_id: str, device_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT access_level FROM device_access WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
        ).fetchone()
        return str(row["access_level"]) if row else None

    def create_session(self, user_id: str) -> tuple[str, dict[str, Any]]:
        user = self.get_user(user_id)
        if user is None:
            raise LookupError("User does not exist.")
        if user["disabled"]:
            raise ValueError("Disabled users cannot create sessions.")
        token = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(24)
        now = datetime.now(UTC)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO user_sessions(
                    token_hash, user_id, csrf_token, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    csrf,
                    now.isoformat(),
                    (now + SESSION_TTL).isoformat(),
                    now.isoformat(),
                ),
            )
            self._audit("session_created", actor_user_id=user_id)
        return token, {"csrf": csrf, "expires_at": (now + SESSION_TTL).isoformat()}

    def verify_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            LOGGER.info("session_verify_failed reason=missing_cookie")
            return None
        row = self.connection.execute(
            """
            SELECT sessions.*, users.username, users.is_admin,
                   users.disabled_at, users.must_change_password
            FROM user_sessions AS sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
            """,
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        if row is None:
            LOGGER.info("session_verify_failed reason=unknown_token")
            return None
        if row["revoked_at"] is not None:
            LOGGER.info("session_verify_failed reason=revoked user_id=%s", row["user_id"])
            return None
        if row["disabled_at"] is not None:
            LOGGER.info("session_verify_failed reason=disabled_user user_id=%s", row["user_id"])
            return None
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            LOGGER.info(
                "session_verify_failed reason=expired user_id=%s expires_at=%s",
                row["user_id"],
                row["expires_at"],
            )
            return None
        with self.connection:
            self.connection.execute(
                "UPDATE user_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (utc_iso(), row["token_hash"]),
            )
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "must_change_password": bool(row["must_change_password"]),
            "csrf": row["csrf_token"],
            "expires_at": row["expires_at"],
        }

    def revoke_session(self, token: str, *, actor_user_id: str | None = None) -> None:
        if not token:
            return
        with self.connection:
            row = self.connection.execute(
                "SELECT user_id FROM user_sessions WHERE token_hash = ?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            self.connection.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ?",
                (utc_iso(), hashlib.sha256(token.encode()).hexdigest()),
            )
            self._audit(
                "session_revoked", actor_user_id=actor_user_id or (row["user_id"] if row else None)
            )

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (utc_iso(), user_id),
            )

    def public_user(self, user_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise LookupError("User does not exist.")
        return self.public_user_row(row)

    @staticmethod
    def public_user_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "disabled": row["disabled_at"] is not None,
            "must_change_password": bool(row["must_change_password"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _enabled_admin_count(self, *, exclude_user_id: str | None = None) -> int:
        if exclude_user_id is None:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled_at IS NULL"
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM users "
                "WHERE is_admin = 1 AND disabled_at IS NULL AND id != ?",
                (exclude_user_id,),
            ).fetchone()
        return int(row[0])

    def _audit(
        self,
        event: str,
        *,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        device_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
            INSERT INTO audit_events(
                created_at, event, device_id, details_json, actor_user_id, target_user_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    utc_iso(),
                    event,
                    device_id,
                    json.dumps(details or {}, separators=(",", ":")),
                    actor_user_id,
                    target_user_id,
                ),
            )
