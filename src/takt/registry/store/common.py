"""Shared constants, helpers, and cross-mixin typing for the registry store.

`RegistryStore` (in `takt.registry.storage`) composes the mixins in this
package. Each mixin only implements one concern (devices, releases, jobs,
mirrors/diagnostics, maintenance, deployments), but several methods call
across concerns (e.g. `create_job` looks up a device). `RegistryStoreState`
declares that shared surface so mypy can check those calls; it is never
used as a real base class at runtime.
"""

from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.engine import Connection, Engine

SCHEMA_VERSION = 13
JOB_TERMINAL_STATUSES = {"succeeded", "failed", "rolled_back", "cancelled"}
JOB_LEASE_SECONDS = 120


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def device_is_online(
    *, last_seen_at: str | None, revoked_at: str | None, poll_seconds: float | None
) -> bool:
    """Shared online-window formula used by both the devices and jobs lists.

    A device counts as online if it heartbeat within three poll cycles plus a
    grace margin -- long enough to absorb a couple of missed/slow beats
    without flapping, short enough that a genuinely offline device is flagged
    quickly.
    """
    if not last_seen_at or revoked_at:
        return False
    heartbeat_interval = float(poll_seconds or 10)
    online_window = min(max(heartbeat_interval * 3 + 15, 30), 180)
    return utc_now() - datetime.fromisoformat(last_seen_at) < timedelta(seconds=online_window)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if TYPE_CHECKING:

    class RegistryStoreState(Protocol):
        """The `RegistryStore` attributes/methods used across mixin files."""

        engine: Engine
        data_directory: Path
        database_path: Path
        release_directory: Path
        mirror_directory: Path
        backup_directory: Path
        bundled_release_status: dict[str, Any]
        bundled_release_directory: Path | None
        job_secret_key_path: Path

        def _transaction(self) -> AbstractContextManager[Connection]:
            """A writable connection, reused if a call is already inside one.

            `RegistryStore` shares one pooled `Engine` across FastAPI's
            threadpool workers. A fresh top-level call checks out its own
            connection and runs it as one real SQLite transaction (mirroring
            the old `with self.connection:` blocks); a call nested inside
            another (e.g. `create_job` -> `_audit`) rejoins that same
            connection/transaction instead of checking out a second one,
            which would otherwise deadlock against SQLite's single writer.
            """
            ...

        def _read(self) -> AbstractContextManager[Connection]:
            """A read connection, reused if a call is already inside one."""
            ...

        def _audit(
            self,
            event: str,
            device_id: str | None = None,
            details: dict[str, Any] | None = None,
        ) -> None: ...

        def get_device(self, device_id: str) -> dict[str, Any] | None: ...

        def expire_stale_leased_jobs(self, device_id: str | None = None) -> None: ...

        def sweep_stale_jobs(self) -> None: ...

        def get_release(self, release_id: str) -> dict[str, Any] | None: ...

        def get_deployment(self, deployment_id: str) -> dict[str, Any] | None: ...

        def _link_deployment_for_code(self, code_hash: str, device_id: str) -> None: ...

    _Base = RegistryStoreState
else:
    _Base = object
