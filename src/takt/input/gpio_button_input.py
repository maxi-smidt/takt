from __future__ import annotations

import logging
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)


class GpioButtonInput:
    """Active-low gpiozero button, imported lazily for laptop compatibility."""

    def __init__(
        self,
        pin_bcm: int,
        bounce_seconds: float,
        on_press: Callable[[], None],
    ) -> None:
        from gpiozero import Button

        self._button = Button(
            pin=pin_bcm,
            pull_up=True,
            bounce_time=bounce_seconds,
        )
        self._button.when_pressed = on_press
        self.available = True
        LOGGER.info("GPIO button initialized")

    def close(self) -> None:
        self._button.close()

