from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from takt.application.timer_controller import TimerController
from takt.config import Config
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.runtime import WebRuntime
from tests.helpers import FakeClock


class RecordingBuzzer:
    def __init__(self) -> None:
        self.events: list[str] = []

    def signal(self, event: str) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass


class UnavailablePowerService:
    available = False
    model = ""

    def shutdown(self) -> None:
        raise AssertionError("shutdown must not be called")


class DelayedAudioService:
    enabled = True
    delay_seconds = 0.01

    def __init__(self) -> None:
        self.started = False
        self.cancelled = False

    async def play_start_sound(self, on_started=None) -> None:
        self.started = True
        await asyncio.sleep(0.01)
        if on_started is not None:
            on_started()
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def payload(self) -> dict[str, object]:
        return {
            "enabled": True,
            "output": "aux",
            "delay_milliseconds": 10,
            "clip_duration_milliseconds": 1_000,
        }

    async def close(self) -> None:
        pass


class WebRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.repository = SQLiteRunRepository(database_path)
        self.clock = FakeClock()
        self.controller = TimerController(self.clock, self.repository)
        self.buzzer = RecordingBuzzer()
        config = Config()
        config.audio.settings_path = Path(self.temporary_directory.name) / "audio.json"
        self.runtime = WebRuntime(
            self.controller,
            self.repository,
            config,
            self.buzzer,
            UnavailablePowerService(),  # type: ignore[arg-type]
            hardware_label="Mock aktiv",
            hardware_available=True,
            show_mock_button=True,
            show_mock_buzzer=True,
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_browser_actions_share_authoritative_timer_state(self) -> None:
        self.assertTrue(self.runtime.dispatch_action("primary"))
        self.clock.advance_ms(83_450)
        self.assertTrue(self.runtime.dispatch_action("primary"))
        self.assertTrue(self.runtime.dispatch_action("add_10"))

        payload = self.runtime.state_payload()
        self.assertEqual(payload["state"], "stopped")
        self.assertEqual(payload["actual"], "01:23.45")
        self.assertEqual(payload["added"], "+00:10.00")
        self.assertEqual(payload["total"], "01:33.45")
        self.assertEqual(self.buzzer.events, ["start", "stop"])

    def test_saved_run_change_requires_one_time_confirmation(self) -> None:
        self.controller.start()
        self.clock.advance_ms(40_000)
        self.controller.stop()
        saved = self.controller.save()
        assert saved is not None and saved.id is not None

        prepared = self.runtime.prepare_confirmation(
            "adjust",
            run_id=saved.id,
            delta_ms=5_000,
        )
        token = str(prepared["confirmation_id"])
        result = asyncio.run(self.runtime.confirm(token))
        self.assertTrue(result["ok"])
        updated = self.repository.get_run(saved.id)
        assert updated is not None
        self.assertEqual(updated.added_time.milliseconds, 5_000)
        self.assertEqual(updated.total_time.milliseconds, 45_000)

        with self.assertRaisesRegex(ValueError, "abgelaufen"):
            asyncio.run(self.runtime.confirm(token))

    def test_saved_confirmation_primary_press_starts_next_run(self) -> None:
        self.controller.start()
        self.clock.advance_ms(1_000)
        self.controller.stop()
        self.controller.save()
        self.assertEqual(self.controller.state, TimerState.SAVED_CONFIRMATION)

        self.assertTrue(self.runtime.primary_press())
        self.assertEqual(self.controller.state, TimerState.RUNNING)

    def test_audio_signal_delays_timer_start(self) -> None:
        asyncio.run(self._exercise_delayed_start())

    async def _exercise_delayed_start(self) -> None:
        audio = DelayedAudioService()
        self.runtime.audio_service = audio  # type: ignore[assignment]
        self.runtime.start()
        try:
            self.assertTrue(self.runtime.primary_press())
            self.assertEqual(self.controller.state, TimerState.READY)
            self.assertTrue(self.runtime.state_payload()["start_sequence"]["active"])
            await asyncio.sleep(0.005)
            self.assertEqual(self.controller.state, TimerState.READY)
            await asyncio.sleep(0.03)
            self.assertEqual(self.controller.state, TimerState.RUNNING)
            self.assertTrue(audio.started)
            self.assertTrue(self.runtime.state_payload()["sound_playing"])

            self.assertTrue(self.runtime.primary_press())
            await asyncio.sleep(0)
            self.assertEqual(self.controller.state, TimerState.STOPPED)
            self.assertTrue(audio.cancelled)
        finally:
            await self.runtime.close()


if __name__ == "__main__":
    unittest.main()
