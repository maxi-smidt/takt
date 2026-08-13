from __future__ import annotations

import unittest

from takt.input.button_gestures import (
    ButtonGesture,
    ButtonGestureRecognizer,
    GestureMode,
)


class ButtonGestureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mode = GestureMode.STOPPED
        self.recognizer = ButtonGestureRecognizer(
            lambda: self.mode,
            double_press_seconds=0.6,
            long_press_seconds=1.0,
        )

    def test_immediate_mode_emits_short_on_press_and_not_release(self) -> None:
        self.mode = GestureMode.IMMEDIATE

        self.assertEqual(
            [event.gesture for event in self.recognizer.press(now=0.0)],
            [ButtonGesture.SHORT],
        )
        self.assertEqual(self.recognizer.release(now=0.2), ())

    def test_single_stopped_press_expires_without_an_action(self) -> None:
        self.assertEqual(self.recognizer.press(now=0.0), ())
        self.assertEqual(self.recognizer.release(now=0.1), ())
        self.assertEqual(self.recognizer.next_deadline, 0.6)
        self.assertEqual(self.recognizer.advance(now=0.6), ())
        self.assertIsNone(self.recognizer.next_deadline)

    def test_double_press_at_boundary_discards_on_second_release(self) -> None:
        self.recognizer.press(now=0.0)
        self.recognizer.release(now=0.1)
        self.recognizer.press(now=0.6)
        events = self.recognizer.release(now=0.7)

        self.assertEqual([event.gesture for event in events], [ButtonGesture.DOUBLE])

    def test_second_press_after_window_starts_a_new_single_gesture(self) -> None:
        self.recognizer.press(now=0.0)
        self.recognizer.release(now=0.1)
        self.assertEqual(self.recognizer.press(now=0.601), ())
        self.recognizer.release(now=0.7)
        self.assertEqual(self.recognizer.advance(now=1.601), ())

    def test_long_press_is_emitted_at_threshold_and_release_is_consumed(self) -> None:
        self.recognizer.press(now=0.0)
        self.assertEqual(
            [event.gesture for event in self.recognizer.advance(now=1.0)],
            [ButtonGesture.LONG],
        )
        self.assertEqual(self.recognizer.release(now=1.2), ())

    def test_release_at_long_threshold_still_emits_long(self) -> None:
        self.recognizer.press(now=0.0)
        events = self.recognizer.release(now=1.0)

        self.assertEqual([event.gesture for event in events], [ButtonGesture.LONG])

    def test_second_press_held_past_double_window_is_still_double_if_short(self) -> None:
        self.recognizer.press(now=0.0)
        self.recognizer.release(now=0.1)
        self.recognizer.press(now=0.5)
        events = self.recognizer.release(now=0.9)

        self.assertEqual([event.gesture for event in events], [ButtonGesture.DOUBLE])

    def test_long_press_never_emits_double_after_release(self) -> None:
        self.recognizer.press(now=0.0)
        self.assertEqual(self.recognizer.advance(now=1.0)[0].gesture, ButtonGesture.LONG)
        self.recognizer.release(now=1.1)
        self.assertEqual(self.recognizer.press(now=1.2), ())

    def test_ignored_gesture_consumes_press_and_release(self) -> None:
        self.mode = GestureMode.IGNORE
        self.assertEqual(self.recognizer.press(now=0.0), ())
        self.assertEqual(self.recognizer.release(now=2.0), ())


if __name__ == "__main__":
    unittest.main()
