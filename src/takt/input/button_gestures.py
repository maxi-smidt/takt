from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class GestureMode(Enum):
    """State-dependent interpretation of a physical button press."""

    IMMEDIATE = "immediate"
    STOPPED = "stopped"
    IGNORE = "ignore"


class ButtonGesture(Enum):
    SHORT = "short"
    LONG = "long"
    DOUBLE = "double"


@dataclass(frozen=True, slots=True)
class GestureEvent:
    gesture: ButtonGesture
    source: str


class _Phase(Enum):
    IDLE = "idle"
    IMMEDIATE_PRESSED = "immediate_pressed"
    IGNORED_PRESSED = "ignored_pressed"
    FIRST_PRESSED = "first_pressed"
    WAITING_FOR_SECOND = "waiting_for_second"
    SECOND_PRESSED = "second_pressed"
    CONSUMED = "consumed"


class ButtonGestureRecognizer:
    """Recognize short, long, and stopped-state double button gestures.

    The recognizer has no GPIO or event-loop dependency. Callers feed it
    debounced press/release edges and invoke :meth:`advance` at
    :attr:`next_deadline` when one is present. ``press`` and ``release`` accept
    explicit timestamps so all timing boundaries can be tested with a fake
    monotonic clock.
    """

    def __init__(
        self,
        mode: Callable[[], GestureMode],
        *,
        double_press_seconds: float = 0.60,
        long_press_seconds: float = 1.00,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if double_press_seconds <= 0 or long_press_seconds <= 0:
            raise ValueError("gesture thresholds must be positive")
        self._mode = mode
        self.double_press_seconds = double_press_seconds
        self.long_press_seconds = long_press_seconds
        self._monotonic = monotonic
        self._phase = _Phase.IDLE
        self._pressed_at: float | None = None
        self._first_pressed_at: float | None = None
        self._second_pressed_at: float | None = None
        self._deadline: float | None = None
        self._source = "button"

    @property
    def next_deadline(self) -> float | None:
        return self._deadline

    def press(self, source: str = "button", now: float | None = None) -> tuple[GestureEvent, ...]:
        timestamp = self._timestamp(now)
        events = list(self._advance(timestamp, expire_equal=True, expire_waiting_equal=False))

        if self._phase is _Phase.WAITING_FOR_SECOND:
            assert self._deadline is not None
            if timestamp <= self._deadline:
                self._phase = _Phase.SECOND_PRESSED
                self._pressed_at = timestamp
                self._second_pressed_at = timestamp
                self._source = source
                self._deadline = timestamp + self.long_press_seconds
                return tuple(events)
            self.reset()

        if self._phase is not _Phase.IDLE:
            return tuple(events)

        self._source = source
        self._pressed_at = timestamp
        mode = self._mode()
        if mode is GestureMode.IMMEDIATE:
            self._phase = _Phase.IMMEDIATE_PRESSED
            events.append(GestureEvent(ButtonGesture.SHORT, source))
        elif mode is GestureMode.STOPPED:
            self._phase = _Phase.FIRST_PRESSED
            self._first_pressed_at = timestamp
            self._deadline = timestamp + self.long_press_seconds
        else:
            self._phase = _Phase.IGNORED_PRESSED
        return tuple(events)

    def release(self, now: float | None = None) -> tuple[GestureEvent, ...]:
        timestamp = self._timestamp(now)
        if self._phase in (_Phase.IDLE, _Phase.WAITING_FOR_SECOND):
            return ()

        events = list(self._advance(timestamp, expire_equal=True, expire_waiting_equal=False))
        if self._phase in (
            _Phase.IMMEDIATE_PRESSED,
            _Phase.IGNORED_PRESSED,
            _Phase.CONSUMED,
        ):
            self.reset()
            return tuple(events)

        if self._phase is _Phase.FIRST_PRESSED:
            assert self._first_pressed_at is not None
            double_deadline = self._first_pressed_at + self.double_press_seconds
            if timestamp <= double_deadline:
                self._phase = _Phase.WAITING_FOR_SECOND
                self._pressed_at = None
                self._deadline = double_deadline
            else:
                self.reset()
            return tuple(events)

        if self._phase is _Phase.SECOND_PRESSED:
            assert self._first_pressed_at is not None
            assert self._second_pressed_at is not None
            if self._second_pressed_at <= self._first_pressed_at + self.double_press_seconds:
                events.append(GestureEvent(ButtonGesture.DOUBLE, self._source))
            self.reset()
        return tuple(events)

    def advance(self, now: float | None = None) -> tuple[GestureEvent, ...]:
        return self._advance(self._timestamp(now), expire_equal=True, expire_waiting_equal=True)

    def reset(self) -> None:
        self._phase = _Phase.IDLE
        self._pressed_at = None
        self._first_pressed_at = None
        self._second_pressed_at = None
        self._deadline = None

    def _advance(
        self,
        timestamp: float,
        *,
        expire_equal: bool,
        expire_waiting_equal: bool,
    ) -> tuple[GestureEvent, ...]:
        if self._deadline is None:
            return ()
        due = timestamp > self._deadline or (expire_equal and timestamp == self._deadline)
        if self._phase is _Phase.WAITING_FOR_SECOND and timestamp == self._deadline:
            due = expire_waiting_equal
        if not due:
            return ()
        if self._phase in (_Phase.FIRST_PRESSED, _Phase.SECOND_PRESSED):
            self._phase = _Phase.CONSUMED
            self._deadline = None
            return (GestureEvent(ButtonGesture.LONG, self._source),)
        if self._phase is _Phase.WAITING_FOR_SECOND:
            self.reset()
        return ()

    def _timestamp(self, now: float | None) -> float:
        timestamp = self._monotonic() if now is None else now
        if timestamp < 0:
            raise ValueError("gesture timestamp must not be negative")
        return timestamp
