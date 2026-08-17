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
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

SCHEMA_VERSION = 13
JOB_TERMINAL_STATUSES = {"succeeded", "failed", "rolled_back", "cancelled"}
JOB_LEASE_SECONDS = 120


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if TYPE_CHECKING:

    class RegistryStoreState(Protocol):
        """The `RegistryStore` attributes/methods used across mixin files."""

        connection: sqlite3.Connection
        data_directory: Path
        database_path: Path
        release_directory: Path
        mirror_directory: Path
        backup_directory: Path
        bundled_release_status: dict[str, Any]
        job_secret_key_path: Path

        def _audit(
            self,
            event: str,
            device_id: str | None = None,
            details: dict[str, Any] | None = None,
        ) -> None: ...

        def get_device(self, device_id: str) -> dict[str, Any] | None: ...

        def get_release(self, release_id: str) -> dict[str, Any] | None: ...

        def get_deployment(self, deployment_id: str) -> dict[str, Any] | None: ...

        def _link_deployment_for_code(self, code_hash: str, device_id: str) -> None: ...

    _Base = RegistryStoreState
else:
    _Base = object
