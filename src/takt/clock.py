from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    def now(self) -> datetime: ...


class SystemClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def now(self) -> datetime:
        return datetime.now().astimezone()

