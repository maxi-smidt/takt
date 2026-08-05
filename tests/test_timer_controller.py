from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from takt.application.timer_controller import TimerController
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository
from tests.helpers import FakeClock


class TimerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.repository = SQLiteRunRepository(database_path)
        self.clock = FakeClock()
        self.controller = TimerController(self.clock, self.repository)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_complete_save_workflow(self) -> None:
        self.assertTrue(self.controller.start())
        self.clock.advance_ms(83_459)
        self.assertTrue(self.controller.stop())
        self.assertEqual(self.controller.state, TimerState.STOPPED)
        self.assertEqual(self.controller.snapshot().actual_time.milliseconds, 83_459)

        self.controller.add_time(5_000)
        self.controller.add_time(10_000)
        saved = self.controller.save()

        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.actual_time.milliseconds, 83_459)
        self.assertEqual(saved.added_time.milliseconds, 15_000)
        self.assertEqual(saved.total_time.milliseconds, 98_459)
        self.assertEqual(self.controller.state, TimerState.SAVED_CONFIRMATION)
        self.assertTrue(self.controller.finish_confirmation())
        self.assertEqual(self.controller.state, TimerState.READY)

    def test_added_time_can_be_reduced_but_never_becomes_negative(self) -> None:
        self.controller.start()
        self.clock.advance_ms(10_000)
        self.controller.stop()
        self.controller.add_time(15_000)

        self.assertTrue(self.controller.subtract_time(5_000))
        self.assertEqual(self.controller.snapshot().added_time.milliseconds, 10_000)
        self.assertEqual(self.controller.snapshot().total_time.milliseconds, 20_000)

        self.assertTrue(self.controller.subtract_time(30_000))
        self.assertEqual(self.controller.snapshot().added_time.milliseconds, 0)
        self.assertEqual(self.controller.snapshot().total_time.milliseconds, 10_000)

    def test_invalid_commands_do_not_change_state(self) -> None:
        self.assertIsNone(self.controller.save())
        self.controller.start()
        self.assertFalse(self.controller.add_time(5_000))
        self.assertFalse(self.controller.request_discard())
        self.assertFalse(self.controller.start())
        self.assertEqual(self.controller.state, TimerState.RUNNING)

    def test_stopped_double_button_press_discards(self) -> None:
        self.controller.handle_primary_button_press()
        self.clock.advance_ms(2_000)
        self.controller.handle_primary_button_press()
        self.assertEqual(self.controller.state, TimerState.STOPPED)

        self.controller.handle_primary_button_press()
        self.clock.advance_ms(400)
        discarded = self.controller.handle_primary_button_press()
        self.assertTrue(discarded)
        self.assertEqual(self.controller.state, TimerState.READY)

    def test_slow_stopped_presses_do_not_discard(self) -> None:
        self.controller.start()
        self.clock.advance_ms(1_000)
        self.controller.stop()
        self.controller.handle_primary_button_press()
        self.clock.advance_ms(900)
        self.controller.handle_primary_button_press()
        self.assertEqual(self.controller.state, TimerState.STOPPED)

    def test_keyboard_discard_requires_confirmation(self) -> None:
        self.controller.start()
        self.clock.advance_ms(1_000)
        self.controller.stop()
        self.assertTrue(self.controller.request_discard())
        self.assertEqual(self.controller.state, TimerState.DISCARD_CONFIRMATION)
        self.assertTrue(self.controller.cancel_discard())
        self.assertEqual(self.controller.state, TimerState.STOPPED)
        self.controller.request_discard()
        self.assertTrue(self.controller.confirm_discard())
        self.assertEqual(self.controller.state, TimerState.READY)


if __name__ == "__main__":
    unittest.main()
