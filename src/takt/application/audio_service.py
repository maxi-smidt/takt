from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from takt.config import AudioConfig

LOGGER = logging.getLogger(__name__)
DEVICE_PATTERN = re.compile(
    r"^Device\s+(?P<address>(?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(?P<name>.+)$",
    re.IGNORECASE,
)
ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
ADDRESS_LIKE_NAME_PATTERN = re.compile(
    r"^(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}$",
    re.IGNORECASE,
)


class CommandRunner(Protocol):
    async def __call__(
        self,
        command: tuple[str, ...],
        timeout: float,
        on_started: Callable[[], None] | None = None,
    ) -> tuple[int, str]: ...


@dataclass(slots=True)
class AudioSettings:
    enabled: bool = False
    output: str = "off"
    delay_milliseconds: int = 3_000
    device_address: str | None = None
    device_name: str | None = None


@dataclass(frozen=True, slots=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False


class AudioService:
    """Controls the start signal and Raspberry Pi audio output."""

    def __init__(
        self,
        config: AudioConfig,
        *,
        runner: CommandRunner | None = None,
        command_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.settings_path = config.settings_path
        self._runner = runner or self._run_command
        self._find = command_finder
        self._sound_path = (
            Path(__file__).resolve().parent.parent / "assets" / "start_signal.wav"
        )
        self.clip_duration_milliseconds = self._read_clip_duration_milliseconds()
        self.settings = self._load_settings(
            AudioSettings(
                enabled=config.enabled,
                output=config.output,
                delay_milliseconds=min(
                    config.delay_milliseconds,
                    self.clip_duration_milliseconds,
                ),
            )
        )
        self.devices: list[BluetoothDevice] = []

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and self.settings.output in {"aux", "bluetooth"}

    @property
    def delay_seconds(self) -> float:
        return self.settings.delay_milliseconds / 1000

    def payload(self) -> dict[str, object]:
        player = self._player()
        return {
            **asdict(self.settings),
            "playback_available": player is not None,
            "bluetooth_available": self._find("bluetoothctl") is not None,
            "player": Path(player).name if player else None,
            "sound": "TAKT Startsignal",
            "clip_duration_milliseconds": self.clip_duration_milliseconds,
            "devices": [asdict(device) for device in self.devices],
        }

    def update_settings(
        self,
        *,
        enabled: bool,
        output: str,
        delay_milliseconds: int,
        device_address: str | None,
        device_name: str | None,
    ) -> dict[str, object]:
        if output not in {"off", "aux", "bluetooth"}:
            raise ValueError("Ungültiger Audio-Ausgang.")
        if not 0 <= delay_milliseconds <= self.clip_duration_milliseconds:
            raise ValueError(
                "Die Wartezeit muss zwischen 0 ms und der Länge des Startsignals "
                f"({self.clip_duration_milliseconds} ms) liegen."
            )
        if output == "bluetooth" and device_address:
            self._validate_address(device_address)
        self.settings = AudioSettings(
            enabled=bool(enabled and output != "off"),
            output=output,
            delay_milliseconds=delay_milliseconds,
            device_address=device_address or None,
            device_name=device_name or None,
        )
        self._save_settings()
        return self.payload()

    async def scan_bluetooth(self) -> dict[str, object]:
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        # Most speakers advertise their A2DP service over Classic Bluetooth.
        # A Classic inquiry and name resolution can take well over four seconds,
        # so scan that transport explicitly and give it enough time to finish.
        await self._runner(("bluetoothctl", "--timeout", "20", "scan", "bredr"), 24)
        code, output = await self._runner(("bluetoothctl", "devices"), 5)
        if code:
            raise RuntimeError("Bluetooth-Geräte konnten nicht gelesen werden.")
        discovered = [
            (
                match.group("address").upper(),
                match.group("name").strip(),
            )
            for match in map(DEVICE_PATTERN.match, output.splitlines())
            if match is not None
            and not self._address_like_name(match.group("name").strip())
        ]
        details = await asyncio.gather(
            *(
                self._runner(("bluetoothctl", "info", address), 5)
                for address, _ in discovered
            )
        )
        self.devices = [
            BluetoothDevice(
                address=address,
                name=self._info_value(info, "Alias")
                or self._info_value(info, "Name")
                or name,
                paired=info_code == 0 and self._info_yes(info, "Paired"),
                connected=info_code == 0 and self._info_yes(info, "Connected"),
            )
            for (address, name), (info_code, info) in zip(
                discovered,
                details,
                strict=True,
            )
        ]
        self._sort_devices()
        return self.payload()

    async def connect_bluetooth(self, address: str) -> dict[str, object]:
        self._validate_address(address)
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        address = address.upper()
        info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
        paired = info_code == 0 and self._info_yes(info, "Paired")
        trusted = info_code == 0 and self._info_yes(info, "Trusted")
        connected = info_code == 0 and self._info_yes(info, "Connected")
        name = (
            self._info_value(info, "Alias")
            or self._info_value(info, "Name")
            or next(
                (device.name for device in self.devices if device.address == address),
                address,
            )
        )

        # Never pair an already paired device again: BlueZ may remove the
        # existing pairing before starting a new one.
        if not paired:
            # Lite has no desktop session to provide a Bluetooth agent. Speakers
            # normally use "Just Works" pairing, for which this headless agent
            # supplies the required authorization without a PIN prompt.
            pair_code, pair_output = await self._runner(
                (
                    "bluetoothctl",
                    "--agent",
                    "NoInputNoOutput",
                    "--timeout",
                    "30",
                    "pair",
                    address,
                ),
                34,
            )
            if pair_code:
                LOGGER.warning(
                    "bluetooth_pair_failed address=%s output=%s",
                    address,
                    self._single_line_output(pair_output),
                )
                raise RuntimeError(
                    "Der Lautsprecher konnte nicht gekoppelt werden. "
                    "Bitte Pairing-Modus prüfen und erneut versuchen."
                )
            info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
            paired = info_code == 0 and self._info_yes(info, "Paired")
            trusted = info_code == 0 and self._info_yes(info, "Trusted")
            connected = info_code == 0 and self._info_yes(info, "Connected")
            if not paired:
                raise RuntimeError(
                    "Der Lautsprecher konnte nicht gekoppelt werden. "
                    "Bitte Pairing-Modus prüfen und erneut versuchen."
                )

        if not trusted:
            trust_code, _ = await self._runner(("bluetoothctl", "trust", address), 6)
            if trust_code:
                raise RuntimeError("Der Lautsprecher konnte nicht gespeichert werden.")
            trusted = True

        if not connected:
            await self._runner(
                ("bluetoothctl", "--timeout", "12", "connect", address),
                16,
            )
            check_code, check_info = await self._runner(
                ("bluetoothctl", "info", address),
                5,
            )
            connected = (
                check_code == 0 and self._info_yes(check_info, "Connected")
            )
            if not connected:
                raise RuntimeError(
                    "Der Lautsprecher konnte nicht verbunden werden. "
                    "Bitte Pairing-Modus prüfen und erneut versuchen."
                )

        self.settings.device_address = address
        self.settings.device_name = name
        self.settings.output = "bluetooth"
        self.settings.enabled = True
        self._save_settings()
        self._remember_device(
            BluetoothDevice(address, name, paired=paired, connected=True)
        )
        await self._select_bluetooth_sink(address, wait_for_sink=True)
        return self.payload()

    async def play_start_sound(
        self,
        on_playback_started: Callable[[], None] | None = None,
    ) -> None:
        player = self._player()
        if player is None:
            raise RuntimeError("Kein Audioplayer ist installiert.")
        if self.settings.output == "bluetooth":
            if not self.settings.device_address:
                raise RuntimeError("Es ist kein Bluetooth-Lautsprecher ausgewählt.")
            await self._ensure_bluetooth_connected(self.settings.device_address)
            await self._select_bluetooth_sink(self.settings.device_address)
        elif self.settings.output == "aux":
            await self._select_aux_sink()
        if not self._sound_path.exists():
            raise RuntimeError("Die Startsignal-Datei wurde nicht gefunden.")
        command = self._play_command(player, self._sound_path)
        timeout = self.clip_duration_milliseconds / 1000 + 10
        code, output = await self._runner(command, timeout, on_playback_started)
        if code:
            LOGGER.warning("start_sound_failed command=%s output=%s", command[0], output)
            raise RuntimeError("Das Startsignal konnte nicht abgespielt werden.")

    async def test_sound(self) -> dict[str, object]:
        await self.play_start_sound()
        return self.payload()

    def _load_settings(self, defaults: AudioSettings) -> AudioSettings:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            output = str(raw.get("output", defaults.output))
            if output not in {"off", "aux", "bluetooth"}:
                output = "off"
            if "delay_milliseconds" in raw:
                delay_milliseconds = int(raw["delay_milliseconds"])
            else:
                delay_milliseconds = round(
                    float(raw.get("delay_seconds", defaults.delay_milliseconds / 1000))
                    * 1000
                )
            return AudioSettings(
                enabled=bool(raw.get("enabled", defaults.enabled)),
                output=output,
                delay_milliseconds=min(
                    max(delay_milliseconds, 0),
                    self.clip_duration_milliseconds,
                ),
                device_address=raw.get("device_address") or None,
                device_name=raw.get("device_name") or None,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return defaults

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self.settings), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.settings_path)

    def _read_clip_duration_milliseconds(self) -> int:
        try:
            with wave.open(str(self._sound_path), "rb") as recording:
                return round(recording.getnframes() / recording.getframerate() * 1000)
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            LOGGER.exception("start_sound_metadata_failed")
            return 0

    def _player(self) -> str | None:
        for command in ("paplay", "pw-play", "afplay", "aplay"):
            path = self._find(command)
            if path:
                return path
        return None

    def _play_command(self, player: str, sound_path: Path) -> tuple[str, ...]:
        name = Path(player).name
        if name == "aplay":
            return (player, "-q", str(sound_path))
        return (player, str(sound_path))

    async def _ensure_bluetooth_connected(self, address: str) -> None:
        code, info = await self._runner(("bluetoothctl", "info", address), 5)
        if code == 0 and "Connected: yes" in info:
            return
        await self._runner(
            ("bluetoothctl", "--timeout", "12", "connect", address),
            16,
        )
        check_code, check_info = await self._runner(
            ("bluetoothctl", "info", address),
            5,
        )
        if check_code or not self._info_yes(check_info, "Connected"):
            raise RuntimeError("Der Bluetooth-Lautsprecher ist nicht verbunden.")

    async def _select_bluetooth_sink(
        self,
        address: str,
        *,
        wait_for_sink: bool = False,
    ) -> None:
        attempts = 8 if wait_for_sink else 1
        for attempt in range(attempts):
            sink = await self._find_pulse_sink(address.replace(":", "_").lower())
            if sink:
                await self._runner(("pactl", "set-default-sink", sink), 5)
                return
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25)

    async def _select_aux_sink(self) -> None:
        sink = await self._find_pulse_sink(
            "analog-stereo",
            "headphones",
            "bcm2835",
        )
        if sink:
            await self._runner(("pactl", "set-default-sink", sink), 5)

    async def _find_pulse_sink(self, *needles: str) -> str | None:
        if self._find("pactl") is None:
            return None
        code, output = await self._runner(("pactl", "list", "short", "sinks"), 5)
        if code:
            return None
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            sink = fields[1]
            lowered = sink.lower()
            if any(needle.lower() in lowered for needle in needles):
                return sink
        return None

    def _remember_device(self, selected: BluetoothDevice) -> None:
        self.devices = [
            selected,
            *(
                device
                for device in self.devices
                if device.address != selected.address
            ),
        ]
        self._sort_devices()

    def _sort_devices(self) -> None:
        selected = self.settings.device_address
        self.devices.sort(
            key=lambda device: (
                device.address != selected,
                not device.connected,
                not device.paired,
                device.name.casefold(),
            )
        )

    @staticmethod
    def _info_value(info: str, field: str) -> str | None:
        prefix = f"{field}:"
        for line in info.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                value = stripped.removeprefix(prefix).strip()
                return value or None
        return None

    @classmethod
    def _info_yes(cls, info: str, field: str) -> bool:
        return cls._info_value(info, field) == "yes"

    @staticmethod
    def _validate_address(address: str) -> None:
        if not ADDRESS_PATTERN.fullmatch(address):
            raise ValueError("Ungültige Bluetooth-Adresse.")

    @staticmethod
    def _address_like_name(name: str) -> bool:
        """Return whether BlueZ has only exposed an address as the device name."""
        return ADDRESS_LIKE_NAME_PATTERN.fullmatch(name) is not None

    @staticmethod
    def _single_line_output(output: str) -> str:
        """Keep command diagnostics useful without writing multiline log records."""
        return " ".join(output.split())[-1_000:]

    @staticmethod
    async def _run_command(
        command: tuple[str, ...],
        timeout: float,
        on_started: Callable[[], None] | None = None,
    ) -> tuple[int, str]:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            if on_started is not None:
                on_started()
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.CancelledError:
            if process is not None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except TimeoutError:
            assert process is not None
            process.kill()
            await process.wait()
            return 124, "Zeitüberschreitung"
        except OSError as error:
            return 127, str(error)
        return process.returncode or 0, output.decode(errors="replace")
