from __future__ import annotations

import contextvars
import csv
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Row

from takt.domain.duration import Duration
from takt.domain.run import Run
from takt.persistence.database import connect_database
from takt.persistence.models import remote_command_receipts, runs, schema_version

REMOTE_RECEIPT_RETENTION = timedelta(days=90)


class SQLiteRunRepository:
    """Transactional local run repository."""

    CSV_EXPORT_COLUMNS = (
        "id",
        "run_number",
        "session_date",
        "started_at",
        "stopped_at",
        "saved_at",
        "actual_time_ms",
        "added_time_ms",
        "total_time_ms",
        "note",
        "created_at",
        "updated_at",
    )

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.engine = connect_database(database_path)
        self._active_connection: contextvars.ContextVar[Connection | None] = (
            contextvars.ContextVar("run_repository_active_connection", default=None)
        )

    def close(self) -> None:
        self.engine.dispose()

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

    def get_schema_version(self) -> int | None:
        with self._read() as conn:
            row = conn.execute(select(schema_version.c.version).limit(1)).fetchone()
        return int(row.version) if row is not None else None

    def get_next_run_number(self, session_date: date) -> int:
        with self._read() as conn:
            value = conn.execute(
                select(func.coalesce(func.max(runs.c.run_number), 0) + 1).where(
                    runs.c.session_date == session_date.isoformat()
                )
            ).scalar_one()
        return int(value)

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
        with self._transaction() as conn:
            run_number = self.get_next_run_number(session_date)
            result = conn.execute(
                runs.insert().values(
                    run_number=run_number,
                    started_at=started_at.isoformat(),
                    stopped_at=stopped_at.isoformat(),
                    saved_at=saved_at.isoformat(),
                    actual_time_ms=actual_time.milliseconds,
                    added_time_ms=added_time.milliseconds,
                    total_time_ms=total_time.milliseconds,
                    session_date=session_date.isoformat(),
                    note=note,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            inserted_primary_key = result.inserted_primary_key
            assert inserted_primary_key is not None
            run_id = inserted_primary_key[0]
        assert run_id is not None
        return Run(
            id=run_id,
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
        with self._read() as conn:
            rows = conn.execute(
                select(runs)
                .where(runs.c.session_date == session_date.isoformat())
                .order_by(runs.c.run_number.desc())
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_best_runs(self, limit: int = 5) -> list[Run]:
        with self._read() as conn:
            rows = conn.execute(
                select(runs)
                .order_by(
                    runs.c.total_time_ms.asc(),
                    runs.c.actual_time_ms.asc(),
                    runs.c.saved_at.asc(),
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_all_runs(self) -> list[Run]:
        with self._read() as conn:
            rows = conn.execute(
                select(runs).order_by(runs.c.started_at.desc(), runs.c.id.desc())
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_run(self, run_id: int) -> Run | None:
        with self._read() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).fetchone()
        return self._row_to_run(row) if row is not None else None

    def update_added_time(self, run_id: int, added_time: Duration) -> Run:
        existing = self.get_run(run_id)
        if existing is None:
            raise LookupError(f"run {run_id} does not exist")
        total_time = existing.actual_time + added_time
        updated_at = datetime.now().astimezone().isoformat()
        with self._transaction() as conn:
            conn.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(
                    added_time_ms=added_time.milliseconds,
                    total_time_ms=total_time.milliseconds,
                    updated_at=updated_at,
                )
            )
        updated = self.get_run(run_id)
        if updated is None:
            raise RuntimeError("updated run unexpectedly disappeared")
        return updated

    def delete_run(self, run_id: int) -> bool:
        with self._transaction() as conn:
            result = conn.execute(runs.delete().where(runs.c.id == run_id))
        return result.rowcount > 0

    def get_recent_runs(self, days: int | None = 30) -> list[Run]:
        with self._read() as conn:
            if days is None:
                rows = conn.execute(
                    select(runs).order_by(runs.c.started_at.asc(), runs.c.id.asc())
                ).fetchall()
            else:
                start_date = date.today() - timedelta(days=max(1, days) - 1)
                rows = conn.execute(
                    select(runs)
                    .where(runs.c.session_date >= start_date.isoformat())
                    .order_by(runs.c.started_at.asc(), runs.c.id.asc())
                ).fetchall()
        return [self._row_to_run(row) for row in rows]

    # backup_to/export_runs_csv open a plain sqlite3 connection (rather than
    # going through `self.engine`) because backup_to needs sqlite3's native
    # online-backup API, which has no SQLAlchemy equivalent; export_runs_csv
    # shares that same isolated, read-only connection for consistency.
    def backup_to(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._open_export_connection()
        try:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()

    def export_runs_csv(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._open_export_connection()
        try:
            rows = source.execute(
                """
                SELECT id, run_number, session_date, started_at, stopped_at, saved_at,
                       actual_time_ms, added_time_ms, total_time_ms, note,
                       created_at, updated_at
                FROM runs
                ORDER BY started_at ASC, id ASC
                """
            ).fetchall()
        finally:
            source.close()
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(self.CSV_EXPORT_COLUMNS)
            writer.writerows(
                tuple(self._csv_value(row[column]) for column in self.CSV_EXPORT_COLUMNS)
                for row in rows
            )

    def apply_remote_curation(
        self,
        *,
        command_id: str,
        operation: str,
        run_id: int,
        expected_updated_at: str,
        desired_added_time_ms: int | None = None,
    ) -> dict[str, object]:
        with self._transaction() as conn:
            receipt = conn.execute(
                select(
                    remote_command_receipts.c.operation,
                    remote_command_receipts.c.result_json,
                ).where(remote_command_receipts.c.command_id == command_id)
            ).fetchone()
            if receipt is not None:
                if receipt.operation != operation:
                    raise ValueError("The command_id was already used for another operation.")
                return json.loads(receipt.result_json)
            cutoff = (datetime.now().astimezone() - REMOTE_RECEIPT_RETENTION).isoformat()
            conn.execute(
                remote_command_receipts.delete().where(
                    remote_command_receipts.c.created_at < cutoff
                )
            )
            row = conn.execute(select(runs).where(runs.c.id == run_id)).fetchone()
            if row is None or row.updated_at != expected_updated_at:
                raise ValueError("Run changed or no longer exists on the authoritative device.")
            before = self._row_to_run(row)
            result: dict[str, object]
            if operation == "adjust_added_time":
                if (
                    desired_added_time_ms is None
                    or not 0 <= desired_added_time_ms <= 24 * 60 * 60 * 1000
                ):
                    raise ValueError("The requested added time is invalid.")
                now = datetime.now().astimezone().isoformat()
                conn.execute(
                    runs.update()
                    .where(runs.c.id == run_id, runs.c.updated_at == expected_updated_at)
                    .values(
                        added_time_ms=desired_added_time_ms,
                        total_time_ms=runs.c.actual_time_ms + desired_added_time_ms,
                        updated_at=now,
                    )
                )
                updated_row = conn.execute(select(runs).where(runs.c.id == run_id)).fetchone()
                if updated_row is None:
                    raise RuntimeError("Updated run unexpectedly disappeared.")
                result = {
                    "operation": operation,
                    "run": self._run_result(self._row_to_run(updated_row)),
                    "previous": self._run_result(before),
                }
            elif operation == "delete":
                conn.execute(
                    runs.delete().where(
                        runs.c.id == run_id, runs.c.updated_at == expected_updated_at
                    )
                )
                result = {"operation": operation, "deleted": True, "run": self._run_result(before)}
            else:
                raise ValueError("Unsupported run curation operation.")
            conn.execute(
                remote_command_receipts.insert().values(
                    command_id=command_id,
                    operation=operation,
                    result_json=json.dumps(result, separators=(",", ":")),
                    created_at=datetime.now().astimezone().isoformat(),
                )
            )
            return result

    @staticmethod
    def _run_result(run: Run) -> dict[str, object]:
        return {
            "id": run.id,
            "run_number": run.run_number,
            "actual_time_ms": run.actual_time.milliseconds,
            "added_time_ms": run.added_time.milliseconds,
            "total_time_ms": run.total_time.milliseconds,
        }

    def _open_export_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _csv_value(value: object) -> object:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return chr(39) + value
        return value

    @staticmethod
    def _row_to_run(row: Row[Any]) -> Run:
        return Run(
            id=int(row.id),
            run_number=int(row.run_number),
            started_at=datetime.fromisoformat(row.started_at),
            stopped_at=datetime.fromisoformat(row.stopped_at),
            saved_at=datetime.fromisoformat(row.saved_at),
            actual_time=Duration(int(row.actual_time_ms)),
            added_time=Duration(int(row.added_time_ms)),
            total_time=Duration(int(row.total_time_ms)),
            note=row.note,
        )
