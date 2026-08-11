from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Duration:
    """A non-negative duration stored with millisecond precision."""

    milliseconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.milliseconds, int):
            raise TypeError("milliseconds must be an integer")
        if self.milliseconds < 0:
            raise ValueError("duration cannot be negative")

    def __add__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.milliseconds + other.milliseconds)

    def format_stopwatch(self) -> str:
        """Format as MM:SS.hh while retaining full precision internally."""
        total_hundredths = self.milliseconds // 10
        hundredths = total_hundredths % 100
        total_seconds = total_hundredths // 100
        seconds = total_seconds % 60
        minutes = total_seconds // 60
        return f"{minutes:02d}:{seconds:02d}.{hundredths:02d}"

    def format_added(self) -> str:
        return f"+{self.format_stopwatch()}"
