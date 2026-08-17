"""Fleet deployment records, SSH host trust, and enrollment-code linking."""

from __future__ import annotations

import secrets
from typing import Any

from takt.registry.store.common import _Base, utc_iso


class DeploymentsMixin(_Base):
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
        deployment = self.get_deployment(deployment_id)
        assert deployment is not None
        return deployment

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
