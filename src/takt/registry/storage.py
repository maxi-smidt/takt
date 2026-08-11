from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    last_seen_at TEXT,
    app_version TEXT,
    agent_version TEXT,
    status_json TEXT NOT NULL DEFAULT '{}',
    last_mirror_at TEXT,
    mirror_sha256 TEXT,
    mirror_size INTEGER,
    run_count INTEGER
);

CREATE TABLE IF NOT EXISTS enrollment_codes (
    code_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_device_status
ON jobs(device_id, status, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    device_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
"""


class RegistryStore:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory
        self.release_directory = data_directory / "releases"
        self.mirror_directory = data_directory / "mirrors"
        self.release_directory.mkdir(parents=True, exist_ok=True)
        self.mirror_directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(data_directory / "registry.db")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_enrollment_code(self, label: str = "", minutes: int = 15) -> str:
        code = f"TAKT-{secrets.token_urlsafe(18)}"
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO enrollment_codes(code_hash, created_at, expires_at, label)
                VALUES (?, ?, ?, ?)
                """,
                (hash_secret(code), utc_iso(now), utc_iso(now + timedelta(minutes=minutes)), label),
            )
            self._audit("enrollment_code_created", details={"label": label})
        return code

    def enroll_device(
        self,
        *,
        code: str,
        device_id: str,
        name: str,
        hostname: str,
    ) -> str:
        now = utc_now()
        row = self.connection.execute(
            """
            SELECT * FROM enrollment_codes
            WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (hash_secret(code), utc_iso(now)),
        ).fetchone()
        if row is None:
            raise ValueError("Enrollment code is invalid, expired, or already used.")
        if self.connection.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone():
            raise ValueError("This device ID is already enrolled.")
        token = secrets.token_urlsafe(36)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO devices(id, name, hostname, token_hash, enrolled_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, name, hostname, hash_secret(token), utc_iso(now)),
            )
            self.connection.execute(
                "UPDATE enrollment_codes SET used_at = ? WHERE code_hash = ?",
                (utc_iso(now), hash_secret(code)),
            )
            self._audit("device_enrolled", device_id, {"name": name, "hostname": hostname})
        return token

    def authenticate_device(self, device_id: str, token: str) -> bool:
        row = self.connection.execute(
            "SELECT token_hash FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        return row is not None and secrets.compare_digest(row["token_hash"], hash_secret(token))

    def update_heartbeat(self, device_id: str, payload: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE devices
                SET name = ?, hostname = ?, last_seen_at = ?, app_version = ?,
                    agent_version = ?, status_json = ?
                WHERE id = ?
                """,
                (
                    str(payload.get("name") or "TAKT"),
                    str(payload.get("hostname") or "unknown"),
                    utc_iso(),
                    payload.get("app_version"),
                    payload.get("agent_version"),
                    json.dumps(payload, separators=(",", ":")),
                    device_id,
                ),
            )

    def list_devices(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM devices ORDER BY name COLLATE NOCASE, enrolled_at"
        ).fetchall()
        now = utc_now()
        devices: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["status"] = json.loads(item.pop("status_json"))
            last_seen = item.get("last_seen_at")
            item["online"] = bool(
                last_seen and now - datetime.fromisoformat(last_seen) < timedelta(seconds=45)
            )
            item.pop("token_hash", None)
            devices.append(item)
        return devices

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_devices() if item["id"] == device_id), None)

    def add_release(
        self, *, version: str, filename: str, sha256: str, size: int, source: Path
    ) -> dict[str, Any]:
        release_id = secrets.token_hex(12)
        target = self.release_path(release_id)
        source.replace(target)
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO releases(id, version, filename, sha256, size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (release_id, version, filename, sha256, size, utc_iso()),
                )
                self._audit("release_uploaded", details={"version": version, "sha256": sha256})
        except Exception:
            target.unlink(missing_ok=True)
            raise
        release = self.get_release(release_id)
        assert release is not None
        return release

    def list_releases(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM releases ORDER BY created_at DESC"
            ).fetchall()
        ]

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        return dict(row) if row else None

    def release_path(self, release_id: str) -> Path:
        return self.release_directory / f"{release_id}.tar.gz"

    def create_job(
        self, device_id: str, action: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.get_device(device_id) is None:
            raise LookupError("Device does not exist.")
        if action == "install_release":
            release_id = str((payload or {}).get("release_id", ""))
            if self.get_release(release_id) is None:
                raise ValueError("Release does not exist.")
        job_id = secrets.token_hex(12)
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobs(
                    id, device_id, action, payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, device_id, action, json.dumps(payload or {}), now, now),
            )
            self._audit("job_created", device_id, {"job_id": job_id, "action": action})
        job = self.get_job(job_id)
        assert job is not None
        return job

    def claim_next_job(self, device_id: str) -> dict[str, Any] | None:
        stale_before = utc_iso(utc_now() - timedelta(minutes=10))
        with self.connection:
            self.connection.execute(
                """
                UPDATE jobs SET status = 'queued', claimed_at = NULL,
                    message = 'Agent reconnected; retrying job', updated_at = ?
                WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND updated_at < ?
                """,
                (utc_iso(), device_id, stale_before),
            )
        row = self.connection.execute(
            """
            SELECT id FROM jobs
            WHERE device_id = ? AND status = 'queued'
            ORDER BY created_at LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE jobs SET status = 'claimed', claimed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
        return self.get_job(row["id"])

    def update_job(
        self, job_id: str, device_id: str, status: str, progress: int, message: str
    ) -> dict[str, Any]:
        allowed = {"queued", "claimed", "running", "succeeded", "failed", "rolled_back"}
        if status not in allowed:
            raise ValueError("Invalid job status.")
        completed_at = utc_iso() if status in {"succeeded", "failed", "rolled_back"} else None
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE jobs SET status = ?, progress = ?, message = ?, updated_at = ?,
                    completed_at = COALESCE(?, completed_at),
                    claimed_at = CASE WHEN ? = 'queued' THEN NULL ELSE claimed_at END
                WHERE id = ? AND device_id = ?
                """,
                (
                    status,
                    min(max(progress, 0), 100),
                    message[:2000],
                    utc_iso(),
                    completed_at,
                    status,
                    job_id,
                    device_id,
                ),
            )
            if not cursor.rowcount:
                raise LookupError("Job does not exist.")
            if completed_at:
                self._audit("job_completed", device_id, {"job_id": job_id, "status": status})
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._job(row)
            for row in self.connection.execute(
                """
                SELECT jobs.*, devices.name AS device_name
                FROM jobs JOIN devices ON devices.id = jobs.device_id
                ORDER BY jobs.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    def job_for_device(self, job_id: str, device_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        return job if job and job["device_id"] == device_id else None

    def record_mirror(
        self, device_id: str, source: Path, sha256: str, size: int, run_count: int
    ) -> None:
        target = self.mirror_path(device_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        with self.connection:
            self.connection.execute(
                """
                UPDATE devices SET last_mirror_at = ?, mirror_sha256 = ?,
                    mirror_size = ?, run_count = ? WHERE id = ?
                """,
                (utc_iso(), sha256, size, run_count, device_id),
            )
            self._audit("mirror_received", device_id, {"sha256": sha256, "size": size})

    def mirror_path(self, device_id: str) -> Path:
        return self.mirror_directory / f"{device_id}.sqlite3"

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def _audit(
        self,
        event: str,
        device_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events(created_at, event, device_id, details_json)
            VALUES (?, ?, ?, ?)
            """,
            (utc_iso(), event, device_id, json.dumps(details or {})),
        )
