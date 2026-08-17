from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event

from takt.migrations_runtime import stamp, upgrade_to_head

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"
HEAD_REVISION = "0001"


def _ensure_schema(path: Path) -> None:
    # Pre-Alembic bootstrap probing happens before the SQLAlchemy engine
    # exists, so it talks to the file directly (same pattern as
    # RegistryStore.__init__ probing "PRAGMA user_version").
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


def connect_database(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(path)
    engine = create_engine(f"sqlite:///{path}", future=True, connect_args={"timeout": 10.0})

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = FULL")
        cursor.close()

    return engine
