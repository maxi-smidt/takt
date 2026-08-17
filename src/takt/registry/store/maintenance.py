"""Health reporting, database backups, retention pruning, and the audit trail."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from takt import __version__
from takt.fleet_actions import DISRUPTIVE_ACTIONS
from takt.registry.store.common import SCHEMA_VERSION, _Base, utc_iso, utc_now


class MaintenanceMixin(_Base):
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
