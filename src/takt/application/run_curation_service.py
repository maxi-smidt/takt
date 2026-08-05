from __future__ import annotations

from takt.domain.duration import Duration
from takt.domain.run import Run
from takt.persistence.run_repository import SQLiteRunRepository


class RunCurationService:
    """Controlled corrections for previously saved runs."""

    def __init__(self, repository: SQLiteRunRepository) -> None:
        self.repository = repository

    def list_runs(self) -> list[Run]:
        return self.repository.get_all_runs()

    def adjust_added_time(self, run_id: int, delta_ms: int) -> Run:
        run = self.repository.get_run(run_id)
        if run is None:
            raise LookupError(f"run {run_id} does not exist")
        corrected_ms = max(0, run.added_time.milliseconds + delta_ms)
        return self.repository.update_added_time(run_id, Duration(corrected_ms))

    def delete_run(self, run_id: int) -> bool:
        return self.repository.delete_run(run_id)

