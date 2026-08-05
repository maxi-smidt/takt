from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from takt.clock import Clock
from takt.domain.duration import Duration
from takt.domain.run import Run
from takt.domain.timer_session import TimerSession
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TimerSnapshot:
    state: TimerState
    actual_time: Duration
    added_time: Duration
    total_time: Duration
    last_saved_run: Run | None
    error_message: str | None = None


class TimerController:
    """Authoritative state machine for the complete timing workflow."""

    def __init__(
        self,
        clock: Clock,
        repository: SQLiteRunRepository,
        double_press_seconds: float = 0.60,
    ) -> None:
        self.clock = clock
        self.repository = repository
        self.double_press_ns = int(double_press_seconds * 1_000_000_000)
        self.state = TimerState.READY
        self.session = TimerSession()
        self.last_saved_run: Run | None = None
        self.error_message: str | None = None
        self._last_stopped_press_ns: int | None = None
        self._listeners: list[Callable[[TimerSnapshot], None]] = []

    def subscribe(self, listener: Callable[[TimerSnapshot], None]) -> None:
        self._listeners.append(listener)

    def snapshot(self) -> TimerSnapshot:
        actual = (
            self.session.elapsed(self.clock.monotonic_ns())
            if self.state is TimerState.RUNNING
            else self.session.actual_time
        )
        return TimerSnapshot(
            state=self.state,
            actual_time=actual,
            added_time=self.session.added_time,
            total_time=actual + self.session.added_time,
            last_saved_run=self.last_saved_run,
            error_message=self.error_message,
        )

    def refresh(self) -> None:
        if self.state is TimerState.RUNNING:
            self._notify()

    def start(self, source: str = "keyboard") -> bool:
        if self.state is not TimerState.READY:
            return False
        self.session.start(self.clock.now(), self.clock.monotonic_ns())
        self._transition(TimerState.RUNNING, source=source)
        return True

    def stop(self, source: str = "keyboard") -> bool:
        if self.state is not TimerState.RUNNING:
            return False
        self.session.stop(self.clock.now(), self.clock.monotonic_ns())
        self._transition(
            TimerState.STOPPED,
            source=source,
            actual_ms=self.session.actual_time.milliseconds,
        )
        return True

    def handle_primary_button_press(self, source: str = "button") -> bool:
        if self.state is TimerState.READY:
            return self.start(source)
        if self.state is TimerState.RUNNING:
            return self.stop(source)
        if self.state is TimerState.STOPPED:
            now_ns = self.clock.monotonic_ns()
            previous = self._last_stopped_press_ns
            self._last_stopped_press_ns = now_ns
            if previous is not None and 0 <= now_ns - previous <= self.double_press_ns:
                self._last_stopped_press_ns = None
                self.discard_immediately(source)
                return True
        return False

    def add_time(self, milliseconds: int) -> bool:
        if self.state is not TimerState.STOPPED or milliseconds < 0:
            return False
        self.session.add_time(milliseconds)
        LOGGER.info(
            "adjustment added_ms=%s total_added_ms=%s",
            milliseconds,
            self.session.added_time.milliseconds,
        )
        self._notify()
        return True

    def subtract_time(self, milliseconds: int) -> bool:
        if self.state is not TimerState.STOPPED or milliseconds < 0:
            return False
        previous = self.session.added_time.milliseconds
        self.session.subtract_time(milliseconds)
        LOGGER.info(
            "adjustment subtracted_ms=%s previous_added_ms=%s total_added_ms=%s",
            milliseconds,
            previous,
            self.session.added_time.milliseconds,
        )
        self._notify()
        return True

    def save(self) -> Run | None:
        if self.state is not TimerState.STOPPED:
            return None
        if self.session.started_wall_clock is None or self.session.stopped_wall_clock is None:
            return None
        try:
            run = self.repository.create_and_save(
                started_at=self.session.started_wall_clock,
                stopped_at=self.session.stopped_wall_clock,
                saved_at=self.clock.now(),
                actual_time=self.session.actual_time,
                added_time=self.session.added_time,
            )
        except Exception:
            LOGGER.exception("run_save_failed")
            self.error_message = "Der Lauf konnte nicht gespeichert werden. Bitte erneut versuchen."
            self._notify()
            return None
        self.last_saved_run = run
        self.error_message = None
        LOGGER.info("run_saved id=%s total_ms=%s", run.id, run.total_time.milliseconds)
        self._transition(TimerState.SAVED_CONFIRMATION, run_id=run.id)
        return run

    def request_discard(self) -> bool:
        if self.state is not TimerState.STOPPED:
            return False
        self._transition(TimerState.DISCARD_CONFIRMATION)
        return True

    def cancel_discard(self) -> bool:
        if self.state is not TimerState.DISCARD_CONFIRMATION:
            return False
        self._transition(TimerState.STOPPED)
        return True

    def confirm_discard(self) -> bool:
        if self.state is not TimerState.DISCARD_CONFIRMATION:
            return False
        self._reset_to_ready("keyboard")
        return True

    def discard_immediately(self, source: str = "button") -> None:
        if self.state is TimerState.STOPPED:
            self._reset_to_ready(source)

    def finish_confirmation(self) -> bool:
        if self.state is not TimerState.SAVED_CONFIRMATION:
            return False
        self._reset_to_ready("confirmation_timeout")
        return True

    def _reset_to_ready(self, source: str) -> None:
        self.session.reset()
        self._last_stopped_press_ns = None
        self.error_message = None
        self._transition(TimerState.READY, source=source)

    def _transition(self, target: TimerState, **context: object) -> None:
        previous = self.state
        self.state = target
        LOGGER.info("state %s -> %s %s", previous.name, target.name, context)
        self._notify()

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for listener in tuple(self._listeners):
            listener(snapshot)
