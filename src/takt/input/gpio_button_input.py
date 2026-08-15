from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Lock, RLock, Timer
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class ButtonLike(Protocol):
    when_pressed: Callable[[], None] | None
    when_released: Callable[[], None] | None

    def close(self) -> None: ...


class TimerLike(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


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
    """Debounce both edges while preserving a short press and release.

    The first edge is delivered immediately. Any further edges during the
    debounce interval are coalesced to the final observed state and settled
    once the interval expires. This is important because gpiozero updates its
    own state before invoking callbacks and will not re-deliver a callback that
    this class suppresses.
    """

    def __init__(
        self,
        debounce_seconds: float,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        timer_factory: Callable[[float, Callable[[], None]], TimerLike] = Timer,
    ) -> None:
        self._debounce_seconds = debounce_seconds
        self._on_press = on_press
        self._on_release = on_release
        self._monotonic = monotonic
        self._timer_factory = timer_factory
        self._delivered_pressed: bool | None = None
        self._pending_pressed: bool | None = None
        self._debounce_deadline: float | None = None
        self._timer: TimerLike | None = None
        self._generation = 0
        self._closed = False
        self._lock = RLock()

    def press(self) -> None:
        self._accept(True)

    def release(self) -> None:
        self._accept(False)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel_timer_locked()
            self._pending_pressed = None
            self._debounce_deadline = None

    def _accept(self, pressed: bool) -> None:
        now = self._monotonic()
        callbacks: list[Callable[[], None]] = []
        with self._lock:
            if self._closed:
                return

            if self._debounce_deadline is not None and now >= self._debounce_deadline:
                callbacks.extend(self._finish_window_locked(now))

            if self._debounce_seconds <= 0:
                self._delivered_pressed = pressed
                callbacks.append(self._callback_for(pressed))
            elif self._debounce_deadline is None:
                self._delivered_pressed = pressed
                callbacks.append(self._callback_for(pressed))
                self._pending_pressed = pressed
                self._debounce_deadline = now + self._debounce_seconds
                self._schedule_timer_locked(self._debounce_seconds)
            else:
                self._pending_pressed = pressed

            for callback in callbacks:
                callback()

    def _callback_for(self, pressed: bool) -> Callable[[], None]:
        return self._on_press if pressed else self._on_release

    def _schedule_timer_locked(self, delay: float) -> None:
        self._cancel_timer_locked()
        self._generation += 1
        generation = self._generation
        timer = self._timer_factory(
            max(delay, 0.0),
            lambda: self._timer_fired(generation),
        )
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _timer_fired(self, generation: int) -> None:
        callbacks: list[Callable[[], None]] = []
        with self._lock:
            if self._closed or generation != self._generation:
                return
            deadline = self._debounce_deadline
            if deadline is None:
                return
            now = self._monotonic()
            remaining = deadline - now
            if remaining > 0:
                self._schedule_timer_locked(remaining)
                return
            callbacks.extend(self._finish_window_locked(now))

            for callback in callbacks:
                callback()

    def _finish_window_locked(
        self,
        now: float,
    ) -> list[Callable[[], None]]:
        pending_pressed = self._pending_pressed
        self._pending_pressed = None
        self._debounce_deadline = None
        self._cancel_timer_locked()
        if pending_pressed is None or pending_pressed == self._delivered_pressed:
            return []
        self._delivered_pressed = pending_pressed
        self._pending_pressed = pending_pressed
        self._debounce_deadline = now + self._debounce_seconds
        self._schedule_timer_locked(self._debounce_seconds)
        return [self._callback_for(pending_pressed)]

    def _cancel_timer_locked(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


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
        timer_factory: Callable[[float, Callable[[], None]], TimerLike] = Timer,
    ) -> None:
        if button_factory is None:
            from gpiozero import Button

            button_factory = Button

        self._edge_debouncer = SharedEdgeDebouncer(
            bounce_seconds,
            on_press,
            on_release or (lambda: None),
            monotonic=monotonic,
            timer_factory=timer_factory,
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
        self._edge_debouncer.close()
        self._button.close()
