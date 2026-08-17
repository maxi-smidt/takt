"""Job lifecycle: creation, claiming, progress updates, and Wi-Fi credentials."""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from takt.fleet_actions import (
    DISRUPTIVE_ACTIONS,
    LEASED_JOBS_CAPABILITY,
    NO_REQUEUE_ON_LEASE_EXPIRY,
    WIFI_PROFILE_CAPABILITY,
    get_action,
)
from takt.registry.job_secrets import JobSecretCipher, JobSecretError
from takt.registry.store.common import (
    JOB_LEASE_SECONDS,
    JOB_TERMINAL_STATUSES,
    QUEUED_JOB_STALE_SECONDS,
    _Base,
    utc_iso,
    utc_now,
)


class JobsMixin(_Base):
    _job_secret_cipher: JobSecretCipher | None

    def _authorize_action(self, device: dict[str, Any], action: str) -> None:
        fleet_action = get_action(action)
        assert fleet_action is not None
        status = device.get("status") or {}
        protocol_version = status.get("protocol_version")
        if protocol_version is None and status:
            # The device has reported status before but still doesn't send a
            # protocol version, so its Fleet agent predates the heartbeat/
            # job-claim protocol entirely (e.g. an old 0.4.x agent) and will
            # never claim a job -- treat it as protocol 0 rather than silently
            # skipping the check.
            protocol_version = 0
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
        with self._read() as conn:
            active = conn.exec_driver_sql(
                f"""
                SELECT id, action FROM jobs WHERE device_id = ?
                    AND status IN ('queued', 'claimed', 'running')
                    AND action IN ({placeholders})
                """,
                (device_id, *DISRUPTIVE_ACTIONS),
            ).mappings().fetchone()
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
        with self._transaction() as conn:
            conn.exec_driver_sql(
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
        with self._transaction() as conn:
            conn.exec_driver_sql(
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
            conn.exec_driver_sql(
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
        with self._transaction() as conn:
            # Power actions kill the agent before it can renew its lease; requeuing
            # them like any other expired lease would make the device reboot or
            # power off again as soon as it reconnects. Fail them instead — the
            # operator can see what happened and re-issue deliberately.
            no_requeue_placeholders = ",".join("?" for _ in NO_REQUEUE_ON_LEASE_EXPIRY)
            expired_no_requeue = conn.exec_driver_sql(
                f"""
                SELECT id FROM jobs WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                    AND action IN ({no_requeue_placeholders})
                """,
                (device_id, now, *NO_REQUEUE_ON_LEASE_EXPIRY),
            ).mappings().all()
            for expired in expired_no_requeue:
                job_id = str(expired["id"])
                message = (
                    "Device did not confirm before its lease expired; the action may or "
                    "may not have applied. Check the device and retry if needed."
                )
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET status = 'failed', stage = 'failed', progress = 100,
                        message = ?, updated_at = ?, completed_at = ?,
                        lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (message, now, now, job_id),
                )
                conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
                self._record_job_event(job_id, "failed", "failed", message)
                self._audit("job_lease_expired_unconfirmed", device_id, {"job_id": job_id})
            conn.exec_driver_sql(
                """
                UPDATE jobs SET status = 'queued', claimed_at = NULL, lease_id = NULL,
                    lease_expires_at = NULL, lease_owner_session = NULL,
                    message = 'Job lease expired; retrying safely', updated_at = ?
                WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (now, device_id, now),
            )
        with self._read() as conn:
            active = conn.exec_driver_sql(
                """
                SELECT id FROM jobs
                WHERE device_id = ? AND status IN ('claimed', 'running')
                    AND lease_expires_at >= ?
                ORDER BY created_at LIMIT 1
                """,
                (device_id, now),
            ).mappings().fetchone()
        if active is not None:
            active_job = self.get_job(active["id"])
            if active_job and active_job.get("lease_owner_session") == agent_session_id:
                return self._attach_job_secret(active_job)
            return None
        with self._read() as conn:
            row = conn.exec_driver_sql(
                """
                SELECT id FROM jobs
                WHERE device_id = ? AND status = 'queued'
                ORDER BY created_at LIMIT 1
                """,
                (device_id,),
            ).mappings().fetchone()
        if row is None:
            return None
        lease_id = secrets.token_urlsafe(18)
        lease_expires_at = utc_iso(now_value + timedelta(seconds=JOB_LEASE_SECONDS))
        with self._transaction() as conn:
            conn.exec_driver_sql(
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

    def expire_stale_queued_jobs(self) -> None:
        """Fail queued jobs no agent has claimed in a reasonable time.

        A job stays 'queued' forever if the target device's agent never polls
        (offline) or predates the heartbeat/job-claim protocol (too old to
        ever call claim_next_job) -- neither case is caught by the lease
        expiry above, which only applies once a job has been claimed.
        """
        stale_before = utc_iso(utc_now() - timedelta(seconds=QUEUED_JOB_STALE_SECONDS))
        message = (
            "No agent claimed this job in time; the device may be offline or its "
            "Fleet agent too old to support this action. Update the Fleet agent "
            "once via SSH, then retry."
        )
        with self._transaction() as conn:
            stale = conn.exec_driver_sql(
                "SELECT id, action, device_id FROM jobs WHERE status = 'queued' AND created_at < ?",
                (stale_before,),
            ).mappings().all()
            for job in stale:
                job_id = str(job["id"])
                stage = (
                    "intervention_required" if job["action"] == "install_release" else "failed"
                )
                now = utc_iso()
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET status = 'failed', stage = ?, progress = 100,
                        message = ?, updated_at = ?, completed_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (stage, message, now, now, job_id),
                )
                conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
                self._record_job_event(job_id, "failed", stage, message)
                self._audit("job_queue_expired", str(job["device_id"]), {"job_id": job_id})

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
        with self._read() as conn:
            current = conn.exec_driver_sql(
                """
                SELECT status, lease_id, stage, action, target_version,
                    bytes_downloaded, bytes_total
                FROM jobs WHERE id = ? AND device_id = ?
                """,
                (job_id, device_id),
            ).mappings().fetchone()
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
        if (
            status == "succeeded"
            and current["action"] == "install_release"
            and not self._install_success_is_confirmed(
                device_id, str(current["target_version"] or "")
            )
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
        with self._transaction() as conn:
            cursor = conn.exec_driver_sql(
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
                conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
            conn.exec_driver_sql(
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
        with self._transaction() as conn:
            if job["status"] in {"queued", "claimed"}:
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET status = 'cancelled', stage = 'cancelled', progress = 100,
                        message = 'Cancelled before activation', updated_at = ?, completed_at = ?,
                        lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (now, now, job_id),
                )
                conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
                self._record_job_event(
                    job_id, "cancelled", "cancelled", "Cancelled before activation"
                )
            else:
                message = "Cancellation requested; stopping at the next safe checkpoint"
                conn.exec_driver_sql(
                    "UPDATE jobs SET cancel_requested = 1, message = ?, updated_at = ? "
                    "WHERE id = ?",
                    (message, now, job_id),
                )
                self._record_job_event(job_id, job["status"], job["stage"], message)
            self._audit("job_cancel_requested", job["device_id"], {"job_id": job_id})
        cancelled = self.get_job(job_id)
        assert cancelled is not None
        return cancelled

    def force_clear_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        """Force a wedged, non-terminal job to 'failed' so the device's queue unblocks.

        Unlike cancel_job, this works from any stage (including
        activating/restarting/health_checking) and doesn't wait for the agent to
        observe a checkpoint; it's an escape hatch for when the agent itself is
        unresponsive, so the true outcome on the device is left unknown.
        """
        job = self.get_job(job_id)
        if job is None:
            raise LookupError("Job does not exist.")
        if job["status"] in JOB_TERMINAL_STATUSES:
            return job
        now = utc_iso()
        message = f"Force-cleared by {actor}; outcome on the device is unknown"
        # Mirrors update_job's stage mapping for a 'failed' status so the stage stays
        # one the action actually declares (install_release has no 'failed' stage).
        stage = "intervention_required" if job["action"] == "install_release" else "failed"
        with self._transaction() as conn:
            conn.exec_driver_sql(
                """
                UPDATE jobs SET status = 'failed', stage = ?, message = ?,
                    cancel_requested = 0, updated_at = ?, completed_at = ?,
                    lease_id = NULL, lease_expires_at = NULL, lease_owner_session = NULL
                WHERE id = ?
                """,
                (stage, message, now, now, job_id),
            )
            conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job_id,))
            self._record_job_event(job_id, "failed", stage, message)
            self._audit(
                "job_force_cleared", job["device_id"], {"job_id": job_id, "actor": actor}
            )
        cleared = self.get_job(job_id)
        assert cleared is not None
        return cleared

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
        with self._transaction() as conn:
            conn.exec_driver_sql(
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
        with self._read() as conn:
            return [
                dict(row)
                for row in conn.exec_driver_sql(
                    "SELECT * FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
                ).mappings().all()
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
        with self._transaction() as conn:
            conn.exec_driver_sql(
                """
                INSERT INTO job_events(job_id, created_at, status, stage, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, utc_iso(), status, stage, message[:2000]),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.exec_driver_sql(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).mappings().fetchone()
        return self._job(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        self.expire_stale_queued_jobs()
        with self._read() as conn:
            rows = conn.exec_driver_sql(
                """
                SELECT jobs.*, devices.name AS device_name
                FROM jobs JOIN devices ON devices.id = jobs.device_id
                ORDER BY jobs.created_at DESC LIMIT ?
                """,
                (limit,),
            ).mappings().all()
        return [self._job(row) for row in rows]

    def job_for_device(self, job_id: str, device_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        return job if job and job["device_id"] == device_id else None

    @staticmethod
    def _job(row: Mapping[Any, Any]) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result_json = item.pop("result_json", None)
        item["result"] = json.loads(result_json) if result_json else None
        return item

    def _attach_job_secret(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if job["action"] != "add_wifi_network":
            return job
        with self._read() as conn:
            secret = conn.exec_driver_sql(
                "SELECT nonce, ciphertext FROM job_secrets WHERE job_id = ?", (job["id"],)
            ).mappings().fetchone()
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
            with self._transaction() as conn:
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET status = 'failed', progress = 100,
                        message = 'Stored Wi-Fi credential is unavailable', updated_at = ?,
                        completed_at = ?, lease_id = NULL, lease_expires_at = NULL,
                        lease_owner_session = NULL
                    WHERE id = ?
                    """,
                    (now, now, job["id"]),
                )
                conn.exec_driver_sql("DELETE FROM job_secrets WHERE job_id = ?", (job["id"],))
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
