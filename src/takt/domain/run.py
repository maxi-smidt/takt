from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from takt.domain.duration import Duration


@dataclass(frozen=True, slots=True)
class Run:
    id: int | None
    run_number: int
    started_at: datetime
    stopped_at: datetime
    saved_at: datetime
    actual_time: Duration
    added_time: Duration
    total_time: Duration
    note: str | None = None

    def __post_init__(self) -> None:
        if self.run_number < 1:
            raise ValueError("run_number must be positive")
        if self.total_time != self.actual_time + self.added_time:
            raise ValueError("total time must equal actual time plus added time")
        for value in (self.started_at, self.stopped_at, self.saved_at):
            if value.tzinfo is None:
                raise ValueError("run timestamps must be timezone-aware")

    @property
    def session_date(self) -> str:
        return self.started_at.astimezone().date().isoformat()
