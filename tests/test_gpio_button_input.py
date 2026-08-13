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


class GpioButtonInputTests(unittest.TestCase):
    def test_short_press_is_immediate_while_contact_bounce_is_suppressed(self) -> None:
        clock = FakeMonotonic()
        presses: list[str] = []
        buttons: list[FakeButton] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        button_input = GpioButtonInput(
            17,
            0.05,
            lambda: presses.append("press"),
            button_factory=button_factory,
            monotonic=clock,
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
        button.when_pressed()
        self.assertEqual(presses, ["press", "press"])

        button_input.close()
        self.assertTrue(button.closed)

    def test_release_edges_are_debounced_and_forwarded(self) -> None:
        clock = FakeMonotonic()
        releases: list[str] = []
        buttons: list[FakeButton] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        GpioButtonInput(
            17,
            0.05,
            lambda: None,
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
        )
        button = buttons[0]
        assert button.when_released is not None
        button.when_released()
        clock.now = 0.01
        button.when_released()
        clock.now = 0.051
        button.when_released()

        self.assertEqual(releases, ["release", "release"])

    def test_press_and_release_edges_share_the_debounce_window(self) -> None:
        clock = FakeMonotonic()
        presses: list[str] = []
        releases: list[str] = []
        buttons: list[FakeButton] = []

        def button_factory(**kwargs: object) -> FakeButton:
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        GpioButtonInput(
            17,
            0.05,
            lambda: presses.append("press"),
            on_release=lambda: releases.append("release"),
            button_factory=button_factory,
            monotonic=clock,
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
        button.when_released()
        self.assertEqual(releases, ["release"])


if __name__ == "__main__":
    unittest.main()
