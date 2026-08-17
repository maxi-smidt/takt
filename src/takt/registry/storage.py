"""The registry's SQLite-backed store.

`RegistryStore` composes one mixin per concern (see `takt.registry.store`):
devices, releases, jobs, mirrors/diagnostics, maintenance, and deployments.
This module keeps schema bootstrap/migration (`__init__`) and connection
lifecycle (`close`), since those touch every concern at once.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from takt.migrations_runtime import upgrade_to_head
from takt.registry.accounts import AccountStore
from takt.registry.job_secrets import JobSecretCipher
from takt.registry.store.common import (
    JOB_LEASE_SECONDS,
    JOB_TERMINAL_STATUSES,
    SCHEMA_VERSION,
    hash_secret,
    utc_iso,
    utc_now,
)
from takt.registry.store.deployments import DeploymentsMixin
from takt.registry.store.devices import DevicesMixin
from takt.registry.store.jobs import JobsMixin
from takt.registry.store.maintenance import MaintenanceMixin
from takt.registry.store.mirrors import MirrorDiagnosticsMixin
from takt.registry.store.releases import ReleasesMixin

__all__ = [
    "JOB_LEASE_SECONDS",
    "JOB_TERMINAL_STATUSES",
    "SCHEMA_VERSION",
    "RegistryStore",
    "hash_secret",
    "utc_iso",
    "utc_now",
]

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"


class RegistryStore(
    DevicesMixin,
    ReleasesMixin,
    JobsMixin,
    MirrorDiagnosticsMixin,
    MaintenanceMixin,
    DeploymentsMixin,
):
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
