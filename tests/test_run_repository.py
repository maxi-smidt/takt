from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from takt.domain.duration import Duration
from takt.persistence.models import runs
from takt.persistence.run_repository import SQLiteRunRepository


class RunRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = SQLiteRunRepository(Path(self.temporary_directory.name) / "runs.db")
        self.base = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def save_run(
        self,
        start: datetime,
        actual_ms: int,
        added_ms: int = 0,
        note: str | None = None,
    ):
        return self.repository.create_and_save(
            started_at=start,
            stopped_at=start + timedelta(milliseconds=actual_ms),
            saved_at=start + timedelta(milliseconds=actual_ms + 100),
            actual_time=Duration(actual_ms),
            added_time=Duration(added_ms),
            note=note,
        )

    def test_numbers_runs_sequentially_per_day(self) -> None:
        first = self.save_run(self.base, 80_000)
        second = self.save_run(self.base + timedelta(hours=1), 79_000)
        next_day = self.save_run(self.base + timedelta(days=1), 81_000)
        self.assertEqual((first.run_number, second.run_number), (1, 2))
        self.assertEqual(next_day.run_number, 1)

    def test_best_runs_use_total_then_actual_time(self) -> None:
        self.save_run(self.base, 70_000, 20_000)
        second = self.save_run(self.base + timedelta(minutes=5), 81_000, 0)
        third = self.save_run(self.base + timedelta(minutes=10), 79_000, 2_000)
        best = self.repository.get_best_runs(3)
        self.assertEqual([run.id for run in best], [third.id, second.id, 1])

    def test_best_runs_for_date_only_rank_that_session_date(self) -> None:
        self.save_run(self.base - timedelta(days=1), 60_000)
        first_today = self.save_run(self.base, 70_000, 20_000)
        second_today = self.save_run(self.base + timedelta(minutes=5), 81_000)

        best_today = self.repository.get_best_runs_for_date(self.base.date(), 2)

        self.assertEqual([run.id for run in best_today], [second_today.id, first_today.id])

    def test_worst_runs_use_total_then_actual_time(self) -> None:
        first = self.save_run(self.base, 70_000, 20_000)
        second = self.save_run(self.base + timedelta(minutes=5), 81_000, 0)
        third = self.save_run(self.base + timedelta(minutes=10), 79_000, 2_000)
        worst = self.repository.get_worst_runs(3)
        self.assertEqual([run.id for run in worst], [first.id, second.id, third.id])

    def test_every_run_is_a_separate_chart_observation(self) -> None:
        self.save_run(self.base, 80_000)
        self.save_run(self.base + timedelta(minutes=15), 81_000)
        self.save_run(self.base + timedelta(minutes=30), 82_000)
        self.assertEqual(len(self.repository.get_recent_runs(None)), 3)

    def test_saved_added_time_can_be_curated_without_changing_actual_time(self) -> None:
        run = self.save_run(self.base, 80_000, 10_000)
        assert run.id is not None
        updated = self.repository.update_added_time(run.id, Duration(5_000))
        self.assertEqual(updated.actual_time, Duration(80_000))
        self.assertEqual(updated.added_time, Duration(5_000))
        self.assertEqual(updated.total_time, Duration(85_000))

    def test_saved_run_can_be_deleted(self) -> None:
        run = self.save_run(self.base, 80_000)
        assert run.id is not None
        self.assertTrue(self.repository.delete_run(run.id))
        self.assertIsNone(self.repository.get_run(run.id))
        self.assertFalse(self.repository.delete_run(run.id))

    def test_csv_export_preserves_order_and_escaped_values(self) -> None:
        first = self.save_run(self.base, 80_000, note="first, \"run\"")
        self.save_run(
            self.base + timedelta(minutes=5),
            81_000,
            note="second\nrun",
        )
        self.save_run(
            self.base + timedelta(minutes=10),
            82_000,
            note="=HYPERLINK(https://example.test) ä",
        )

        target = Path(self.temporary_directory.name) / "runs.csv"
        self.repository.export_runs_csv(target)

        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))
        with target.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([int(row["id"]) for row in rows], [first.id, 2, 3])
        self.assertEqual(rows[0]["note"], "first, \"run\"")
        self.assertEqual(rows[1]["note"], "second\nrun")
        self.assertEqual(
            rows[2]["note"],
            chr(39) + "=HYPERLINK(https://example.test) ä",
        )
        self.assertEqual(
            list(rows[0]),
            list(SQLiteRunRepository.CSV_EXPORT_COLUMNS),
        )

    def test_database_rejects_inconsistent_total(self) -> None:
        with self.assertRaises(IntegrityError), self.repository.engine.begin() as conn:
            conn.execute(
                runs.insert().values(
                    run_number=1,
                    started_at=self.base.isoformat(),
                    stopped_at=self.base.isoformat(),
                    saved_at=self.base.isoformat(),
                    actual_time_ms=1000,
                    added_time_ms=500,
                    total_time_ms=1000,
                    session_date=self.base.date().isoformat(),
                    created_at=self.base.isoformat(),
                    updated_at=self.base.isoformat(),
                )
            )


if __name__ == "__main__":
    unittest.main()
