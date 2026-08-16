from __future__ import annotations

import sqlite3
from pathlib import Path

from takt.migrations_runtime import stamp, upgrade_to_head

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"
HEAD_REVISION = "0001"


def _ensure_schema(path: Path) -> None:
    probe = sqlite3.connect(path)
    try:
        has_alembic_version = (
            probe.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
            ).fetchone()
            is not None
        )
        has_legacy_schema = (
            probe.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
            ).fetchone()
            is not None
        )
    finally:
        probe.close()
    if not has_alembic_version and has_legacy_schema:
        # A pre-Alembic database already has this (and only this) schema on
        # disk; record it as such instead of replaying migrations against it.
        stamp(MIGRATIONS_DIRECTORY, path, HEAD_REVISION)
    upgrade_to_head(MIGRATIONS_DIRECTORY, path)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(path)
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection
