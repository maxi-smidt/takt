from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from takt.application.timer_controller import TimerController
from takt.config import Config
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.runtime import MaintenanceLeaseMismatch, MaintenanceUnavailable, WebRuntime
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

    def test_maintenance_lease_is_idempotent_and_blocks_every_start_source(self) -> None:
        lease = self.runtime.acquire_maintenance(
            request_id="install-job-1",
            owner="takt-agent",
            reason="Install release 0.2.0",
            ttl_seconds=30,
        )
        replay = self.runtime.acquire_maintenance(
            request_id="install-job-1",
            owner="takt-agent",
            reason="Install release 0.2.0",
            ttl_seconds=30,
        )

        self.assertTrue(lease["acquired"])
        self.assertFalse(lease["reused"])
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["lease_token"], lease["lease_token"])
        self.assertFalse(self.runtime.primary_press("web"))
        self.assertFalse(self.runtime.primary_press("gpio-taster"))
        self.assertEqual(self.controller.state, TimerState.READY)
        self.assertTrue(self.runtime.maintenance_status()["held"])

        with self.assertRaises(MaintenanceUnavailable):
            self.runtime.acquire_maintenance(
                request_id="restart-job-2",
                owner="takt-agent",
            )
        with self.assertRaises(MaintenanceLeaseMismatch):
            self.runtime.release_maintenance("not-the-lease-token")

        self.assertTrue(self.runtime.release_maintenance(str(lease["lease_token"])))
        self.assertFalse(self.runtime.release_maintenance(str(lease["lease_token"])))
        self.assertTrue(self.runtime.primary_press("gpio-taster"))
        self.assertEqual(self.controller.state, TimerState.RUNNING)
        with self.assertRaises(MaintenanceUnavailable):
            self.runtime.acquire_maintenance(
                request_id="install-job-3",
                owner="takt-agent",
            )

    def test_expired_maintenance_lease_fails_open_for_local_timing(self) -> None:
        with patch("takt.web.runtime.time.monotonic", return_value=100.0):
            self.runtime.acquire_maintenance(
                request_id="abandoned-install",
                owner="takt-agent",
                ttl_seconds=5,
            )
            self.assertFalse(self.runtime.primary_press())

        with patch("takt.web.runtime.time.monotonic", return_value=105.1):
            self.assertFalse(self.runtime.maintenance_status()["held"])
            self.assertTrue(self.runtime.primary_press())
            self.assertEqual(self.controller.state, TimerState.RUNNING)

    def test_persistent_maintenance_marker_survives_server_restart(self) -> None:
        marker = Path(self.temporary_directory.name) / "maintenance.json"
        marker.write_text(json.dumps({"expires_at": time.time() + 60}), encoding="utf-8")
        self.runtime.maintenance_marker = marker
        self.assertTrue(self.runtime.maintenance_status()["held"])
        self.assertFalse(self.runtime.primary_press("gpio-taster"))
        marker.unlink()
        self.assertFalse(self.runtime.maintenance_status()["held"])
        self.assertTrue(self.runtime.primary_press("gpio-taster"))

    def test_corrupt_old_maintenance_marker_is_recoverable(self) -> None:
        marker = Path(self.temporary_directory.name) / "maintenance.json"
        marker.write_text("{truncated", encoding="utf-8")
        old = time.time() - 31 * 60
        os.utime(marker, (old, old))
        self.runtime.maintenance_marker = marker
        self.assertFalse(self.runtime.maintenance_status()["held"])
        self.assertFalse(marker.exists())
        self.assertTrue(self.runtime.primary_press("gpio-taster"))

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
            with self.assertRaises(MaintenanceUnavailable):
                self.runtime.acquire_maintenance(
                    request_id="install-during-start-sequence",
                    owner="takt-agent",
                )
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
