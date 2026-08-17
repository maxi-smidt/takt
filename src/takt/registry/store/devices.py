"""Device enrollment, authentication, and heartbeat handling."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

from takt.registry.store.common import _Base, device_is_online, hash_secret, utc_iso, utc_now


class DevicesMixin(_Base):
    def create_enrollment_code(
        self, label: str = "", minutes: int = 60, deployment_id: str | None = None
    ) -> str:
        code = f"TAKT-{secrets.token_urlsafe(18)}"
        now = utc_now()
        with self._transaction() as conn:
            if deployment_id:
                conn.exec_driver_sql(
                    "UPDATE enrollment_codes SET used_at = ? "
                    "WHERE deployment_id = ? AND used_at IS NULL AND expires_at > ?",
                    (utc_iso(now), deployment_id, utc_iso(now)),
                )

            conn.exec_driver_sql(
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
        with self._read() as conn:
            existing = conn.exec_driver_sql(
                "SELECT token_hash, revoked_at FROM devices WHERE id = ?", (device_id,)
            ).mappings().fetchone()
        if existing is not None:
            if existing["revoked_at"]:
                raise ValueError("This device has been revoked by the registry administrator.")
            if token and secrets.compare_digest(existing["token_hash"], hash_secret(token)):
                with self._transaction() as conn:
                    consumed = conn.exec_driver_sql(
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
        with self._transaction() as conn:
            consumed_row = conn.exec_driver_sql(
                """
                UPDATE enrollment_codes SET used_at = ?
                WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?
                RETURNING code_hash
                """,
                (utc_iso(now), code_hash, utc_iso(now)),
            ).fetchone()
            if consumed_row is None:
                raise ValueError("Enrollment code is invalid, expired, or already used.")
            conn.exec_driver_sql(
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
        with self._read() as conn:
            row = conn.exec_driver_sql(
                "SELECT token_hash FROM devices WHERE id = ? AND revoked_at IS NULL", (device_id,)
            ).mappings().fetchone()
        return row is not None and secrets.compare_digest(row["token_hash"], hash_secret(token))

    def update_heartbeat(self, device_id: str, payload: dict[str, Any]) -> None:
        with self._transaction() as conn:
            previous = conn.exec_driver_sql(
                "SELECT status_json, recovery_raised_at FROM devices WHERE id = ?",
                (device_id,),
            ).mappings().fetchone()
            was_stuck = bool(
                previous
                and json.loads(previous["status_json"] or "{}")
                .get("update_recovery", {})
                .get("stuck")
            )
            is_stuck = bool((payload.get("update_recovery") or {}).get("stuck"))
            recovery_raised_at: str | None
            if is_stuck and not was_stuck:
                # A fresh recovery episode always needs fresh attention, even if the
                # previous one at this device was already acknowledged.
                recovery_raised_at = utc_iso()
            elif is_stuck:
                recovery_raised_at = previous["recovery_raised_at"] if previous else None
            else:
                recovery_raised_at = None
            conn.exec_driver_sql(
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
        with self._transaction() as conn:
            row = conn.exec_driver_sql(
                "SELECT status_json FROM devices WHERE id = ?", (device_id,)
            ).mappings().fetchone()
            if row is None:
                raise LookupError("Device does not exist.")
            recovery = json.loads(row["status_json"] or "{}").get("update_recovery") or {}
            if not recovery.get("stuck"):
                raise ValueError("This device has no active update recovery alert.")
            now = utc_iso()
            conn.exec_driver_sql(
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
        with self._read() as conn:
            rows = conn.exec_driver_sql(
                "SELECT * FROM devices ORDER BY name COLLATE NOCASE, enrolled_at"
            ).mappings().all()
        devices: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["status"] = json.loads(item.pop("status_json"))
            item["health_checks"] = json.loads(item.pop("health_checks_json") or "{}")
            item["online"] = device_is_online(
                last_seen_at=item.get("last_seen_at"),
                revoked_at=item.get("revoked_at"),
                poll_seconds=item["status"].get("poll_seconds"),
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
        with self._transaction() as conn:
            cursor = conn.exec_driver_sql(
                "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, device_id),
            )
            if not cursor.rowcount:
                device = self.get_device(device_id)
                if device is None:
                    raise LookupError("Device does not exist.")
                return device
            conn.exec_driver_sql(
                """
                DELETE FROM job_secrets WHERE job_id IN (
                    SELECT id FROM jobs
                    WHERE device_id = ? AND status IN ('queued', 'claimed', 'running')
                )
                """,
                (device_id,),
            )
            conn.exec_driver_sql(
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
