from __future__ import annotations

import asyncio
import tempfile
import unittest
import wave
from pathlib import Path

from takt.application.audio_service import AudioService
from takt.config import AudioConfig


class RecordingRunner:
    def __init__(
        self,
        *,
        paired: bool = True,
        pair_is_confirmed: bool = True,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.paired = paired
        self.pair_is_confirmed = pair_is_confirmed
        self.trusted = paired
        self.connected = False

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
            return 0, (
                "Device 10:20:30:40:50:60 10-20-30-40-50-60\n"
                "Device AA:BB:CC:DD:EE:FF Hallenlautsprecher\n"
            )
        if command[:2] == ("bluetoothctl", "info"):
            return 0, (
                "Name: Hallenlautsprecher\n"
                "Alias: Hallenlautsprecher\n"
                f"Paired: {'yes' if self.paired else 'no'}\n"
                f"Trusted: {'yes' if self.trusted else 'no'}\n"
                f"Connected: {'yes' if self.connected else 'no'}\n"
            )
        if "pair" in command:
            if self.pair_is_confirmed:
                self.paired = True
                self.trusted = True
                self.connected = True
                return 0, "Pairing successful\n"
            return 0, "Attempting to pair\n"
        if "trust" in command:
            self.trusted = True
            return 0, "trust succeeded\n"
        if "connect" in command:
            self.connected = True
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
        self.assertIn(
            ("bluetoothctl", "--timeout", "20", "scan", "bredr"),
            self.runner.commands,
        )
        self.assertFalse(
            any(device["name"] == "10-20-30-40-50-60" for device in devices)
        )

        await self.service.connect_bluetooth("AA:BB:CC:DD:EE:FF")
        self.assertFalse(
            any("pair" in command for command in self.runner.commands),
            "an already paired speaker must not be paired again",
        )
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

    def test_new_bluetooth_device_is_paired_once(self) -> None:
        runner = RecordingRunner(paired=False)
        service = AudioService(
            self.config,
            runner=runner,
            command_finder=lambda command: f"/usr/bin/{command}",
        )

        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        pair_commands = [
            command
            for command in runner.commands
            if "pair" in command
        ]
        self.assertEqual(
            pair_commands,
            [
                (
                    "bluetoothctl",
                    "--timeout",
                    "30",
                    "pair",
                    "AA:BB:CC:DD:EE:FF",
                )
            ],
        )
        self.assertTrue(runner.connected)

    def test_unconfirmed_pairing_logs_bluez_diagnostics(self) -> None:
        runner = RecordingRunner(paired=False, pair_is_confirmed=False)
        service = AudioService(
            self.config,
            runner=runner,
            command_finder=lambda command: f"/usr/bin/{command}",
        )

        with self.assertLogs(
            "takt.application.audio_service",
            level="WARNING",
        ) as captured:
            with self.assertRaisesRegex(RuntimeError, "Pairing-Modus"):
                asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertIn("bluetooth_pair_not_confirmed", captured.output[0])
        self.assertIn("Attempting to pair", captured.output[0])
        self.assertIn("Paired: no", captured.output[0])


if __name__ == "__main__":
    unittest.main()
