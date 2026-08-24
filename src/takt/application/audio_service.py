from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import wave
from collections.abc import Awaitable, Callable
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
    run_signals_enabled: bool = True


@dataclass(frozen=True, slots=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False


class AudioService:
    """Controls the start signal and Raspberry Pi audio output."""

    # BlueZ drops "temporary" (unpaired) discovery results roughly 30 s after
    # discovery ends, so a background scan is kept running while devices
    # stream into the UI instead of blocking on one long one-shot call.
    SCAN_DURATION_SECONDS = 15.0
    SCAN_TIMEOUT_SECONDS = 17.0
    POLL_INTERVAL_SECONDS = 2.0
    REDISCOVER_TIMEOUT_SECONDS = 8.0
    REDISCOVER_POLL_INTERVAL_SECONDS = 1.0
    PAIR_TIMEOUT_SECONDS = 30.0
    PAIR_OUTER_TIMEOUT_SECONDS = 34.0
    CONNECT_ATTEMPT_TIMEOUT_SECONDS = 10.0
    CONNECT_OUTER_TIMEOUT_SECONDS = 14.0
    CONNECT_MAX_ATTEMPTS = 3
    ENSURE_CONNECT_MAX_ATTEMPTS = 2
    CONNECT_BACKOFF_SECONDS = 1.5
    SERVICES_RESOLVED_TIMEOUT_SECONDS = 5.0
    SERVICES_RESOLVED_POLL_INTERVAL_SECONDS = 1.0
    SINK_WAIT_TIMEOUT_SECONDS = 10.0
    SINK_POLL_INTERVAL_SECONDS = 0.25

    # Substrings (matched case-insensitively) of bluetoothctl/BlueZ output that
    # map to an actionable German message instead of a generic failure.
    _ERROR_TRANSLATIONS: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("page-timeout", "not available", "no route to host", "host is down"),
            (
                "Der Lautsprecher konnte nicht erreicht werden. Bitte prüfen, ob er "
                "eingeschaltet und in Reichweite ist, und in den Pairing-Modus versetzen."
            ),
        ),
        (
            ("authenticationfailed", "auth", "rejected"),
            (
                "Die Kopplung wurde vom Lautsprecher verweigert. Bitte den Lautsprecher "
                "in den Pairing-Modus versetzen, TAKT koppelt dann automatisch neu."
            ),
        ),
    )
    _AUTH_FAILURE_NEEDLES = ("authenticationfailed", "auth", "rejected")

    def __init__(
        self,
        config: AudioConfig,
        *,
        runner: CommandRunner | None = None,
        command_finder: Callable[[str], str | None] = shutil.which,
        scan_process_runner: Callable[[], Awaitable[tuple[int, str]]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings_path = config.settings_path
        self._runner = runner or self._run_command
        self._find = command_finder
        self._scan_process_runner = scan_process_runner or self._default_scan_process
        self._sleep = sleep
        self._sound_path = Path(__file__).resolve().parent.parent / "assets" / "start_signal.wav"
        self._run_signal_paths = {
            name: self._sound_path.with_name(f"{name}.wav")
            for name in (
                "best_run_signal",
                "top_five_run_signal",
                "daily_best_run_signal",
                "worst_ten_run_signal",
            )
        }
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
        self.scanning = False
        self.on_devices_changed: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._discovery_task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and self.settings.output in {"aux", "bluetooth"}

    @property
    def delay_seconds(self) -> float:
        return self.settings.delay_milliseconds / 1000

    @property
    def run_signals_enabled(self) -> bool:
        return self.enabled and self.settings.run_signals_enabled

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
            "scanning": self.scanning,
        }

    def update_settings(
        self,
        *,
        enabled: bool,
        output: str,
        delay_milliseconds: int,
        device_address: str | None,
        device_name: str | None,
        run_signals_enabled: bool | None = None,
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
            run_signals_enabled=(
                self.settings.run_signals_enabled
                if run_signals_enabled is None
                else bool(run_signals_enabled)
            ),
        )
        self._save_settings()
        return self.payload()

    async def scan_bluetooth(self) -> dict[str, object]:
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        async with self._lock:
            await self._ensure_adapter_ready()
            if not self.scanning:
                self.scanning = True
                first_poll_done = asyncio.Event()
                self._discovery_task = asyncio.create_task(self._run_discovery(first_poll_done))
                await first_poll_done.wait()
        return self.payload()

    async def connect_bluetooth(self, address: str) -> dict[str, object]:
        self._validate_address(address)
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        address = address.upper()
        async with self._lock:
            await self._ensure_adapter_ready()
            await self._cancel_discovery()
            await self._connect_device(address)
        return self.payload()

    async def forget_bluetooth(self, address: str) -> dict[str, object]:
        self._validate_address(address)
        if self._find("bluetoothctl") is None:
            raise RuntimeError("Bluetooth-Verwaltung ist auf diesem Gerät nicht verfügbar.")
        address = address.upper()
        async with self._lock:
            await self._cancel_discovery()
            await self._runner(("bluetoothctl", "disconnect", address), 8)
            remove_code, remove_output = await self._runner(("bluetoothctl", "remove", address), 8)
            if remove_code:
                LOGGER.warning(
                    "bluetooth_remove_failed address=%s output=%s",
                    address,
                    self._single_line_output(remove_output),
                )
            self.devices = [device for device in self.devices if device.address != address]
            if self.settings.device_address == address:
                self.settings.device_address = None
                self.settings.device_name = None
                self._save_settings()
        return self.payload()

    async def play_start_sound(
        self,
        on_playback_started: Callable[[], None] | None = None,
    ) -> None:
        await self._play_sound(
            self._sound_path,
            duration_milliseconds=self.clip_duration_milliseconds,
            missing_message="Die Startsignal-Datei wurde nicht gefunden.",
            failure_message="Das Startsignal konnte nicht abgespielt werden.",
            failure_event="start_sound_failed",
            on_playback_started=on_playback_started,
        )

    async def play_run_signal(self, signal: str) -> None:
        sound_path = self._run_signal_paths.get(signal)
        if sound_path is None:
            raise ValueError("Unbekanntes Ergebnissignal.")
        if not sound_path.exists():
            raise RuntimeError(f"Die Datei für {signal} wurde nicht gefunden.")
        await self._play_sound(
            sound_path,
            duration_milliseconds=self._read_sound_duration_milliseconds(
                sound_path,
                metadata_error_event=f"{signal}_metadata_failed",
            ),
            missing_message=f"Die Datei für {signal} wurde nicht gefunden.",
            failure_message="Das Ergebnissignal konnte nicht abgespielt werden.",
            failure_event=f"{signal}_failed",
        )

    async def _play_sound(
        self,
        sound_path: Path,
        *,
        duration_milliseconds: int,
        missing_message: str,
        failure_message: str,
        failure_event: str,
        on_playback_started: Callable[[], None] | None = None,
    ) -> None:
        player = self._player()
        if player is None:
            raise RuntimeError("Kein Audioplayer ist installiert.")
        if not sound_path.exists():
            raise RuntimeError(missing_message)
        sink: str | None = None
        if self.settings.output == "bluetooth":
            if not self.settings.device_address:
                raise RuntimeError("Es ist kein Bluetooth-Lautsprecher ausgewählt.")
            await self._ensure_bluetooth_connected(self.settings.device_address)
            sink = await self._select_bluetooth_sink(self.settings.device_address)
            if sink is None:
                raise RuntimeError(
                    "Der Bluetooth-Lautsprecher ist verbunden, aber die Audioausgabe "
                    "wurde nicht gefunden. Bitte erneut versuchen."
                )
        elif self.settings.output == "aux":
            sink = await self._select_aux_sink()
        command = self._play_command(player, sound_path, sink)
        timeout = duration_milliseconds / 1000 + 10
        code, output = await self._runner(command, timeout, on_playback_started)
        if code:
            LOGGER.warning("%s command=%s output=%s", failure_event, command[0], output)
            raise RuntimeError(failure_message)

    async def test_sound(self) -> dict[str, object]:
        await self.play_start_sound()
        return self.payload()

    async def close(self) -> None:
        await self._cancel_discovery()

    # -- Adapter ---------------------------------------------------------

    async def _ensure_adapter_ready(self) -> None:
        if self._find("rfkill") is not None:
            await self._runner(("rfkill", "unblock", "bluetooth"), 5)
        code, output = await self._runner(("bluetoothctl", "show"), 5)
        if code:
            LOGGER.warning("bluetooth_adapter_missing output=%s", self._single_line_output(output))
            raise RuntimeError(
                "Bluetooth-Adapter ist nicht verfügbar. Bitte Bluetooth-Hardware und "
                "Raspberry Pi prüfen."
            )
        if "Powered: yes" not in output:
            await self._runner(("bluetoothctl", "power", "on"), 8)
            code, output = await self._runner(("bluetoothctl", "show"), 5)
            if code or "Powered: yes" not in output:
                LOGGER.warning(
                    "bluetooth_adapter_not_powered output=%s",
                    self._single_line_output(output),
                )
                raise RuntimeError(
                    "Bluetooth-Adapter konnte nicht eingeschaltet werden. Bitte "
                    "Raspberry Pi neu starten oder Bluetooth-Hardware prüfen."
                )

    # -- Background discovery ---------------------------------------------

    async def _default_scan_process(self) -> tuple[int, str]:
        return await self._runner(
            (
                "bluetoothctl",
                "--timeout",
                str(int(self.SCAN_DURATION_SECONDS)),
                "scan",
                "bredr",
            ),
            self.SCAN_TIMEOUT_SECONDS,
        )

    async def _run_discovery(self, first_poll_done: asyncio.Event) -> None:
        scan_task = asyncio.ensure_future(self._scan_process_runner())
        try:
            while not scan_task.done():
                await self._poll_devices()
                first_poll_done.set()
                self._notify_devices_changed()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(scan_task), timeout=self.POLL_INTERVAL_SECONDS
                    )
            await self._poll_devices()
        except asyncio.CancelledError:
            scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scan_task
            raise
        finally:
            first_poll_done.set()
            self.scanning = False
            self._discovery_task = None
            self._notify_devices_changed()

    async def _cancel_discovery(self) -> None:
        task = self._discovery_task
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _poll_devices(self) -> None:
        code, output = await self._runner(("bluetoothctl", "devices"), 5)
        if code:
            return
        discovered = [
            (match.group("address").upper(), match.group("name").strip())
            for match in map(DEVICE_PATTERN.match, output.splitlines())
            if match is not None and not self._address_like_name(match.group("name").strip())
        ]
        details = await asyncio.gather(
            *(self._runner(("bluetoothctl", "info", address), 5) for address, _ in discovered)
        )
        for (address, name), (info_code, info) in zip(discovered, details, strict=True):
            if info_code != 0:
                # Keep whatever we already know about this device rather than
                # overwriting it with an unknown/failed lookup.
                continue
            self._merge_device(
                BluetoothDevice(
                    address=address,
                    name=self._info_value(info, "Alias") or self._info_value(info, "Name") or name,
                    paired=self._info_yes(info, "Paired"),
                    connected=self._info_yes(info, "Connected"),
                )
            )
        self._sort_devices()

    def _notify_devices_changed(self) -> None:
        if self.on_devices_changed is not None:
            self.on_devices_changed()

    async def _rediscover_device(self, address: str) -> bool:
        """Run a short targeted discovery burst until `address` appears."""
        scan_task = asyncio.ensure_future(self._scan_process_runner())
        try:
            elapsed = 0.0
            while True:
                info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
                if self._device_known(info_code, info):
                    return True
                if elapsed >= self.REDISCOVER_TIMEOUT_SECONDS:
                    return False
                await self._sleep(self.REDISCOVER_POLL_INTERVAL_SECONDS)
                elapsed += self.REDISCOVER_POLL_INTERVAL_SECONDS
        finally:
            scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scan_task

    # -- Connect ------------------------------------------------------------

    async def _connect_device(self, address: str) -> None:
        info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
        if not self._device_known(info_code, info):
            if not await self._rediscover_device(address):
                LOGGER.warning("bluetooth_device_not_found address=%s", address)
                raise RuntimeError(
                    "Der Lautsprecher wurde nicht gefunden. Bitte einschalten und in "
                    "den Pairing-Modus versetzen, dann erneut verbinden."
                )
            info_code, info = await self._runner(("bluetoothctl", "info", address), 5)

        name = (
            self._info_value(info, "Alias")
            or self._info_value(info, "Name")
            or next(
                (device.name for device in self.devices if device.address == address),
                address,
            )
        )

        # A speaker that has been paired with another phone in the meantime
        # (JBL speakers in particular) can leave TAKT with a stale pairing.
        # Auth-class failures are recovered at most once via remove + re-pair.
        recovered = False
        while True:
            paired = info_code == 0 and self._info_yes(info, "Paired")
            if not paired:
                # Never re-pair an already paired device: BlueZ may drop the
                # existing pairing before starting a new one.
                paired, pair_output = await self._pair_with_retry(address)
                if not paired:
                    if not recovered and self._is_auth_failure(pair_output):
                        recovered = True
                        await self._recover_pairing(address)
                        info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
                        continue
                    self._raise_translated(
                        pair_output,
                        context="pair",
                        address=address,
                        event="bluetooth_pair_failed",
                    )
                info_code, info = await self._runner(("bluetoothctl", "info", address), 5)

            trusted = info_code == 0 and self._info_yes(info, "Trusted")
            if not trusted:
                trust_code, _ = await self._runner(("bluetoothctl", "trust", address), 6)
                if trust_code:
                    raise RuntimeError("Der Lautsprecher konnte nicht gespeichert werden.")

            connected = info_code == 0 and self._info_yes(info, "Connected")
            if not connected:
                connected, connect_output = await self._connect_with_retries(address)
                if not connected:
                    if not recovered and self._is_auth_failure(connect_output):
                        recovered = True
                        await self._recover_pairing(address)
                        info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
                        continue
                    self._raise_translated(
                        connect_output,
                        context="connect",
                        address=address,
                        event="bluetooth_connect_failed",
                    )
            break

        self.settings.device_address = address
        self.settings.device_name = name
        self.settings.output = "bluetooth"
        self.settings.enabled = True
        self._save_settings()
        self._merge_device(BluetoothDevice(address, name, paired=True, connected=True))
        self._sort_devices()

        await self._wait_for_services_resolved(address)
        sink = await self._select_bluetooth_sink(address)
        if sink is None:
            raise RuntimeError(
                "Der Bluetooth-Lautsprecher ist verbunden, aber die Audioausgabe wurde "
                "nicht gefunden. Bitte erneut versuchen."
            )

    async def _pair_with_retry(self, address: str) -> tuple[bool, str]:
        last_output = ""
        for attempt in range(2):
            _, pair_output = await self._runner(
                (
                    "bluetoothctl",
                    "--timeout",
                    str(int(self.PAIR_TIMEOUT_SECONDS)),
                    "pair",
                    address,
                ),
                self.PAIR_OUTER_TIMEOUT_SECONDS,
            )
            last_output = pair_output
            info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
            if info_code == 0 and self._info_yes(info, "Paired"):
                return True, pair_output
            if attempt == 0:
                LOGGER.warning(
                    "bluetooth_pair_attempt_failed address=%s output=%s",
                    address,
                    self._single_line_output(pair_output),
                )
                await self._sleep(self.CONNECT_BACKOFF_SECONDS)
        return False, last_output

    async def _recover_pairing(self, address: str) -> None:
        LOGGER.warning("bluetooth_recovering_pairing address=%s", address)
        await self._runner(("bluetoothctl", "remove", address), 6)
        if not await self._rediscover_device(address):
            return
        paired, _ = await self._pair_with_retry(address)
        if paired:
            await self._runner(("bluetoothctl", "trust", address), 6)

    async def _connect_with_retries(
        self,
        address: str,
        *,
        attempts: int | None = None,
    ) -> tuple[bool, str]:
        attempts = self.CONNECT_MAX_ATTEMPTS if attempts is None else attempts
        last_output = ""
        for attempt in range(attempts):
            _, output = await self._runner(
                (
                    "bluetoothctl",
                    "--timeout",
                    str(int(self.CONNECT_ATTEMPT_TIMEOUT_SECONDS)),
                    "connect",
                    address,
                ),
                self.CONNECT_OUTER_TIMEOUT_SECONDS,
            )
            last_output = output
            check_code, check_info = await self._runner(("bluetoothctl", "info", address), 5)
            if check_code == 0 and self._info_yes(check_info, "Connected"):
                return True, output
            if attempt + 1 < attempts:
                await self._sleep(self.CONNECT_BACKOFF_SECONDS)
        return False, last_output

    async def _wait_for_services_resolved(self, address: str) -> None:
        elapsed = 0.0
        while elapsed < self.SERVICES_RESOLVED_TIMEOUT_SECONDS:
            code, info = await self._runner(("bluetoothctl", "info", address), 5)
            if code == 0 and self._info_yes(info, "ServicesResolved"):
                return
            await self._sleep(self.SERVICES_RESOLVED_POLL_INTERVAL_SECONDS)
            elapsed += self.SERVICES_RESOLVED_POLL_INTERVAL_SECONDS
        LOGGER.warning("bluetooth_services_not_resolved address=%s", address)

    async def _ensure_bluetooth_connected(self, address: str) -> None:
        async with self._lock:
            await self._ensure_adapter_ready()
            await self._cancel_discovery()
            info_code, info = await self._runner(("bluetoothctl", "info", address), 5)
            if info_code == 0 and self._info_yes(info, "Connected"):
                return
            connected, output = await self._connect_with_retries(
                address, attempts=self.ENSURE_CONNECT_MAX_ATTEMPTS
            )
            if not connected:
                self._raise_translated(
                    output,
                    context="connect",
                    address=address,
                    event="bluetooth_ensure_connect_failed",
                )

    # -- Playback -------------------------------------------------------

    def _player(self) -> str | None:
        for command in ("paplay", "pw-play", "afplay", "aplay"):
            path = self._find(command)
            if path:
                return path
        return None

    def _play_command(
        self,
        player: str,
        sound_path: Path,
        sink: str | None,
    ) -> tuple[str, ...]:
        name = Path(player).name
        if name == "aplay":
            return (player, "-q", str(sound_path))
        if name == "afplay":
            return (player, str(sound_path))
        if sink and name == "paplay":
            return (player, f"--device={sink}", str(sound_path))
        if sink and name == "pw-play":
            return (player, "--target", sink, str(sound_path))
        return (player, str(sound_path))

    async def _select_bluetooth_sink(self, address: str) -> str | None:
        needle = address.replace(":", "_").lower()
        elapsed = 0.0
        while True:
            sink = await self._find_pulse_sink(needle)
            if sink:
                await self._runner(("pactl", "set-default-sink", sink), 5)
                return sink
            if elapsed >= self.SINK_WAIT_TIMEOUT_SECONDS:
                return None
            await self._sleep(self.SINK_POLL_INTERVAL_SECONDS)
            elapsed += self.SINK_POLL_INTERVAL_SECONDS

    async def _select_aux_sink(self) -> str | None:
        sink = await self._find_pulse_sink("analog-stereo", "headphones", "bcm2835")
        if sink:
            await self._runner(("pactl", "set-default-sink", sink), 5)
        return sink

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

    # -- Bookkeeping ------------------------------------------------------

    def _merge_device(self, device: BluetoothDevice) -> None:
        for index, existing in enumerate(self.devices):
            if existing.address == device.address:
                self.devices[index] = device
                return
        self.devices.append(device)

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
                    float(raw.get("delay_seconds", defaults.delay_milliseconds / 1000)) * 1000
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
                run_signals_enabled=bool(
                    raw.get("run_signals_enabled", defaults.run_signals_enabled)
                ),
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
        return self._read_sound_duration_milliseconds(
            self._sound_path,
            metadata_error_event="start_sound_metadata_failed",
        )

    @staticmethod
    def _read_sound_duration_milliseconds(
        sound_path: Path,
        *,
        metadata_error_event: str,
    ) -> int:
        try:
            with wave.open(str(sound_path), "rb") as recording:
                return round(recording.getnframes() / recording.getframerate() * 1000)
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            LOGGER.exception(metadata_error_event)
            return 0

    # -- Error translation --------------------------------------------------

    def _raise_translated(
        self,
        output: str,
        *,
        context: str,
        address: str,
        event: str,
    ) -> None:
        LOGGER.warning("%s address=%s output=%s", event, address, self._single_line_output(output))
        raise RuntimeError(self._translate_error(output, context=context))

    def _translate_error(self, output: str, *, context: str) -> str:
        lowered = output.lower()
        for needles, message in self._ERROR_TRANSLATIONS:
            if any(needle in lowered for needle in needles):
                return self._with_diagnostic(message, output)
        fallback = {
            "pair": (
                "Der Lautsprecher konnte nicht gekoppelt werden. Bitte Pairing-Modus "
                "prüfen und erneut versuchen."
            ),
            "connect": (
                "Der Lautsprecher konnte nicht verbunden werden. Bitte Pairing-Modus "
                "prüfen und erneut versuchen."
            ),
        }.get(context, "Der Bluetooth-Vorgang ist fehlgeschlagen.")
        return self._with_diagnostic(fallback, output)

    def _with_diagnostic(self, message: str, output: str) -> str:
        diagnostic = self._single_line_output(output)
        return f"{message} ({diagnostic})" if diagnostic else message

    @classmethod
    def _is_auth_failure(cls, output: str) -> bool:
        lowered = output.lower()
        return any(needle in lowered for needle in cls._AUTH_FAILURE_NEEDLES)

    # -- bluetoothctl output helpers ----------------------------------------

    @staticmethod
    def _device_known(code: int, info: str) -> bool:
        return code == 0 and "not available" not in info.lower()

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
