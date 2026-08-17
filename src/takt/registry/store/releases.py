"""Release upload, lookup, and bundled-release replacement."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from takt.registry.store.common import _Base, utc_iso


class ReleasesMixin(_Base):
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
            with self._transaction() as conn:
                conn.exec_driver_sql(
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
        with self._read() as conn:
            rows = [
                dict(row)
                for row in conn.exec_driver_sql(
                    "SELECT * FROM releases ORDER BY created_at DESC"
                ).mappings().all()
            ]
        for row in rows:
            row["installed"] = self.release_path(row["id"]).is_file()
        return rows

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.exec_driver_sql(
                "SELECT * FROM releases WHERE id = ?", (release_id,)
            ).mappings().fetchone()
        if row is None:
            return None
        release = dict(row)
        release["installed"] = self.release_path(release_id).is_file()
        return release

    def uninstall_release(self, release_id: str) -> dict[str, Any]:
        """Delete the locally cached archive for a release, keeping its row.

        The release stays visible with its full history; only the on-disk
        `.tar.gz` blob is removed. A later job that needs the bytes will
        transparently repair them from the bundled image artifact (see
        `bundled_release.ensure_release_cached`) if it still matches, or
        fail clearly if it doesn't.
        """
        release = self.get_release(release_id)
        if release is None:
            raise LookupError("Release does not exist.")
        path = self.release_path(release_id)
        if path.is_file():
            path.unlink()
            with self._transaction():
                self._audit(
                    "release_uninstalled",
                    details={"version": release["version"], "sha256": release["sha256"]},
                )
        updated = self.get_release(release_id)
        assert updated is not None
        return updated

    def get_release_by_version(self, version: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.exec_driver_sql(
                "SELECT * FROM releases WHERE version = ?", (version,)
            ).mappings().fetchone()
        return dict(row) if row else None

    def mark_release_bundled(self, release_id: str, *, commit_sha: str | None) -> None:
        with self._transaction() as conn:
            conn.exec_driver_sql(
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
        with self._transaction() as conn:
            conn.exec_driver_sql(
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
