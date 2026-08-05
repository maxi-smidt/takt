from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from takt.domain.duration import Duration
from takt.domain.run import Run
from takt.persistence.database import connect_database


class SQLiteRunRepository:
    """Transactional local run repository."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection = connect_database(database_path)

    def close(self) -> None:
        self.connection.close()

    def get_next_run_number(self, session_date: date) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(run_number), 0) + 1 FROM runs WHERE session_date = ?",
            (session_date.isoformat(),),
        ).fetchone()
        return int(row[0])

    def create_and_save(
        self,
        *,
        started_at: datetime,
        stopped_at: datetime,
        saved_at: datetime,
        actual_time: Duration,
        added_time: Duration,
        note: str | None = None,
    ) -> Run:
        session_date = started_at.astimezone().date()
        total_time = actual_time + added_time
        now_iso = saved_at.isoformat()
        with self.connection:
            run_number = self.get_next_run_number(session_date)
            cursor = self.connection.execute(
                """
                INSERT INTO runs (
                    run_number, started_at, stopped_at, saved_at,
                    actual_time_ms, added_time_ms, total_time_ms,
                    session_date, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_number,
                    started_at.isoformat(),
                    stopped_at.isoformat(),
                    saved_at.isoformat(),
                    actual_time.milliseconds,
                    added_time.milliseconds,
                    total_time.milliseconds,
                    session_date.isoformat(),
                    note,
                    now_iso,
                    now_iso,
                ),
            )
        return Run(
            id=int(cursor.lastrowid),
            run_number=run_number,
            started_at=started_at,
            stopped_at=stopped_at,
            saved_at=saved_at,
            actual_time=actual_time,
            added_time=added_time,
            total_time=total_time,
            note=note,
        )

    def get_runs_for_date(self, session_date: date) -> list[Run]:
        rows = self.connection.execute(
            """
            SELECT * FROM runs
            WHERE session_date = ?
            ORDER BY run_number DESC
            """,
            (session_date.isoformat(),),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_best_runs(self, limit: int = 5) -> list[Run]:
        rows = self.connection.execute(
            """
            SELECT * FROM runs
            ORDER BY total_time_ms ASC, actual_time_ms ASC, saved_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_all_runs(self) -> list[Run]:
        rows = self.connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC"
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_run(self, run_id: int) -> Run | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def update_added_time(self, run_id: int, added_time: Duration) -> Run:
        existing = self.get_run(run_id)
        if existing is None:
            raise LookupError(f"run {run_id} does not exist")
        total_time = existing.actual_time + added_time
        updated_at = datetime.now().astimezone().isoformat()
        with self.connection:
            self.connection.execute(
                """
                UPDATE runs
                SET added_time_ms = ?, total_time_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    added_time.milliseconds,
                    total_time.milliseconds,
                    updated_at,
                    run_id,
                ),
            )
        updated = self.get_run(run_id)
        if updated is None:
            raise RuntimeError("updated run unexpectedly disappeared")
        return updated

    def delete_run(self, run_id: int) -> bool:
        with self.connection:
            cursor = self.connection.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0

    def get_recent_runs(self, days: int | None = 30) -> list[Run]:
        if days is None:
            rows = self.connection.execute(
                "SELECT * FROM runs ORDER BY started_at ASC, id ASC"
            ).fetchall()
        else:
            start_date = date.today() - timedelta(days=max(1, days) - 1)
            rows = self.connection.execute(
                """
                SELECT * FROM runs
                WHERE session_date >= ?
                ORDER BY started_at ASC, id ASC
                """,
                (start_date.isoformat(),),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def backup_to(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(target)
        try:
            self.connection.backup(destination)
        finally:
            destination.close()

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Run:
        return Run(
            id=int(row["id"]),
            run_number=int(row["run_number"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            stopped_at=datetime.fromisoformat(row["stopped_at"]),
            saved_at=datetime.fromisoformat(row["saved_at"]),
            actual_time=Duration(int(row["actual_time_ms"])),
            added_time=Duration(int(row["added_time_ms"])),
            total_time=Duration(int(row["total_time_ms"])),
            note=row["note"],
        )
