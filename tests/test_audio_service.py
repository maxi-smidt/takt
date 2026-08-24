from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
import wave
from pathlib import Path

from takt.application.audio_service import AudioService
from takt.config import AudioConfig


async def no_delay(seconds: float) -> None:
    """Fast stand-in for asyncio.sleep so retry/backoff tests stay quick."""


async def drain_discovery(service: AudioService) -> None:
    """Wait for a background discovery task started by the service to finish."""
    task = service._discovery_task
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await task


class RecordingRunner:
    """Simple always-succeeds fake used by the happy-path tests below."""

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
        if command == ("bluetoothctl", "show"):
            return 0, "Powered: yes\n"
        if command == ("bluetoothctl", "power", "on"):
            return 0, "Changing power on succeeded\n"
        if command[:2] == ("rfkill", "unblock"):
            return 0, ""
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
                "ServicesResolved: yes\n"
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


class ScriptedRunner:
    """Configurable CommandRunner fake used to script Bluetooth failure scenarios."""

    def __init__(
        self,
        *,
        address: str = "AA:BB:CC:DD:EE:FF",
        name: str = "Hallenlautsprecher",
        known: bool = True,
        discover_after_polls: int = 0,
        paired: bool = True,
        trusted: bool = True,
        connected: bool = False,
        powered: bool = True,
        connect_failures: int = 0,
        connect_failure_output: str = "br-connection-page-timeout\n",
        auth_failure_on_connect: bool = False,
        auth_failure_on_pair: bool = False,
        sink_polls_before_ready: int = 0,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.address = address
        self.name = name
        self.known = known
        self.discover_after_polls = discover_after_polls
        self.paired = paired
        self.trusted = trusted
        self.connected = connected
        self.powered = powered
        self.connect_failures = connect_failures
        self.connect_failure_output = connect_failure_output
        self.auth_failure_on_connect = auth_failure_on_connect
        self.auth_failure_on_pair = auth_failure_on_pair
        self.sink_name = f"bluez_output.{address.replace(':', '_')}.1"
        self.sink_polls_before_ready = sink_polls_before_ready
        self.removed = False
        self._info_polls = 0
        self._sink_polls = 0

    def _info_output(self) -> tuple[int, str]:
        if not self.known:
            self._info_polls += 1
            if self._info_polls > self.discover_after_polls:
                self.known = True
            else:
                return 1, "Device AA:BB:CC:DD:EE:FF not available\n"
        return 0, (
            f"Name: {self.name}\n"
            f"Alias: {self.name}\n"
            f"Paired: {'yes' if self.paired else 'no'}\n"
            f"Trusted: {'yes' if self.trusted else 'no'}\n"
            f"Connected: {'yes' if self.connected else 'no'}\n"
            "ServicesResolved: yes\n"
        )

    async def __call__(
        self,
        command: tuple[str, ...],
        timeout: float,
        on_started=None,
    ) -> tuple[int, str]:
        self.commands.append(command)
        if on_started is not None:
            on_started()
        if command == ("bluetoothctl", "show"):
            return 0, f"Powered: {'yes' if self.powered else 'no'}\n"
        if command == ("bluetoothctl", "power", "on"):
            self.powered = True
            return 0, "Changing power on succeeded\n"
        if command[:2] == ("rfkill", "unblock"):
            return 0, ""
        if command == ("bluetoothctl", "devices"):
            if not self.known:
                return 0, ""
            return 0, f"Device {self.address} {self.name}\n"
        if command[:2] == ("bluetoothctl", "info"):
            return self._info_output()
        if command[:2] == ("bluetoothctl", "remove"):
            self.removed = True
            self.paired = False
            self.trusted = False
            self.connected = False
            # A remove + re-pair cycle recovers a pairing a JBL speaker dropped.
            self.auth_failure_on_connect = False
            self.auth_failure_on_pair = False
            return 0, "Device has been removed\n"
        if command[:2] == ("bluetoothctl", "disconnect"):
            self.connected = False
            return 0, "Successful disconnected\n"
        if "pair" in command:
            if self.auth_failure_on_pair:
                return 1, "org.bluez.Error.AuthenticationFailed\n"
            self.paired = True
            self.trusted = True
            self.known = True
            return 0, "Pairing successful\n"
        if "trust" in command:
            self.trusted = True
            return 0, "trust succeeded\n"
        if "connect" in command:
            if self.auth_failure_on_connect:
                return 1, "org.bluez.Error.AuthenticationFailed\n"
            if self.connect_failures > 0:
                self.connect_failures -= 1
                return 1, self.connect_failure_output
            self.connected = True
            return 0, "Connection successful\n"
        if command == ("pactl", "list", "short", "sinks"):
            self._sink_polls += 1
            if self._sink_polls > self.sink_polls_before_ready:
                return 0, f"42 {self.sink_name} RUNNING\n"
            return 0, ""
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
            sleep=no_delay,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _make_service(self, runner, **kwargs) -> AudioService:
        kwargs.setdefault("command_finder", lambda command: f"/usr/bin/{command}")
        kwargs.setdefault("sleep", no_delay)
        return AudioService(self.config, runner=runner, **kwargs)

    def test_settings_are_persisted(self) -> None:
        self.service.update_settings(
            enabled=True,
            output="aux",
            delay_milliseconds=2_500,
            device_address=None,
            device_name=None,
            run_signals_enabled=False,
        )

        restored = AudioService(
            self.config,
            runner=self.runner,
            command_finder=lambda command: f"/usr/bin/{command}",
        )
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.settings.output, "aux")
        self.assertEqual(restored.settings.delay_milliseconds, 2_500)
        self.assertFalse(restored.settings.run_signals_enabled)
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

    def test_supplied_run_signals_are_packaged_wavs(self) -> None:
        expected_durations = {
            "best_run_signal": 2.03,
            "top_five_run_signal": 2.80,
            "daily_best_run_signal": 1.62,
            "worst_ten_run_signal": 3.30,
        }
        self.assertEqual(set(self.service._run_signal_paths), set(expected_durations))
        for name, expected_duration in expected_durations.items():
            with self.subTest(name=name), wave.open(
                str(self.service._run_signal_paths[name]),
                "rb",
            ) as recording:
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(recording.getsampwidth(), 2)
                self.assertEqual(recording.getframerate(), 44_100)
                self.assertAlmostEqual(
                    recording.getnframes() / recording.getframerate(),
                    expected_duration,
                    places=1,
                )

    def test_run_signal_uses_the_named_wav(self) -> None:
        signal_path = Path(self.temporary_directory.name) / "daily_best_run_signal.wav"
        with wave.open(str(signal_path), "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(8_000)
            recording.writeframes(bytes(1_600))
        self.service._run_signal_paths["daily_best_run_signal"] = signal_path

        asyncio.run(self.service.play_run_signal("daily_best_run_signal"))

        play_commands = [
            command for command in self.runner.commands if Path(command[0]).name == "paplay"
        ]
        self.assertEqual(Path(play_commands[-1][-1]), signal_path)

    def test_unknown_run_signal_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unbekanntes Ergebnissignal"):
            asyncio.run(self.service.play_run_signal("unknown"))

    def test_bluetooth_scan_connect_and_sound(self) -> None:
        asyncio.run(self._exercise_bluetooth())

    async def _exercise_bluetooth(self) -> None:
        scan = await self.service.scan_bluetooth()
        await drain_discovery(self.service)
        self.assertTrue(scan["scanning"])
        devices = self.service.payload()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "Hallenlautsprecher")
        self.assertIn(
            ("bluetoothctl", "--timeout", "15", "scan", "bredr"),
            self.runner.commands,
        )
        self.assertFalse(any(device["name"] == "10-20-30-40-50-60" for device in devices))
        self.assertFalse(self.service.payload()["scanning"])

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
        play_commands = [
            command for command in self.runner.commands if Path(command[0]).name == "paplay"
        ]
        self.assertTrue(play_commands)
        self.assertIn("--device=bluez_output.AA_BB_CC_DD_EE_FF.1", play_commands[-1])

    def test_new_bluetooth_device_is_paired_once(self) -> None:
        runner = RecordingRunner(paired=False)
        service = self._make_service(runner)

        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        pair_commands = [command for command in runner.commands if "pair" in command]
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
        service = self._make_service(runner)

        with self.assertLogs(
            "takt.application.audio_service",
            level="WARNING",
        ) as captured, self.assertRaisesRegex(RuntimeError, "Pairing-Modus"):
            asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertTrue(
            any(
                "bluetooth_pair_attempt_failed" in line and "Attempting to pair" in line
                for line in captured.output
            )
        )
        self.assertTrue(any("bluetooth_pair_failed" in line for line in captured.output))

    # -- Bluetooth hardening plan coverage ---------------------------------

    def test_adapter_is_powered_on_when_unpowered(self) -> None:
        runner = ScriptedRunner(powered=False, connected=True)
        service = self._make_service(runner)

        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertIn(("bluetoothctl", "power", "on"), runner.commands)

    def test_connect_retries_after_page_timeout_and_then_succeeds(self) -> None:
        runner = ScriptedRunner(connect_failures=2)
        service = self._make_service(runner)

        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        connect_commands = [
            command
            for command in runner.commands
            if command[0] == "bluetoothctl" and "connect" in command
        ]
        self.assertEqual(len(connect_commands), 3)
        self.assertTrue(runner.connected)

    def test_connect_exhausting_all_attempts_raises_translated_error(self) -> None:
        runner = ScriptedRunner(connect_failures=99)
        service = self._make_service(runner)

        with (
            self.assertLogs("takt.application.audio_service", level="WARNING") as captured,
            self.assertRaisesRegex(RuntimeError, "Reichweite"),
        ):
            asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertTrue(any("bluetooth_connect_failed" in line for line in captured.output))
        self.assertTrue(any("br-connection-page-timeout" in line for line in captured.output))

    def test_auth_failure_on_connect_triggers_remove_and_repair_recovery(self) -> None:
        runner = ScriptedRunner(
            paired=True,
            trusted=True,
            connected=False,
            auth_failure_on_connect=True,
        )
        service = self._make_service(runner)

        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertIn(("bluetoothctl", "remove", "AA:BB:CC:DD:EE:FF"), runner.commands)
        pair_commands = [command for command in runner.commands if "pair" in command]
        self.assertTrue(pair_commands, "recovery must re-pair after removing the device")
        self.assertTrue(runner.connected)

    def test_device_missing_from_cache_is_rediscovered(self) -> None:
        runner = ScriptedRunner(
            known=False,
            discover_after_polls=1,
            paired=False,
            connected=False,
        )
        service = self._make_service(runner)

        result = asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertTrue(runner.connected)
        self.assertEqual(result["device_address"], "AA:BB:CC:DD:EE:FF")
        info_calls = [
            command for command in runner.commands if command[:2] == ("bluetoothctl", "info")
        ]
        self.assertGreaterEqual(len(info_calls), 3)

    def test_sink_appears_after_polling_and_playback_targets_it(self) -> None:
        runner = ScriptedRunner(connected=True, sink_polls_before_ready=3)
        service = self._make_service(runner)
        service.update_settings(
            enabled=True,
            output="bluetooth",
            delay_milliseconds=0,
            device_address="AA:BB:CC:DD:EE:FF",
            device_name="Hallenlautsprecher",
        )

        asyncio.run(service.play_start_sound())

        play_commands = [
            command for command in runner.commands if Path(command[0]).name == "paplay"
        ]
        self.assertTrue(play_commands)
        self.assertIn(f"--device={runner.sink_name}", play_commands[-1])

    def test_scan_merges_and_keeps_previously_known_device(self) -> None:
        from takt.application.audio_service import BluetoothDevice

        runner = ScriptedRunner(address="AA:BB:CC:DD:EE:FF", name="Hallenlautsprecher")
        service = self._make_service(runner)
        service.devices = [
            BluetoothDevice(
                address="11:22:33:44:55:66",
                name="Altgerät",
                paired=True,
                connected=False,
            )
        ]

        asyncio.run(self._scan_and_drain(service))

        addresses = {device.address for device in service.devices}
        self.assertIn("11:22:33:44:55:66", addresses)
        self.assertIn("AA:BB:CC:DD:EE:FF", addresses)

    @staticmethod
    async def _scan_and_drain(service: AudioService) -> None:
        await service.scan_bluetooth()
        await drain_discovery(service)

    def test_second_scan_while_scanning_does_not_start_a_second_discovery(self) -> None:
        asyncio.run(self._exercise_overlapping_scan())

    async def _exercise_overlapping_scan(self) -> None:
        runner = ScriptedRunner()
        gate = asyncio.Event()

        async def blocking_scan_process() -> tuple[int, str]:
            await gate.wait()
            return 0, ""

        service = self._make_service(runner, scan_process_runner=blocking_scan_process)

        first = await service.scan_bluetooth()
        self.assertTrue(first["scanning"])
        task_after_first_scan = service._discovery_task
        self.assertIsNotNone(task_after_first_scan)

        second = await service.scan_bluetooth()
        self.assertTrue(second["scanning"])
        self.assertIs(service._discovery_task, task_after_first_scan)

        gate.set()
        await drain_discovery(service)
        self.assertFalse(service.payload()["scanning"])

    def test_forget_removes_device_and_clears_selected_settings(self) -> None:
        runner = ScriptedRunner(connected=True)
        service = self._make_service(runner)
        asyncio.run(service.connect_bluetooth("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(service.settings.device_address, "AA:BB:CC:DD:EE:FF")

        result = asyncio.run(service.forget_bluetooth("AA:BB:CC:DD:EE:FF"))

        self.assertIn(("bluetoothctl", "remove", "AA:BB:CC:DD:EE:FF"), runner.commands)
        self.assertIsNone(service.settings.device_address)
        self.assertIsNone(service.settings.device_name)
        self.assertFalse(
            any(device["address"] == "AA:BB:CC:DD:EE:FF" for device in result["devices"])
        )

    def test_error_translation_for_page_timeout(self) -> None:
        message = self.service._translate_error("br-connection-page-timeout", context="connect")
        self.assertIn("Reichweite", message)
        self.assertIn("br-connection-page-timeout", message)


if __name__ == "__main__":
    unittest.main()
