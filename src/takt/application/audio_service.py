from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from takt.config import AudioConfig

LOGGER = logging.getLogger(__name__)
DEVICE_PATTERN = re.compile(
    r"^Device\s+(?P<address>(?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(?P<name>.+)$",
    re.IGNORECASE,
)
ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
CommandRunner = Callable[[tuple[str, ...], float], Awaitable[tuple[int, str]]]


@dataclass(slots=True)
class AudioSettings:
    enabled: bool = False
    output: str = "off"
    delay_seconds: float = 3.0
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
        self.settings = self._load_settings(
            AudioSettings(
                enabled=config.enabled,
                output=config.output,
                delay_seconds=config.delay_seconds,
            )
        )
        self.devices: list[BluetoothDevice] = []
        self._sound_path = (
            Path(__file__).resolve().parent.parent / "assets" / "start_signal.wav"
        )

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and self.settings.output in {"aux", "bluetooth"}

    @property
    def delay_seconds(self) -> float:
        return self.settings.delay_seconds

    def payload(self) -> dict[str, object]:
        player = self._player()
        return {
            **asdict(self.settings),
            "playback_available": player is not None,
            "bluetooth_available": self._find("bluetoothctl") is not None,
            "player": Path(player).name if player else None,
            "sound": "TAKT Startsignal",
            "devices": [asdict(device) for device in self.devices],
        }

    def update_settings(
        self,
        *,
        enabled: bool,
        output: str,
        delay_seconds: float,
        device_address: str | None,
        device_name: str | None,
    ) -> dict[str, object]:
        if output not in {"off", "aux", "bluetooth"}:
            raise ValueError("Ungültiger Audio-Ausgang.")
        if not 0 <= delay_seconds <= 10:
            raise ValueError("Die Wartezeit muss zwischen 0 und 10 Sekunden liegen.")
        if output == "bluetooth" and device_address:
            self._validate_address(device_address)
        self.settings = AudioSettings(
            enabled=bool(enabled and output != "off"),
            output=output,
            delay_seconds=round(delay_seconds, 1),
            device_address=device_address or None,
            device_name=device_name or None,
        )
        self._save_settings()
        return self.payload()

    async def scan_bluetooth(self) -> dict[str, object]:
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        await self._runner(("bluetoothctl", "--timeout", "8", "scan", "on"), 12)
        code, output = await self._runner(("bluetoothctl", "devices"), 5)
        if code:
            raise RuntimeError("Bluetooth-Geräte konnten nicht gelesen werden.")
        devices: list[BluetoothDevice] = []
        for match in map(DEVICE_PATTERN.match, output.splitlines()):
            if match is None:
                continue
            address = match.group("address").upper()
            info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
            details = info if info_code == 0 else ""
            devices.append(
                BluetoothDevice(
                    address=address,
                    name=match.group("name").strip(),
                    paired="Paired: yes" in details,
                    connected="Connected: yes" in details,
                )
            )
        self.devices = devices
        return self.payload()

    async def connect_bluetooth(self, address: str) -> dict[str, object]:
        self._validate_address(address)
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        address = address.upper()
        await self._runner(("bluetoothctl", "--timeout", "25", "pair", address), 30)
        await self._runner(("bluetoothctl", "trust", address), 8)
        code, output = await self._runner(
            ("bluetoothctl", "--timeout", "20", "connect", address),
            25,
        )
        if code or "Connection successful" not in output:
            raise RuntimeError(
                "Der Lautsprecher konnte nicht verbunden werden. "
                "Bitte Pairing-Modus prüfen und erneut versuchen."
            )
        name = next(
            (device.name for device in self.devices if device.address == address),
            address,
        )
        self.settings.device_address = address
        self.settings.device_name = name
        self.settings.output = "bluetooth"
        self.settings.enabled = True
        self._save_settings()
        self.devices = [
            BluetoothDevice(
                device.address,
                device.name,
                paired=device.paired or device.address == address,
                connected=device.address == address,
            )
            for device in self.devices
        ]
        await self._select_bluetooth_sink(address)
        return self.payload()

    async def play_start_sound(self) -> None:
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
        code, output = await self._runner(command, 15)
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
            return AudioSettings(
                enabled=bool(raw.get("enabled", defaults.enabled)),
                output=output,
                delay_seconds=min(
                    max(float(raw.get("delay_seconds", defaults.delay_seconds)), 0.0),
                    10.0,
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
        code, output = await self._runner(
            ("bluetoothctl", "--timeout", "12", "connect", address),
            16,
        )
        if code or "Connection successful" not in output:
            raise RuntimeError("Der Bluetooth-Lautsprecher ist nicht verbunden.")

    async def _select_bluetooth_sink(self, address: str) -> None:
        sink = await self._find_pulse_sink(address.replace(":", "_").lower())
        if sink:
            await self._runner(("pactl", "set-default-sink", sink), 5)

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

    @staticmethod
    def _validate_address(address: str) -> None:
        if not ADDRESS_PATTERN.fullmatch(address):
            raise ValueError("Ungültige Bluetooth-Adresse.")

    @staticmethod
    async def _run_command(command: tuple[str, ...], timeout: float) -> tuple[int, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 124, "Zeitüberschreitung"
        except OSError as error:
            return 127, str(error)
        return process.returncode or 0, output.decode(errors="replace")
