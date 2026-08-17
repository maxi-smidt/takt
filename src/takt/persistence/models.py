"""SQLAlchemy Core table definitions for the Pi run database.

This is the source of truth for the schema; `migrations/versions/` applies
it (and any future changes to it) as Alembic revisions. Application code
still talks to the database through raw `sqlite3` (see `run_repository.py`)
so these are plain Core `Table` objects rather than an ORM mapping.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_number", Integer, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("stopped_at", Text, nullable=False),
    Column("saved_at", Text, nullable=False),
    Column("actual_time_ms", Integer, nullable=False),
    Column("added_time_ms", Integer, nullable=False, server_default=text("0")),
    Column("total_time_ms", Integer, nullable=False),
    Column("session_date", Text, nullable=False),
    Column("note", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("actual_time_ms >= 0", name="ck_runs_actual_time_ms"),
    CheckConstraint("added_time_ms >= 0", name="ck_runs_added_time_ms"),
    CheckConstraint(
        "total_time_ms >= 0 AND total_time_ms = actual_time_ms + added_time_ms",
        name="ck_runs_total_time_ms",
    ),
    UniqueConstraint("session_date", "run_number", name="uq_runs_session_date_run_number"),
    sqlite_autoincrement=True,
)

Index("idx_runs_session_date", runs.c.session_date)
Index("idx_runs_total_time", runs.c.total_time_ms, runs.c.actual_time_ms, runs.c.saved_at)

remote_command_receipts = Table(
    "remote_command_receipts",
    metadata,
    Column("command_id", Text, primary_key=True),
    Column("operation", Text, nullable=False),
    Column("result_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
