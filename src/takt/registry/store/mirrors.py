"""Run-database mirror snapshots and diagnostics bundles."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from takt.registry.store.common import _Base, utc_iso, utc_now


class MirrorDiagnosticsMixin(_Base):
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
