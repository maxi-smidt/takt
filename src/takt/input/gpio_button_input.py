from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Lock
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class ButtonLike(Protocol):
    when_pressed: Callable[[], None] | None
    when_released: Callable[[], None] | None

    def close(self) -> None: ...


class ImmediateEdgeDebouncer:
    """Accept the first edge immediately and suppress contact bounce."""

    def __init__(
        self,
        debounce_seconds: float,
        callback: Callable[[], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._debounce_seconds = debounce_seconds
        self._callback = callback
        self._monotonic = monotonic
        self._last_edge_at: float | None = None
        self._lock = Lock()

    def __call__(self) -> None:
        now = self._monotonic()
        with self._lock:
            if (
                self._last_edge_at is not None
                and now - self._last_edge_at < self._debounce_seconds
            ):
                return
            self._last_edge_at = now
        self._callback()


class ImmediatePressDebouncer(ImmediateEdgeDebouncer):
    """Accept the first falling edge immediately and suppress contact bounce."""

    def __init__(
        self,
        debounce_seconds: float,
        on_press: Callable[[], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(debounce_seconds, on_press, monotonic=monotonic)


class SharedEdgeDebouncer:
    """Debounce press and release edges through one shared time gate."""

    def __init__(
        self,
        debounce_seconds: float,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._debounce_seconds = debounce_seconds
        self._on_press = on_press
        self._on_release = on_release
        self._monotonic = monotonic
        self._last_edge_at: float | None = None
        self._lock = Lock()

    def press(self) -> None:
        self._accept(self._on_press)

    def release(self) -> None:
        self._accept(self._on_release)

    def _accept(self, callback: Callable[[], None]) -> None:
        now = self._monotonic()
        with self._lock:
            if (
                self._last_edge_at is not None
                and now - self._last_edge_at < self._debounce_seconds
            ):
                return
            self._last_edge_at = now
        callback()


class GpioButtonInput:
    """Active-low gpiozero button, imported lazily for laptop compatibility."""

    def __init__(
        self,
        pin_bcm: int,
        bounce_seconds: float,
        on_press: Callable[[], None],
        *,
        on_release: Callable[[], None] | None = None,
        button_factory: Callable[..., ButtonLike] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if button_factory is None:
            from gpiozero import Button

            button_factory = Button

        self._edge_debouncer = SharedEdgeDebouncer(
            bounce_seconds,
            on_press,
            on_release or (lambda: None),
            monotonic=monotonic,
        )
        self._button = button_factory(
            pin=pin_bcm,
            pull_up=True,
            # Driver-level debounce can behave like a minimum pulse filter on
            # some pin factories. React to the first edge immediately instead.
            bounce_time=None,
        )
        # gpiozero introspects callback functions and does not support arbitrary
        # callable objects here. A bound method preserves the debouncer while
        # matching gpiozero's supported callback shape.
        self._button.when_pressed = self._edge_debouncer.press
        self._button.when_released = self._edge_debouncer.release
        self.available = True
        LOGGER.info(
            "GPIO button initialized pin_bcm=%s software_debounce_seconds=%s",
            pin_bcm,
            bounce_seconds,
        )

    def close(self) -> None:
        self._button.close()
