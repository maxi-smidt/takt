from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from takt.domain.duration import Duration


@dataclass(slots=True)
class TimerSession:
    """The current, unsaved measurement."""

    started_wall_clock: datetime | None = None
    started_monotonic_ns: int | None = None
    stopped_wall_clock: datetime | None = None
    actual_time: Duration = Duration()
    added_time: Duration = Duration()

    @property
    def total_time(self) -> Duration:
        return self.actual_time + self.added_time

    def start(self, wall_clock: datetime, monotonic_ns: int) -> None:
        if wall_clock.tzinfo is None:
            raise ValueError("wall clock must be timezone-aware")
        self.started_wall_clock = wall_clock
        self.started_monotonic_ns = monotonic_ns
        self.stopped_wall_clock = None
        self.actual_time = Duration()
        self.added_time = Duration()

    def elapsed(self, monotonic_ns: int) -> Duration:
        if self.started_monotonic_ns is None:
            return Duration()
        elapsed_ns = max(0, monotonic_ns - self.started_monotonic_ns)
        return Duration(elapsed_ns // 1_000_000)

    def stop(self, wall_clock: datetime, monotonic_ns: int) -> None:
        if self.started_monotonic_ns is None:
            raise RuntimeError("cannot stop a session that has not started")
        self.actual_time = self.elapsed(monotonic_ns)
        self.stopped_wall_clock = wall_clock

    def add_time(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("only positive additional time is allowed")
        self.added_time = self.added_time + Duration(milliseconds)

    def subtract_time(self, milliseconds: int) -> None:
        """Reduce only the added time, without allowing a negative value."""
        if milliseconds < 0:
            raise ValueError("subtraction amount must be positive")
        remaining = max(0, self.added_time.milliseconds - milliseconds)
        self.added_time = Duration(remaining)

    def reset(self) -> None:
        self.started_wall_clock = None
        self.started_monotonic_ns = None
        self.stopped_wall_clock = None
        self.actual_time = Duration()
        self.added_time = Duration()
