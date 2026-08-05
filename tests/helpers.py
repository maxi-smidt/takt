from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value_ns = 0
        self.wall_clock = datetime(2026, 8, 5, 9, 15, tzinfo=UTC)

    def monotonic_ns(self) -> int:
        return self.monotonic_value_ns

    def now(self) -> datetime:
        return self.wall_clock

    def advance_ms(self, milliseconds: int) -> None:
        self.monotonic_value_ns += milliseconds * 1_000_000
        self.wall_clock += timedelta(milliseconds=milliseconds)
