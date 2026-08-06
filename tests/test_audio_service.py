from __future__ import annotations

import asyncio
import tempfile
import unittest
import wave
from pathlib import Path

from takt.application.audio_service import AudioService
from takt.config import AudioConfig


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    async def __call__(
        self,
        command: tuple[str, ...],
        timeout: float,
        on_started=None,
    ) -> tuple[int, str]:
        self.commands.append(command)
        if on_started is not None:
            on_started()
        if command == ("bluetoothctl", "devices"):
            return 0, "Device AA:BB:CC:DD:EE:FF Hallenlautsprecher\n"
        if command[:2] == ("bluetoothctl", "info"):
            return 0, "Paired: yes\nConnected: no\n"
        if "connect" in command:
            return 0, "Connection successful\n"
        if command == ("pactl", "list", "short", "sinks"):
            return 0, "42 bluez_output.AA_BB_CC_DD_EE_FF.1 RUNNING\n"
        return 0, ""


class AudioServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings_path = Path(self.temporary_directory.name) / "audio.json"
        self.config = AudioConfig(settings_path=settings_path)
        self.runner = RecordingRunner()
        self.service = AudioService(
            self.config,
            runner=self.runner,
            command_finder=lambda command: f"/usr/bin/{command}",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_settings_are_persisted(self) -> None:
        self.service.update_settings(
            enabled=True,
            output="aux",
            delay_milliseconds=2_500,
            device_address=None,
            device_name=None,
        )

        restored = AudioService(
            self.config,
            runner=self.runner,
            command_finder=lambda command: f"/usr/bin/{command}",
        )
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.settings.output, "aux")
        self.assertEqual(restored.settings.delay_milliseconds, 2_500)
        self.assertEqual(restored.delay_seconds, 2.5)
        self.assertEqual(restored.clip_duration_milliseconds, 17_512)

        with self.assertRaisesRegex(ValueError, "Länge des Startsignals"):
            self.service.update_settings(
                enabled=True,
                output="aux",
                delay_milliseconds=17_513,
                device_address=None,
                device_name=None,
            )

    def test_supplied_start_signal_is_the_packaged_wav(self) -> None:
        with wave.open(str(self.service._sound_path), "rb") as recording:
            self.assertEqual(recording.getnchannels(), 2)
            self.assertEqual(recording.getframerate(), 11_025)
            self.assertAlmostEqual(
                recording.getnframes() / recording.getframerate(),
                17.51,
                places=1,
            )

    def test_bluetooth_scan_connect_and_sound(self) -> None:
        asyncio.run(self._exercise_bluetooth())

    async def _exercise_bluetooth(self) -> None:
        scan = await self.service.scan_bluetooth()
        devices = scan["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "Hallenlautsprecher")

        await self.service.connect_bluetooth("AA:BB:CC:DD:EE:FF")
        self.service.update_settings(
            enabled=True,
            output="bluetooth",
            delay_milliseconds=1_000,
            device_address="AA:BB:CC:DD:EE:FF",
            device_name="Hallenlautsprecher",
        )
        await self.service.play_start_sound()

        self.assertIn(
            ("pactl", "set-default-sink", "bluez_output.AA_BB_CC_DD_EE_FF.1"),
            self.runner.commands,
        )
        self.assertTrue(any(Path(command[0]).name == "paplay" for command in self.runner.commands))


if __name__ == "__main__":
    unittest.main()
