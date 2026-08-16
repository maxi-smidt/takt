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
from takt.fleet_actions import (
    DISRUPTIVE_ACTIONS,
    LEASED_JOBS_CAPABILITY,
    NO_REQUEUE_ON_LEASE_EXPIRY,
    WIFI_PROFILE_CAPABILITY,
    get_action,
)
from takt.migrations_runtime import upgrade_to_head
from takt.registry.accounts import AccountStore
from takt.registry.job_secrets import JobSecretCipher, JobSecretError

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCHEMA_VERSION = 13
JOB_TERMINAL_STATUSES = {"succeeded", "failed", "rolled_back", "cancelled"}
JOB_LEASE_SECONDS = 120


class RegistryStore:
    def __init__(self, data_directory: Path, *, allow_thread_handoff: bool = False) -> None:
        self.data_directory = data_directory
        self.database_path = data_directory / "registry.db"
        self.job_secret_key_path = data_directory / "job-secrets.key"
        self.release_directory = data_directory / "releases"
        self.mirror_directory = data_directory / "mirrors"
        self.backup_directory = data_directory / "backups"
        self.diagnostics_directory = data_directory / "diagnostics"
        self.release_directory.mkdir(parents=True, exist_ok=True)
        self.mirror_directory.mkdir(parents=True, exist_ok=True)
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        self.diagnostics_directory.mkdir(parents=True, exist_ok=True)
        database_existed = self.database_path.exists() and self.database_path.stat().st_size > 0
        probe = sqlite3.connect(self.database_path, timeout=10)
        try:
            previous_version = int(probe.execute("PRAGMA user_version").fetchone()[0])
        finally:
            probe.close()
        self.database_path.chmod(0o600)
        self._job_secret_cipher: JobSecretCipher | None = None
        self.bundled_release_status: dict[str, Any] = {"status": "absent"}
        if previous_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Registry database schema {previous_version} is newer than this "
                f"server supports ({SCHEMA_VERSION})."
            )
        if database_existed and previous_version < SCHEMA_VERSION:
            self.backup_database(label=f"pre-migration-v{previous_version}", retain=20)
        upgrade_to_head(MIGRATIONS_DIRECTORY, self.database_path)
        self.connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            check_same_thread=not allow_thread_handoff,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self._rebuild_disruptive_index()
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
        self.accounts = AccountStore(self.connection)
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
            if deployment_id:
                self.connection.execute(
                    "UPDATE enrollment_codes SET used_at = ? "
                    "WHERE deployment_id = ? AND used_at IS NULL AND expires_at > ?",
                    (utc_iso(now), deployment_id, utc_iso(now)),
                )

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
            previous = self.connection.execute(
                "SELECT status_json, recovery_raised_at FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            was_stuck = bool(
                previous
                and json.loads(previous["status_json"] or "{}")
                .get("update_recovery", {})
                .get("stuck")
            )
            is_stuck = bool((payload.get("update_recovery") or {}).get("stuck"))
            if is_stuck and not was_stuck:
                # A fresh recovery episode always needs fresh attention, even if the
                # previous one at this device was already acknowledged.
                recovery_raised_at = utc_iso()
            elif is_stuck:
                recovery_raised_at = previous["recovery_raised_at"] if previous else None
            else:
                recovery_raised_at = None
            self.connection.execute(
                """
                UPDATE devices
                SET name = ?, hostname = ?, last_seen_at = ?, app_version = ?,
                    agent_version = ?, status_json = ?, recovery_raised_at = ?
                WHERE id = ?
                """,
                (
                    str(payload.get("name") or "TAKT"),
                    str(payload.get("hostname") or "unknown"),
                    utc_iso(),
                    payload.get("app_version"),
                    payload.get("agent_version"),
                    json.dumps(payload, separators=(",", ":")),
                    recovery_raised_at,
                    device_id,
                ),
            )

    def acknowledge_update_recovery(self, device_id: str, *, actor: str) -> dict[str, Any]:
        with self.connection:
            row = self.connection.execute(
                "SELECT status_json FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Device does not exist.")
            recovery = json.loads(row["status_json"] or "{}").get("update_recovery") or {}
            if not recovery.get("stuck"):
                raise ValueError("This device has no active update recovery alert.")
            now = utc_iso()
            self.connection.execute(
                "UPDATE devices SET recovery_ack_at = ?, recovery_ack_by = ? WHERE id = ?",
                (now, actor, device_id),
            )
            self._audit(
                "update_recovery_acknowledged",
                device_id,
                {"phase": recovery.get("phase"), "error": recovery.get("error"), "actor": actor},
            )
        device = self.get_device(device_id)
        assert device is not None
        return device

    def list_devices(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM devices ORDER BY name COLLATE NOCASE, enrolled_at"
        ).fetchall()
        now = utc_now()
        devices: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["status"] = json.loads(item.pop("status_json"))
            item["health_checks"] = json.loads(item.pop("health_checks_json") or "{}")
            last_seen = item.get("last_seen_at")
            heartbeat_interval = float(item["status"].get("poll_seconds") or 10)
            online_window = min(max(heartbeat_interval * 3 + 15, 30), 180)
            item["online"] = bool(
                last_seen
                and not item.get("revoked_at")
                and now - datetime.fromisoformat(last_seen) < timedelta(seconds=online_window)
            )
            item.pop("token_hash", None)
            recovery = item["status"].get("update_recovery")
            raised_at = item.pop("recovery_raised_at", None)
            ack_at = item.pop("recovery_ack_at", None)
            ack_by = item.pop("recovery_ack_by", None)
            acknowledged = ack_at and (not raised_at or ack_at >= raised_at)
            if recovery and recovery.get("stuck") and acknowledged:
                item["status"] = {
                    **item["status"],
                    "update_recovery": {
                        **recovery,
                        "stuck": False,
                        "acknowledged_at": ack_at,
                        "acknowledged_by": ack_by,
                    },
                }
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
        self,
        *,
        version: str,
        filename: str,
        sha256: str,
        size: int,
        source: Path,
        release_source: str = "upload",
        commit_sha: str | None = None,
    ) -> dict[str, Any]:
        release_id = secrets.token_hex(12)
        target = self.release_path(release_id)
        source.replace(target)
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO releases(
                        id, version, filename, sha256, size, created_at, source, commit_sha
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release_id,
                        version,
                        filename,
                        sha256,
                        size,
                        utc_iso(),
                        release_source,
                        commit_sha,
                    ),
                )
                self._audit(
                    "release_uploaded",
                    details={"version": version, "sha256": sha256, "source": release_source},
                )
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

    def get_release_by_version(self, version: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM releases WHERE version = ?", (version,)
        ).fetchone()
        return dict(row) if row else None

    def mark_release_bundled(self, release_id: str, *, commit_sha: str | None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE releases SET source = 'bundled', "
                "commit_sha = COALESCE(commit_sha, ?) WHERE id = ?",
                (commit_sha, release_id),
            )

    def replace_bundled_release(
        self,
        release_id: str,
        *,
        filename: str,
        sha256: str,
        size: int,
        source: Path,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        """Overwrite an existing release's bytes and metadata in place.

        Used to refresh a previously bundled release whose packaged bytes
        changed without a version bump, or to repair a release whose
        persisted artifact went missing or was damaged independently of its
        database row.
        """
        previous = self.get_release(release_id)
        target = self.release_path(release_id)
        source.replace(target)
        with self.connection:
            self.connection.execute(
                "UPDATE releases SET filename = ?, sha256 = ?, size = ?, "
                "source = 'bundled', commit_sha = ? WHERE id = ?",
                (filename, sha256, size, commit_sha, release_id),
            )
            self._audit(
                "release_bundled_refreshed",
                details={
                    "version": previous["version"] if previous else None,
                    "previous_sha256": previous["sha256"] if previous else None,
                    "sha256": sha256,
                },
            )
        release = self.get_release(release_id)
        assert release is not None
        return release

    def release_path(self, release_id: str) -> Path:
        return self.release_directory / f"{release_id}.tar.gz"

    def _authorize_action(self, device: dict[str, Any], action: str) -> None:
        fleet_action = get_action(action)
        assert fleet_action is not None
        status = device.get("status") or {}
        protocol_version = status.get("protocol_version")
        if protocol_version is not None and int(protocol_version) < fleet_action.min_protocol:
            raise ValueError(
                f"This Pi agent's Fleet protocol is too old for '{action}'; "
                "update the Fleet agent once via SSH to enable it."
            )
        if fleet_action.capability == LEASED_JOBS_CAPABILITY:
            # Every agent that can heartbeat at all supports the baseline
            # leased-job actions; this is not a negotiated/optional feature.
            return
        capabilities = status.get("capabilities") or []
        if fleet_action.capability not in capabilities:
            raise ValueError(
                f"This Pi agent does not support '{action}' yet; "
                "update the Fleet agent once via SSH to enable it."
            )

    def create_job(
        self,
        device_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        override: bool = False,
        requested_by_user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        # Operator consent is controlled by the explicit argument, never by caller data.
        payload.pop("override", None)
        fleet_action = get_action(action)
        if fleet_action is None:
            raise ValueError("Unsupported action.")
        if override and not fleet_action.overridable:
            raise ValueError(f"'{action}' cannot be overridden.")
        if override:
            payload["override"] = True
        device = self.get_device(device_id)
        if device is None:
            raise LookupError("Device does not exist.")
        if device.get("revoked_at"):
            raise ValueError("Device access has been revoked.")
        if not device.get("online"):
            raise ValueError("Device must be online to queue a job.")
        self._authorize_action(device, action)
        placeholders = ",".join("?" for _ in DISRUPTIVE_ACTIONS)
        active = self.connection.execute(
            f"""
            SELECT id, action FROM jobs WHERE device_id = ?
                AND status IN ('queued', 'claimed', 'running')
                AND action IN ({placeholders})
            """,
            (device_id, *DISRUPTIVE_ACTIONS),
        ).fetchone()
        if active is not None and action in DISRUPTIVE_ACTIONS:
            existing = self.get_job(str(active["id"]))
            if (
                action == "install_release"
                and active["action"] == action
                and existing is not None
                and existing["payload"].get("release_id") == payload.get("release_id")
            ):
                existing["reused"] = True
                return existing
            raise ValueError("Another disruptive operation is already queued for this device.")
        release: dict[str, Any] | None = None
        if action == "install_release":
            release_id = str(payload.get("release_id", ""))
            release = self.get_release(release_id)
            if release is None:
                raise ValueError("Release does not exist.")
        job_id = secrets.token_hex(12)
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobs(
                    id, device_id, action, payload_json, status, stage, requested_by_user_id,
                    current_version, target_version, bytes_total, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    device_id,
                    action,
                    json.dumps(payload),
                    requested_by_user_id,
                    device.get("app_version"),
                    release.get("version") if release else None,
                    release.get("size") if release else None,
                    now,
                    now,
                ),
            )
            self._record_job_event(job_id, "queued", "queued", "Job queued")
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
            self._record_job_event(job_id, "queued", "queued", "Job queued")
            self._audit("job_created", device_id, {"job_id": job_id, "action": action})
        job = self.get_job(job_id)
        assert job is not None
        return job

    def claim_next_job(self, device_id: str, agent_session_id: str = "") -> dict[str, Any] | None:
        now_value = utc_now()
        now = utc_iso(now_value)
        with self.connection:
            # Power actions kill the agent before it can renew its lease; requeuing
            # them like any other expired lease would make the device reboot or
            # power off again as soon as it reconnects. Fail them instead — the
            # operator can see what happened and re-issue deliberately.
            no_requeue_placeholders = ",".join("?" for _ in NO_REQUEUE_ON_LEASE_EXPIRY)
            expired_no_requeue = self.connection.execute(
                f"""
                SELECT id FROM jobs WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                    AND action IN ({no_requeue_placeholders})
                """,
                (device_id, now, *NO_REQUEUE_ON_LEASE_EXPIRY),
            ).fetchall()
            for expired in expired_no_requeue:
                job_id = str(expired["id"])
                message = (
                    "Device did not confirm before its lease expired; the action may or "
                    "may not have applied. Check the device and retry if needed."
                )
                self.connection.execute(
                    """
                    UPDATE jobs SET status = 'failed', stage = 'failed', progress = 100,
                        message = ?, updated_at = ?, completed_at = ?,
                        lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (message, now, now, job_id),
                )
                self.connection.execute("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
                self._record_job_event(job_id, "failed", "failed", message)
                self._audit("job_lease_expired_unconfirmed", device_id, {"job_id": job_id})
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
        *,
        result: dict[str, Any] | None = None,
        stage: str | None = None,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
    ) -> dict[str, Any]:
        allowed = {"queued", "claimed", "running", *JOB_TERMINAL_STATUSES}
        if status not in allowed:
            raise ValueError("Invalid job status.")
        current = self.connection.execute(
            """
            SELECT status, lease_id, stage, action, target_version, bytes_downloaded, bytes_total
            FROM jobs WHERE id = ? AND device_id = ?
            """,
            (job_id, device_id),
        ).fetchone()
        if current is None:
            raise LookupError("Job does not exist.")
        if current["status"] in JOB_TERMINAL_STATUSES:
            if status == current["status"]:
                job = self.get_job(job_id)
                assert job is not None
                return job
            raise ValueError("Completed jobs cannot change state.")
        if stage is None:
            stage = (
                status
                if status in {"succeeded", "rolled_back", "cancelled"}
                else "intervention_required"
                if status == "failed" and current["action"] == "install_release"
                else str(current["stage"] or "queued")
            )
        action_stages = getattr(get_action(str(current["action"])), "stages", ())
        if action_stages and stage not in action_stages:
            raise ValueError("Invalid job stage.")
        transitions = {
            "queued": {"claimed", "cancelled"},
            "claimed": {"queued", "running", *JOB_TERMINAL_STATUSES},
            "running": {"queued", "running", *JOB_TERMINAL_STATUSES},
        }
        if status != current["status"] and status not in transitions[current["status"]]:
            raise ValueError(f"Invalid job transition: {current['status']} -> {status}.")
        if current["status"] in {"claimed", "running"}:
            if not lease_id or not current["lease_id"]:
                raise ValueError("A job lease is required for this operation.")
            if not secrets.compare_digest(lease_id, current["lease_id"]):
                raise ValueError("Job lease no longer belongs to this agent operation.")
        if status == "succeeded" and current["action"] == "install_release":
            if not self._install_success_is_confirmed(
                device_id, str(current["target_version"] or "")
            ):
                raise ValueError(
                    "Expected version and healthy agent status have not been confirmed."
                )
        completed_at = utc_iso() if status in JOB_TERMINAL_STATUSES else None
        lease_expires_at = (
            utc_iso(utc_now() + timedelta(seconds=JOB_LEASE_SECONDS))
            if status in {"claimed", "running"}
            else None
        )
        downloaded = (
            current["bytes_downloaded"]
            if bytes_downloaded is None
            else min(max(int(bytes_downloaded), 0), 250 * 1024 * 1024)
        )
        total = (
            current["bytes_total"]
            if bytes_total is None
            else min(max(int(bytes_total), 0), 250 * 1024 * 1024)
        )
        result_json = json.dumps(result, separators=(",", ":")) if result is not None else None
        if downloaded is not None and total is not None and downloaded > total:
            raise ValueError("Downloaded bytes exceed release size.")
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, progress = ?, message = ?, updated_at = ?,
                    bytes_downloaded = ?, bytes_total = ?,
                    completed_at = COALESCE(?, completed_at),
                    result_json = COALESCE(?, result_json),
                    claimed_at = CASE WHEN ? = 'queued' THEN NULL ELSE claimed_at END,
                    lease_id = CASE WHEN ? IN (
                        'queued', 'succeeded', 'failed', 'rolled_back', 'cancelled'
                    ) THEN NULL ELSE lease_id END,
                    lease_owner_session = CASE
                        WHEN ? IN ('queued', 'succeeded', 'failed', 'rolled_back', 'cancelled')
                        THEN NULL ELSE lease_owner_session END,
                    lease_expires_at = ?
                WHERE id = ? AND device_id = ?
                """,
                (
                    status,
                    stage,
                    min(max(progress, 0), 100),
                    message[:2000],
                    utc_iso(),
                    downloaded,
                    total,
                    completed_at,
                    result_json,
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
            if status != current["status"] or stage != current["stage"]:
                self._record_job_event(job_id, status, stage, message[:2000])
            if completed_at:
                self._audit("job_completed", device_id, {"job_id": job_id, "status": status})
                self.connection.execute("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
            self.connection.execute(
                "UPDATE devices SET last_seen_at = ? WHERE id = ?", (utc_iso(), device_id)
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise LookupError("Job does not exist.")
        if job["status"] in JOB_TERMINAL_STATUSES:
            return job
        if job["stage"] in {"activating", "restarting", "health_checking"}:
            raise ValueError("This install is past its cancellation checkpoint.")
        now = utc_iso()
        with self.connection:
            if job["status"] in {"queued", "claimed"}:
                self.connection.execute(
                    """
                    UPDATE jobs SET status = 'cancelled', stage = 'cancelled', progress = 100,
                        message = 'Cancelled before activation', updated_at = ?, completed_at = ?,
                        lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (now, now, job_id),
                )
                self.connection.execute("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
                self._record_job_event(
                    job_id, "cancelled", "cancelled", "Cancelled before activation"
                )
            else:
                message = "Cancellation requested; stopping at the next safe checkpoint"
                self.connection.execute(
                    "UPDATE jobs SET cancel_requested = 1, message = ?, updated_at = ? "
                    "WHERE id = ?",
                    (message, now, job_id),
                )
                self._record_job_event(job_id, job["status"], job["stage"], message)
            self._audit("job_cancel_requested", job["device_id"], {"job_id": job_id})
        cancelled = self.get_job(job_id)
        assert cancelled is not None
        return cancelled

    def retry_job(self, job_id: str, *, override: bool = False) -> dict[str, Any]:
        previous = self.get_job(job_id)
        if previous is None:
            raise LookupError("Job does not exist.")
        if previous["status"] not in {"failed", "rolled_back", "cancelled"}:
            raise ValueError("Only an unsuccessful completed job can be retried.")
        if previous["action"] == "add_wifi_network":
            raise ValueError(
                "Wi-Fi jobs cannot be retried because their credential is not retained."
            )
        replacement = self.create_job(
            previous["device_id"], previous["action"], previous["payload"], override=override
        )
        if replacement.get("reused"):
            return replacement
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET retry_of = ? WHERE id = ?", (job_id, replacement["id"])
            )
            self._record_job_event(replacement["id"], "queued", "queued", f"Retry of {job_id}")
            self._audit(
                "job_retried",
                previous["device_id"],
                {"job_id": replacement["id"], "retry_of": job_id},
            )
        retried = self.get_job(replacement["id"])
        assert retried is not None
        return retried

    def list_job_events(self, job_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        ]

    def _install_success_is_confirmed(self, device_id: str, target_version: str) -> bool:
        device = self.get_device(device_id)
        status = (device or {}).get("status", {})
        health = status.get("health") or {}
        return bool(
            target_version
            and device
            and device.get("online")
            and status.get("app_version") == target_version
            and health.get("ok")
            and health.get("state") == "ready"
            and health.get("version") == target_version
        )

    def _record_job_event(self, job_id: str, status: str, stage: str, message: str) -> None:
        self.connection.execute(
            """
            INSERT INTO job_events(job_id, created_at, status, stage, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, utc_iso(), status, stage, message[:2000]),
        )

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

    def record_diagnostics(
        self, device_id: str, job_id: str, source: Path, sha256: str, size: int
    ) -> dict[str, Any]:
        created_at = utc_now()
        bundle_id = secrets.token_hex(12)
        relative_path = (
            Path("diagnostics")
            / device_id
            / f"{created_at.strftime('%Y%m%dT%H%M%SZ')}-{sha256[:12]}.tar.gz"
        )
        target = self.data_directory / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        target.chmod(0o600)
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO diagnostics(
                        id, device_id, job_id, created_at, sha256, size, relative_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bundle_id,
                        device_id,
                        job_id,
                        utc_iso(created_at),
                        sha256,
                        size,
                        str(relative_path),
                    ),
                )
                self._audit(
                    "diagnostics_received", device_id, {"job_id": job_id, "size": size}
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self._prune_diagnostics(device_id)
        bundle = self.get_diagnostics(bundle_id)
        assert bundle is not None
        return bundle

    def get_diagnostics(self, bundle_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM diagnostics WHERE id = ?", (bundle_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_diagnostics(self, device_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT id, device_id, job_id, created_at, sha256, size FROM diagnostics "
                "WHERE device_id = ? ORDER BY created_at DESC",
                (device_id,),
            )
        ]

    def diagnostics_for_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM diagnostics WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def diagnostics_path(self, bundle_id: str) -> Path | None:
        bundle = self.get_diagnostics(bundle_id)
        return self.data_directory / bundle["relative_path"] if bundle else None

    def record_health_checks(self, device_id: str, report: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE devices SET health_checks_json = ? WHERE id = ?",
                (json.dumps(report, separators=(",", ":")), device_id),
            )

    def _prune_diagnostics(self, device_id: str, *, keep: int = 5) -> None:
        rows = self.connection.execute(
            "SELECT id, relative_path FROM diagnostics WHERE device_id = ? "
            "ORDER BY created_at DESC",
            (device_id,),
        ).fetchall()
        expired = rows[keep:]
        if not expired:
            return
        with self.connection:
            self.connection.executemany(
                "DELETE FROM diagnostics WHERE id = ?", [(row["id"],) for row in expired]
            )
        for row in expired:
            (self.data_directory / row["relative_path"]).unlink(missing_ok=True)

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
        bundled_release_ok = self.bundled_release_status.get("reason") != "collision"
        return {
            "ok": database_ok and disk_free > 1024 * 1024 and bundled_release_ok,
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
            "bundled_release": self.bundled_release_status,
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
        result_json = item.pop("result_json", None)
        item["result"] = json.loads(result_json) if result_json else None
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

    def record_hostname_change(
        self,
        deployment_id: str,
        *,
        old_hostname: str,
        new_hostname: str,
        event: str = "hostname_changed",
    ) -> None:
        deployment = self.get_deployment(deployment_id)
        with self.connection:
            self._audit(
                event,
                deployment.get("device_id") if deployment else None,
                {
                    "deployment_id": deployment_id,
                    "old_hostname": old_hostname,
                    "new_hostname": new_hostname,
                },
            )

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

    def _rebuild_disruptive_index(self) -> None:
        # Derived from the fleet action table rather than hardcoded, so adding a
        # new disruptive action only needs a SCHEMA_VERSION bump, not a second
        # place to remember to update.
        actions = ", ".join(f"'{name}'" for name in sorted(DISRUPTIVE_ACTIONS))
        index_sql = (
            "CREATE UNIQUE INDEX idx_jobs_one_active_disruptive_operation ON jobs(device_id) "
            f"WHERE action IN ({actions}) AND status IN ('queued', 'claimed', 'running')"
        )
        existing = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_jobs_one_active_disruptive_operation'"
        ).fetchone()
        if existing is not None and existing["sql"] == index_sql:
            return
        self.connection.execute("DROP INDEX IF EXISTS idx_jobs_one_active_disruptive_operation")
        self.connection.execute(index_sql)

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
        hostname_change_confirmed: bool = False,
        registry_url: str,
        allow_insecure_http: bool,
        release_id: str,
    ) -> dict[str, Any]:
        release = self.connection.execute(
            "SELECT id FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if release is None:
            raise ValueError("Release not found.")
        self.ensure_deployment_target_available(target, port)
        deployment_id = secrets.token_hex(12)
        now = utc_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO deployments(
                    id, target, port, ssh_user, device_name, requested_hostname,
                    hostname_change_confirmed, registry_url, allow_insecure_http,
                    release_id, status, stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'starting', ?, ?)
                """,
                (
                    deployment_id,
                    target,
                    port,
                    ssh_user,
                    device_name,
                    requested_hostname,
                    int(hostname_change_confirmed),
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

    def ensure_deployment_target_available(self, target: str, port: int) -> None:
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
        with self.connection:
            self._set_deployment_fields(deployment_id, changes)
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
                (deployment_id, now, stage, level, message),
            )
            changes: dict[str, Any] = {"stage": stage, "message": message}
            if status is not None:
                changes["status"] = status
                if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                    changes["completed_at"] = now
            self._set_deployment_fields(deployment_id, changes, now)
        return self.get_deployment(deployment_id) or {}

    def _set_deployment_fields(
        self, deployment_id: str, changes: dict[str, Any], now: str | None = None
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [now or utc_iso()]
        for key, value in changes.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(deployment_id)
        updated = self.connection.execute(
            f"UPDATE deployments SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        if not updated.rowcount:
            raise KeyError(deployment_id)
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
        row = self.connection.execute(
            "SELECT deployment_id FROM enrollment_codes WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        if row is None or row["deployment_id"] is None:
            return
        linked = self.connection.execute(
            "UPDATE deployments SET device_id = ?, updated_at = ? WHERE id = ?",
            (device_id, utc_iso(), row["deployment_id"]),
        )
        if linked.rowcount != 1:
            raise RuntimeError("Enrollment code refers to a missing deployment.")
