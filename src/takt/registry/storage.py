"""The registry's SQLite-backed store.

`RegistryStore` composes one mixin per concern (see `takt.registry.store`):
devices, releases, jobs, mirrors/diagnostics, maintenance, and deployments.
This module keeps schema bootstrap/migration (`__init__`) and connection
lifecycle (`close`), since those touch every concern at once.
"""

from __future__ import annotations

import contextvars
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection

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

        # A single pooled Engine, shared across every mixin and AccountStore.
        # FastAPI dispatches sync route/dependency code onto a threadpool, so
        # unlike a single shared sqlite3.Connection (not safe for concurrent
        # use even with check_same_thread=False), each `_transaction`/`_read`
        # call below checks out its own connection from the pool. Nested
        # calls on the same logical call stack (e.g. `create_job` ->
        # `_audit`) rejoin that connection via `_active_connection` instead
        # of checking out a second one, which would otherwise deadlock
        # against SQLite's single writer.
        self.engine = create_engine(
            f"sqlite:///{self.database_path}",
            future=True,
            connect_args={"check_same_thread": not allow_thread_handoff, "timeout": 10},
        )

        @event.listens_for(self.engine, "connect")
        def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
            dbapi_connection.row_factory = sqlite3.Row
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout = 10000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = FULL")
            cursor.close()

        self._active_connection: contextvars.ContextVar[Connection | None] = (
            contextvars.ContextVar("registry_active_connection", default=None)
        )
        self.accounts = AccountStore(self.engine)
        with self._transaction() as conn:
            self._rebuild_disruptive_index()
            conn.exec_driver_sql(
                """
                UPDATE deployments SET status = 'interrupted', stage = 'interrupted',
                    message = 'Registry restarted while deployment was active', updated_at = ?
                WHERE status IN ('pending', 'running')
                """,
                (utc_iso(),),
            )
            conn.exec_driver_sql(
                """
                UPDATE jobs SET status = 'queued', claimed_at = NULL,
                    message = 'Registry upgraded; retrying job safely'
                WHERE status IN ('claimed', 'running') AND lease_expires_at IS NULL
                """
            )
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.prune()

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        current = self._active_connection.get()
        if current is not None:
            yield current
            return
        with self.engine.begin() as conn:
            token = self._active_connection.set(conn)
            try:
                yield conn
            finally:
                self._active_connection.reset(token)

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        current = self._active_connection.get()
        if current is not None:
            yield current
            return
        with self.engine.connect() as conn:
            token = self._active_connection.set(conn)
            try:
                yield conn
            finally:
                self._active_connection.reset(token)

    def close(self) -> None:
        with self._read() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        self.engine.dispose()
