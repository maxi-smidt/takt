from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from takt import __version__
from takt.registry.job_secrets import JobSecretCipher, JobSecretError


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCHEMA_VERSION = 7
JOB_LEASE_SECONDS = 120
WIFI_PROFILE_CAPABILITY = "wifi-profile-v1"


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
    run_count INTEGER,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS enrollment_codes (
    code_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    label TEXT NOT NULL DEFAULT '',
    deployment_id TEXT
);

CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    port INTEGER NOT NULL,
    ssh_user TEXT NOT NULL,
    device_name TEXT NOT NULL,
    requested_hostname TEXT NOT NULL,
    registry_url TEXT NOT NULL,
    allow_insecure_http INTEGER NOT NULL DEFAULT 0,
    release_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    host_key TEXT,
    host_key_fingerprint TEXT,
    device_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_deployments_target_status
ON deployments(target, port, status, created_at);

CREATE TABLE IF NOT EXISTS deployment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deployment_events_deployment
ON deployment_events(deployment_id, id);

CREATE TABLE IF NOT EXISTS trusted_ssh_hosts (
    target_key TEXT PRIMARY KEY,
    host_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    trusted_at TEXT NOT NULL
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
    completed_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_id TEXT,
    lease_expires_at TEXT,
    lease_owner_session TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_device_status
ON jobs(device_id, status, created_at);

CREATE TABLE IF NOT EXISTS job_secrets (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    device_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mirror_snapshots (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    received_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    run_count INTEGER NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    UNIQUE(device_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_mirror_snapshots_device_received
ON mirror_snapshots(device_id, received_at DESC);
"""


class RegistryStore:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory
        self.database_path = data_directory / "registry.db"
        self.job_secret_key_path = data_directory / "job-secrets.key"
        self.release_directory = data_directory / "releases"
        self.mirror_directory = data_directory / "mirrors"
        self.backup_directory = data_directory / "backups"
        self.release_directory.mkdir(parents=True, exist_ok=True)
        self.mirror_directory.mkdir(parents=True, exist_ok=True)
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        database_existed = self.database_path.exists() and self.database_path.stat().st_size > 0
        self.connection = sqlite3.connect(self.database_path, timeout=10)
        self.database_path.chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        self._job_secret_cipher: JobSecretCipher | None = None
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        previous_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if previous_version > SCHEMA_VERSION:
            self.connection.close()
            raise RuntimeError(
                f"Registry database schema {previous_version} is newer than this "
                f"server supports ({SCHEMA_VERSION})."
            )
        if database_existed and previous_version < SCHEMA_VERSION:
            self.backup_database(label=f"pre-migration-v{previous_version}", retain=20)
        self.connection.executescript(SCHEMA)
        self._ensure_column("jobs", "attempt", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("jobs", "lease_id", "TEXT")
        self._ensure_column("jobs", "lease_expires_at", "TEXT")
        self._ensure_column("jobs", "lease_owner_session", "TEXT")
        self._ensure_column("devices", "revoked_at", "TEXT")
        self._ensure_column("enrollment_codes", "deployment_id", "TEXT")
        self.connection.execute(
            """
            UPDATE deployments SET status = 'interrupted', stage = 'interrupted',
                message = 'Registry restarted while deployment was active', updated_at = ?
            WHERE status IN ('pending', 'running')
            """,
            (utc_iso(),),
        )
        self.connection.execute(
            """
            UPDATE jobs SET status = 'queued', claimed_at = NULL,
                message = 'Registry upgraded; retrying job safely'
            WHERE status IN ('claimed', 'running') AND lease_expires_at IS NULL
            """
        )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()
        self.prune()

    def close(self) -> None:
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.close()

    def create_enrollment_code(
        self, label: str = "", minutes: int = 60, deployment_id: str | None = None
    ) -> str:
        code = f"TAKT-{secrets.token_urlsafe(18)}"
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO enrollment_codes(
                    code_hash, created_at, expires_at, label, deployment_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    hash_secret(code),
                    utc_iso(now),
                    utc_iso(now + timedelta(minutes=minutes)),
                    label,
                    deployment_id,
                ),
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
        token: str | None = None,
    ) -> str:
        now = utc_now()
        existing = self.connection.execute(
            "SELECT token_hash, revoked_at FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        if existing is not None:
            if existing["revoked_at"]:
                raise ValueError("This device has been revoked by the registry administrator.")
            if token and secrets.compare_digest(existing["token_hash"], hash_secret(token)):
                with self.connection:
                    consumed = self.connection.execute(
                        """
                        UPDATE enrollment_codes SET used_at = ?
                        WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?
                        """,
                        (utc_iso(now), hash_secret(code), utc_iso(now)),
                    )
                    if consumed.rowcount:
                        self._audit("enrollment_code_consumed", device_id)
                    self._link_deployment_for_code(hash_secret(code), device_id)
                return token
            raise ValueError("This device ID is already enrolled with another secret.")
        code_hash = hash_secret(code)
        token = token or secrets.token_urlsafe(36)
        with self.connection:
            consumed = self.connection.execute(
                """
                UPDATE enrollment_codes SET used_at = ?
                WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?
                RETURNING code_hash
                """,
                (utc_iso(now), code_hash, utc_iso(now)),
            ).fetchone()
            if consumed is None:
                raise ValueError("Enrollment code is invalid, expired, or already used.")
            self.connection.execute(
                """
                INSERT INTO devices(id, name, hostname, token_hash, enrolled_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, name, hostname, hash_secret(token), utc_iso(now)),
            )
            self._link_deployment_for_code(code_hash, device_id)
            self._audit("device_enrolled", device_id, {"name": name, "hostname": hostname})
        return token

    def authenticate_device(self, device_id: str, token: str) -> bool:
        row = self.connection.execute(
            "SELECT token_hash FROM devices WHERE id = ? AND revoked_at IS NULL", (device_id,)
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
            heartbeat_interval = float(item["status"].get("poll_seconds") or 10)
            online_window = min(max(heartbeat_interval * 3 + 15, 30), 180)
            item["online"] = bool(
                last_seen
                and not item.get("revoked_at")
                and now - datetime.fromisoformat(last_seen) < timedelta(seconds=online_window)
            )
            item.pop("token_hash", None)
            devices.append(item)
        return devices

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_devices() if item["id"] == device_id), None)

    def revoke_device(self, device_id: str) -> dict[str, Any]:
        now = utc_iso()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, device_id),
            )
            if not cursor.rowcount:
                device = self.get_device(device_id)
                if device is None:
                    raise LookupError("Device does not exist.")
                return device
            self.connection.execute(
                """
                DELETE FROM job_secrets WHERE job_id IN (
                    SELECT id FROM jobs
                    WHERE device_id = ? AND status IN ('queued', 'claimed', 'running')
                )
                """,
                (device_id,),
            )
            self.connection.execute(
                """
                UPDATE jobs SET status = 'failed', progress = 100,
                    message = 'Device access was revoked', updated_at = ?, completed_at = ?,
                    lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                WHERE device_id = ? AND status IN ('queued', 'claimed', 'running')
                """,
                (now, now, device_id),
            )
            self._audit("device_revoked", device_id)
        device = self.get_device(device_id)
        assert device is not None
        return device

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
        device = self.get_device(device_id)
        if device is None:
            raise LookupError("Device does not exist.")
        if device.get("revoked_at"):
            raise ValueError("Device access has been revoked.")
        protocol_version = device.get("status", {}).get("protocol_version")
        has_heartbeat = bool(device.get("status"))
        if action in {"install_release", "restart_takt"} and (
            protocol_version != 1 and (has_heartbeat or protocol_version is not None)
        ):
            raise ValueError(
                "This Pi agent is incompatible with safe remote operations; update it once via SSH."
            )
        active = self.connection.execute(
            """
            SELECT 1 FROM jobs WHERE device_id = ?
                AND status IN ('queued', 'claimed', 'running')
                AND action IN ('install_release', 'restart_takt')
            """,
            (device_id,),
        ).fetchone()
        if active is not None and action in {"install_release", "restart_takt"}:
            raise ValueError("Another disruptive operation is already queued for this device.")
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

    def create_wifi_job(self, device_id: str, ssid: str, password: str) -> dict[str, Any]:
        device = self.get_device(device_id)
        if device is None:
            raise LookupError("Device does not exist.")
        if device.get("revoked_at"):
            raise ValueError("Device access has been revoked.")
        if not device.get("online"):
            raise ValueError("Device must be online to add a Wi-Fi network.")
        capabilities = device.get("status", {}).get("capabilities", [])
        if WIFI_PROFILE_CAPABILITY not in capabilities:
            raise ValueError(
                "This Pi agent cannot manage Wi-Fi profiles; update it once via SSH."
            )
        job_id = secrets.token_hex(12)
        action = "add_wifi_network"
        now = utc_iso()
        cipher = self._get_job_secret_cipher(create=True)
        nonce, ciphertext = cipher.encrypt(
            password,
            associated_data=self._job_secret_aad(job_id, device_id, action),
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobs(
                    id, device_id, action, payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id,
                    device_id,
                    action,
                    json.dumps({"ssid": ssid, "priority": 0}, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO job_secrets(job_id, nonce, ciphertext, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, nonce, ciphertext, now),
            )
            self._audit("job_created", device_id, {"job_id": job_id, "action": action})
        job = self.get_job(job_id)
        assert job is not None
        return job

    def claim_next_job(self, device_id: str, agent_session_id: str = "") -> dict[str, Any] | None:
        now_value = utc_now()
        now = utc_iso(now_value)
        with self.connection:
            self.connection.execute(
                """
                UPDATE jobs SET status = 'queued', claimed_at = NULL, lease_id = NULL,
                    lease_expires_at = NULL, lease_owner_session = NULL,
                    message = 'Job lease expired; retrying safely', updated_at = ?
                WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (now, device_id, now),
            )
        active = self.connection.execute(
            """
            SELECT id FROM jobs
            WHERE device_id = ? AND status IN ('claimed', 'running')
                AND lease_expires_at >= ?
            ORDER BY created_at LIMIT 1
            """,
            (device_id, now),
        ).fetchone()
        if active is not None:
            active_job = self.get_job(active["id"])
            if active_job and active_job.get("lease_owner_session") == agent_session_id:
                return self._attach_job_secret(active_job)
            return None
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
        lease_id = secrets.token_urlsafe(18)
        lease_expires_at = utc_iso(now_value + timedelta(seconds=JOB_LEASE_SECONDS))
        with self.connection:
            self.connection.execute(
                """
                UPDATE jobs SET status = 'claimed', claimed_at = ?, updated_at = ?,
                    attempt = attempt + 1, lease_id = ?, lease_expires_at = ?,
                    lease_owner_session = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, lease_id, lease_expires_at, agent_session_id, row["id"]),
            )
        job = self.get_job(row["id"])
        return self._attach_job_secret(job) if job else None

    def update_job(
        self,
        job_id: str,
        device_id: str,
        status: str,
        progress: int,
        message: str,
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        allowed = {"queued", "claimed", "running", "succeeded", "failed", "rolled_back"}
        if status not in allowed:
            raise ValueError("Invalid job status.")
        current = self.connection.execute(
            "SELECT status, lease_id FROM jobs WHERE id = ? AND device_id = ?",
            (job_id, device_id),
        ).fetchone()
        if current is None:
            raise LookupError("Job does not exist.")
        terminal = {"succeeded", "failed", "rolled_back"}
        if current["status"] in terminal:
            if status == current["status"]:
                job = self.get_job(job_id)
                assert job is not None
                return job
            raise ValueError("Completed jobs cannot change state.")
        transitions = {
            "queued": {"claimed"},
            "claimed": {"queued", "running", *terminal},
            "running": {"queued", "running", *terminal},
        }
        if status != current["status"] and status not in transitions[current["status"]]:
            raise ValueError(f"Invalid job transition: {current['status']} -> {status}.")
        if current["status"] in {"claimed", "running"}:
            if not lease_id or not current["lease_id"]:
                raise ValueError("A job lease is required for this operation.")
            if not secrets.compare_digest(lease_id, current["lease_id"]):
                raise ValueError("Job lease no longer belongs to this agent operation.")
        completed_at = utc_iso() if status in {"succeeded", "failed", "rolled_back"} else None
        lease_expires_at = (
            utc_iso(utc_now() + timedelta(seconds=JOB_LEASE_SECONDS))
            if status in {"claimed", "running"}
            else None
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE jobs SET status = ?, progress = ?, message = ?, updated_at = ?,
                    completed_at = COALESCE(?, completed_at),
                    claimed_at = CASE WHEN ? = 'queued' THEN NULL ELSE claimed_at END,
                    lease_id = CASE WHEN ? IN ('queued', 'succeeded', 'failed', 'rolled_back')
                        THEN NULL ELSE lease_id END,
                    lease_owner_session = CASE
                        WHEN ? IN ('queued', 'succeeded', 'failed', 'rolled_back')
                        THEN NULL ELSE lease_owner_session END,
                    lease_expires_at = ?
                WHERE id = ? AND device_id = ?
                """,
                (
                    status,
                    min(max(progress, 0), 100),
                    message[:2000],
                    utc_iso(),
                    completed_at,
                    status,
                    status,
                    status,
                    lease_expires_at,
                    job_id,
                    device_id,
                ),
            )
            if not cursor.rowcount:
                raise LookupError("Job does not exist.")
            if completed_at:
                self._audit("job_completed", device_id, {"job_id": job_id, "status": status})
                self.connection.execute("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
            self.connection.execute(
                "UPDATE devices SET last_seen_at = ? WHERE id = ?", (utc_iso(), device_id)
            )
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
        self,
        device_id: str,
        source: Path,
        sha256: str,
        size: int,
        run_count: int,
        *,
        existing_blob_valid: bool | None = None,
    ) -> None:
        existing = self.connection.execute(
            "SELECT relative_path FROM mirror_snapshots WHERE device_id = ? AND sha256 = ?",
            (device_id, sha256),
        ).fetchone()
        if existing is not None:
            existing_path = self.data_directory / existing["relative_path"]
            existing_valid = existing_blob_valid
            if existing_valid is None:
                existing_valid = (
                    existing_path.is_file()
                    and existing_path.stat().st_size == size
                    and self._sha256_file(existing_path) == sha256
                )
            if existing_valid:
                source.unlink(missing_ok=True)
            else:
                existing_path.parent.mkdir(parents=True, exist_ok=True)
                source.replace(existing_path)
                existing_path.chmod(0o600)
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE devices SET last_mirror_at = ?, mirror_sha256 = ?,
                        mirror_size = ?, run_count = ? WHERE id = ?
                    """,
                    (utc_iso(), sha256, size, run_count, device_id),
                )
                self._audit("mirror_received", device_id, {"sha256": sha256, "size": size})
            self._prune_mirror_snapshots(device_id, recent=48, daily=30)
            return
        received_at = utc_now()
        snapshot_id = secrets.token_hex(12)
        relative_path = (
            Path("mirrors")
            / device_id
            / f"{received_at.strftime('%Y%m%dT%H%M%SZ')}-{sha256[:12]}.sqlite3"
        )
        target = self.data_directory / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        target.chmod(0o600)
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO mirror_snapshots(
                        id, device_id, received_at, sha256, size, run_count, relative_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        device_id,
                        utc_iso(received_at),
                        sha256,
                        size,
                        run_count,
                        str(relative_path),
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE devices SET last_mirror_at = ?, mirror_sha256 = ?,
                        mirror_size = ?, run_count = ? WHERE id = ?
                    """,
                    (utc_iso(received_at), sha256, size, run_count, device_id),
                )
                self._audit("mirror_received", device_id, {"sha256": sha256, "size": size})
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self._prune_mirror_snapshots(device_id, recent=48, daily=30)

    def mirror_blob_path(self, device_id: str, sha256: str) -> Path | None:
        row = self.connection.execute(
            """
            SELECT relative_path FROM mirror_snapshots
            WHERE device_id = ? AND sha256 = ?
            """,
            (device_id, sha256),
        ).fetchone()
        return self.data_directory / row["relative_path"] if row is not None else None

    def mirror_path(self, device_id: str) -> Path:
        row = self.connection.execute(
            """
            SELECT snapshots.relative_path
            FROM devices
            JOIN mirror_snapshots AS snapshots
              ON snapshots.device_id = devices.id
             AND snapshots.sha256 = devices.mirror_sha256
            WHERE devices.id = ?
            """,
            (device_id,),
        ).fetchone()
        if row is not None:
            return self.data_directory / row["relative_path"]
        return self.mirror_directory / f"{device_id}.sqlite3"

    def health(self) -> dict[str, Any]:
        try:
            self.connection.execute("SELECT 1").fetchone()
            schema_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            database_ok = schema_version == SCHEMA_VERSION
        except sqlite3.Error:
            schema_version = -1
            database_ok = False
        latest_backup = self.latest_backup()
        disk_free = shutil.disk_usage(self.data_directory).free
        return {
            "ok": database_ok and disk_free > 1024 * 1024,
            "service": "takt-registry",
            "version": __version__,
            "schema_version": schema_version,
            "database": "ready" if database_ok else "unavailable",
            "database_size": self.database_path.stat().st_size
            if self.database_path.exists()
            else 0,
            "disk_free_bytes": disk_free,
            "last_backup_at": datetime.fromtimestamp(latest_backup.stat().st_mtime, UTC).isoformat()
            if latest_backup
            else None,
        }

    def backup_database(self, *, label: str = "automatic", retain: int = 14) -> Path:
        timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        safe_label = "".join(
            character for character in label if character.isalnum() or character == "-"
        )
        target = self.backup_directory / f"registry-{timestamp}-{safe_label or 'backup'}.sqlite3"
        temporary = target.with_suffix(".tmp")
        source = sqlite3.connect(self.database_path, timeout=10)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise sqlite3.DatabaseError("Registry backup failed its integrity check.")
        finally:
            destination.close()
            source.close()
        temporary.chmod(0o600)
        temporary.replace(target)
        backups = sorted(self.backup_directory.glob("registry-*.sqlite3"), reverse=True)
        for expired in backups[max(retain, 1) :]:
            expired.unlink(missing_ok=True)
        return target

    def backup_if_due(self, *, hours: int = 24) -> Path | None:
        latest = self.latest_backup()
        if latest is not None:
            age = utc_now() - datetime.fromtimestamp(latest.stat().st_mtime, UTC)
            if age < timedelta(hours=hours):
                return None
        return self.backup_database()

    def latest_backup(self) -> Path | None:
        backups = sorted(self.backup_directory.glob("registry-*.sqlite3"), reverse=True)
        return next(iter(backups), None)

    def prune(self) -> None:
        now = utc_iso()
        audit_before = utc_iso(utc_now() - timedelta(days=180))
        job_before = utc_iso(utc_now() - timedelta(days=90))
        with self.connection:
            self.connection.execute(
                "DELETE FROM enrollment_codes WHERE expires_at < ? OR used_at IS NOT NULL",
                (now,),
            )
            self.connection.execute(
                "DELETE FROM audit_events WHERE created_at < ?", (audit_before,)
            )
            self.connection.execute(
                """
                DELETE FROM jobs WHERE completed_at IS NOT NULL AND completed_at < ?
                """,
                (job_before,),
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def _attach_job_secret(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if job["action"] != "add_wifi_network":
            return job
        secret = self.connection.execute(
            "SELECT nonce, ciphertext FROM job_secrets WHERE job_id = ?", (job["id"],)
        ).fetchone()
        try:
            if secret is None:
                raise JobSecretError("Stored job secret is missing.")
            cipher = self._get_job_secret_cipher(create=False)
            password = cipher.decrypt(
                bytes(secret["nonce"]),
                bytes(secret["ciphertext"]),
                associated_data=self._job_secret_aad(
                    str(job["id"]), str(job["device_id"]), str(job["action"])
                ),
            )
        except JobSecretError:
            now = utc_iso()
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE jobs SET status = 'failed', progress = 100,
                        message = 'Stored Wi-Fi credential is unavailable', updated_at = ?,
                        completed_at = ?, lease_id = NULL, lease_expires_at = NULL,
                        lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (now, now, job["id"]),
                )
                self.connection.execute("DELETE FROM job_secrets WHERE job_id = ?", (job["id"],))
                self._audit(
                    "job_completed",
                    str(job["device_id"]),
                    {"job_id": job["id"], "status": "failed"},
                )
            return None
        job["credential"] = {"password": password}
        return job

    def _get_job_secret_cipher(self, *, create: bool) -> JobSecretCipher:
        if self._job_secret_cipher is None:
            self._job_secret_cipher = JobSecretCipher(self.job_secret_key_path, create=create)
        return self._job_secret_cipher

    @staticmethod
    def _job_secret_aad(job_id: str, device_id: str, action: str) -> bytes:
        return f"{job_id}\0{device_id}\0{action}".encode()

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

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _prune_mirror_snapshots(self, device_id: str, *, recent: int, daily: int) -> None:
        rows = self.connection.execute(
            """
            SELECT id, relative_path, received_at, size FROM mirror_snapshots
            WHERE device_id = ? ORDER BY received_at DESC
            """,
            (device_id,),
        ).fetchall()
        keep: set[str] = {row["id"] for row in rows[:recent]}
        daily_dates: set[str] = set()
        for row in rows[recent:]:
            date = str(row["received_at"])[:10]
            if len(daily_dates) < daily and date not in daily_dates:
                keep.add(row["id"])
                daily_dates.add(date)
        # Bound worst-case storage for a compromised or malfunctioning device.
        retained_size = 0
        bounded_keep: set[str] = set()
        for row in rows:
            if row["id"] in keep and (
                not bounded_keep or retained_size + int(row["size"]) <= 2 * 1024**3
            ):
                bounded_keep.add(row["id"])
                retained_size += int(row["size"])
        expired = [row for row in rows if row["id"] not in bounded_keep]
        if not expired:
            return
        with self.connection:
            self.connection.executemany(
                "DELETE FROM mirror_snapshots WHERE id = ?",
                [(row["id"],) for row in expired],
            )
        for row in expired:
            (self.data_directory / row["relative_path"]).unlink(missing_ok=True)
    def create_deployment(
        self,
        *,
        target: str,
        port: int,
        ssh_user: str,
        device_name: str,
        requested_hostname: str,
        registry_url: str,
        allow_insecure_http: bool,
        release_id: str,
    ) -> dict[str, Any]:
        release = self.connection.execute(
            "SELECT id FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if release is None:
            raise ValueError("Release not found.")
        active = self.connection.execute(
            """
            SELECT 1 FROM deployments
            WHERE target = ? AND port = ?
              AND status NOT IN ('succeeded', 'failed', 'cancelled', 'interrupted')
            LIMIT 1
            """,
            (target, port),
        ).fetchone()
        if active is not None:
            raise ValueError("A deployment for this target is already active.")
        deployment_id = secrets.token_hex(12)
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO deployments(
                    id, target, port, ssh_user, device_name, requested_hostname,
                    registry_url, allow_insecure_http, release_id, status, stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'starting', ?, ?)
                """,
                (
                    deployment_id,
                    target,
                    port,
                    ssh_user,
                    device_name,
                    requested_hostname,
                    registry_url,
                    int(allow_insecure_http),
                    release_id,
                    now,
                    now,
                ),
            )
            self._audit("deployment_created", deployment_id, {"target": target})
            self.connection.execute(
                """
                INSERT INTO deployment_events(
                    deployment_id, created_at, stage, level, message
                ) VALUES (?, ?, 'starting', 'info', 'Deployment queued')
                """,
                (deployment_id, now),
            )
        return self.get_deployment(deployment_id)

    def get_deployment(self, deployment_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT deployments.*, releases.version AS release_version
            FROM deployments
            LEFT JOIN releases ON releases.id = deployments.release_id
            WHERE deployments.id = ?
            """,
            (deployment_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_deployments(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT deployments.*, releases.version AS release_version
            FROM deployments
            LEFT JOIN releases ON releases.id = deployments.release_id
            ORDER BY deployments.created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_deployment(self, deployment_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "stage",
            "message",
            "host_key",
            "host_key_fingerprint",
            "device_id",
            "completed_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported deployment fields: {sorted(unknown)}")
        if not changes:
            result = self.get_deployment(deployment_id)
            if result is None:
                raise KeyError(deployment_id)
            return result
        assignments = ["updated_at = ?"]
        values: list[Any] = [utc_iso()]
        for key, value in changes.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(deployment_id)
        with self.connection:
            updated = self.connection.execute(
                f"UPDATE deployments SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        if not updated.rowcount:
            raise KeyError(deployment_id)
        result = self.get_deployment(deployment_id)
        assert result is not None
        return result

    def record_deployment_event(
        self,
        deployment_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        status: str | None = None,
    ) -> dict[str, Any]:
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO deployment_events(
                    deployment_id, created_at, stage, level, message
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (deployment_id, stage, level, message),
            )
            changes: dict[str, Any] = {"stage": stage, "message": message}
            if status is not None:
                changes["status"] = status
                if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                    changes["completed_at"] = now
            assignments = ["updated_at = ?"]
            values: list[Any] = [now]
            for key, value in changes.items():
                assignments.append(f"{key} = ?")
                values.append(value)
            values.append(deployment_id)
            self.connection.execute(
                f"UPDATE deployments SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return self.get_deployment(deployment_id) or {}

    def list_deployment_events(
        self, deployment_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, deployment_id, created_at, stage, level, message
            FROM deployment_events
            WHERE deployment_id = ? AND id > ?
            ORDER BY id
            """,
            (deployment_id, max(0, after)),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_trusted_ssh_host(self, target_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM trusted_ssh_hosts WHERE target_key = ?", (target_key,)
        ).fetchone()
        return dict(row) if row else None

    def trust_ssh_host(
        self,
        target_key: str,
        host_key: str,
        fingerprint: str,
        *,
        replace: bool = False,
    ) -> None:
        existing = self.get_trusted_ssh_host(target_key)
        if existing and existing["host_key"] != host_key and not replace:
            raise ValueError("SSH host key changed; explicit replacement is required.")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO trusted_ssh_hosts(target_key, host_key, fingerprint, trusted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    host_key = excluded.host_key,
                    fingerprint = excluded.fingerprint,
                    trusted_at = excluded.trusted_at
                """,
                (target_key, host_key, fingerprint, utc_iso()),
            )

    @staticmethod
    def deployment_target_key(target: str, port: int) -> str:
        return f"{target}:{port}"

    def _link_deployment_for_code(self, code_hash: str, device_id: str) -> None:
        self.connection.execute(
            """
            UPDATE deployments
            SET device_id = ?, updated_at = ?
            WHERE id = (
                SELECT deployment_id FROM enrollment_codes WHERE code_hash = ?
            )
            """,
            (device_id, utc_iso(), code_hash),
        )
