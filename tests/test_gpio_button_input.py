from __future__ import annotations

import inspect
import unittest

from takt.input.gpio_button_input import GpioButtonInput


class FakeButton:
    def __init__(self, **kwargs: object) -> None:
        self.arguments = kwargs
        self.when_pressed = None
        self.when_released = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeTimer:
    def __init__(self, delay: float, callback: object) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.started = False
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            assert callable(self.callback)
            self.callback()


class GpioButtonInputTests(unittest.TestCase):
    def test_short_press_is_immediate_while_contact_bounce_is_suppressed(self) -> None:
        clock = FakeMonotonic()
        presses: list[str] = []
        buttons: list[FakeButton] = []
        timers: list[FakeTimer] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        def timer_factory(delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        button_input = GpioButtonInput(
            17,
            0.05,
            lambda: presses.append("press"),
            button_factory=button_factory,
            monotonic=clock,
            timer_factory=timer_factory,
        )
        button = buttons[0]
        self.assertIsNone(button.arguments["bounce_time"])
        assert button.when_pressed is not None
        inspect.getcallargs(button.when_pressed)

        button.when_pressed()
        self.assertEqual(presses, ["press"])

        clock.now = 0.01
        button.when_pressed()
        self.assertEqual(presses, ["press"])

        clock.now = 0.051
        timers[0].fire()
        button.when_pressed()
        self.assertEqual(presses, ["press", "press"])

        button_input.close()
        self.assertTrue(button.closed)

    def test_release_edges_are_debounced_and_forwarded(self) -> None:
        clock = FakeMonotonic()
        releases: list[str] = []
        buttons: list[FakeButton] = []
        timers: list[FakeTimer] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        def timer_factory(delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        GpioButtonInput(
            17,
            0.05,
            lambda: None,
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
            timer_factory=timer_factory,
        )
        button = buttons[0]
        assert button.when_released is not None
        button.when_released()
        clock.now = 0.01
        button.when_released()
        clock.now = 0.051
        timers[0].fire()
        button.when_released()

        self.assertEqual(releases, ["release", "release"])

    def test_short_release_is_delivered_once_after_press(self) -> None:
        clock = FakeMonotonic()
        presses: list[str] = []
        releases: list[str] = []
        buttons: list[FakeButton] = []
        timers: list[FakeTimer] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        def timer_factory(delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        GpioButtonInput(
            17,
            0.05,
            lambda: presses.append("press"),
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
            timer_factory=timer_factory,
        )
        button = buttons[0]
        assert button.when_pressed is not None
        assert button.when_released is not None
        button.when_pressed()
        clock.now = 0.01
        button.when_released()
        self.assertEqual(presses, ["press"])
        self.assertEqual(releases, [])
        clock.now = 0.051
        timers[0].fire()
        self.assertEqual(releases, ["release"])

    def test_alternating_press_bounce_does_not_emit_release(self) -> None:
        clock = FakeMonotonic()
        presses: list[str] = []
        releases: list[str] = []
        buttons: list[FakeButton] = []
        timers: list[FakeTimer] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        def timer_factory(delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        GpioButtonInput(
            17,
            0.05,
            lambda: presses.append("press"),
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
            timer_factory=timer_factory,
        )
        button = buttons[0]
        assert button.when_pressed is not None
        assert button.when_released is not None
        button.when_pressed()
        clock.now = 0.01
        button.when_released()
        clock.now = 0.02
        button.when_pressed()
        clock.now = 0.051
        timers[0].fire()

        self.assertEqual(presses, ["press"])
        self.assertEqual(releases, [])

    def test_close_cancels_pending_release_and_ignores_late_timer(self) -> None:
        clock = FakeMonotonic()
        releases: list[str] = []
        buttons: list[FakeButton] = []
        timers: list[FakeTimer] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        def timer_factory(delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        button_input = GpioButtonInput(
            17,
            0.05,
            lambda: None,
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
            timer_factory=timer_factory,
        )
        button = buttons[0]
        assert button.when_pressed is not None
        assert button.when_released is not None
        button.when_pressed()
        clock.now = 0.01
        button.when_released()
        button_input.close()
        timers[0].fire()

        self.assertEqual(releases, [])
        self.assertTrue(timers[0].cancelled)
        self.assertTrue(button.closed)


if __name__ == "__main__":
    unittest.main()
