from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class Buzzer(Protocol):
    def signal(self, event: str) -> None: ...

    def close(self) -> None: ...


class NullBuzzer:
    def signal(self, event: str) -> None:
        pass

    def close(self) -> None:
        pass


class MockBuzzer:
    """Laptop mock: delegates the visible/audible signal to the UI."""

    def __init__(self, on_signal: Callable[[str], None]) -> None:
        self.on_signal = on_signal

    def signal(self, event: str) -> None:
        LOGGER.info("mock_buzzer event=%s", event)
        self.on_signal(event)

    def close(self) -> None:
        pass


class GpioBuzzer:
    """Short pulse on a configurable output pin; used only on Raspberry Pi."""

    def __init__(self, pin_bcm: int) -> None:
        from gpiozero import Buzzer as GpioZeroBuzzer

        self._buzzer = GpioZeroBuzzer(pin_bcm)

    def signal(self, event: str) -> None:
        self._buzzer.beep(on_time=0.08, off_time=0.04, n=1, background=True)

    def close(self) -> None:
        self._buzzer.close()
