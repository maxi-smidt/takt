from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_number INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    actual_time_ms INTEGER NOT NULL CHECK (actual_time_ms >= 0),
    added_time_ms INTEGER NOT NULL DEFAULT 0 CHECK (added_time_ms >= 0),
    total_time_ms INTEGER NOT NULL CHECK (
        total_time_ms >= 0 AND total_time_ms = actual_time_ms + added_time_ms
    ),
    session_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (session_date, run_number)
);

CREATE INDEX IF NOT EXISTS idx_runs_session_date
ON runs(session_date);

CREATE INDEX IF NOT EXISTS idx_runs_total_time
ON runs(total_time_ms, actual_time_ms, saved_at);
"""


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.executescript(SCHEMA)
    row = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    if row and row[0] == 0:
        connection.execute("INSERT INTO schema_version(version) VALUES (1)")
    connection.commit()
    return connection
