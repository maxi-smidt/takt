from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _expanded(path: str) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(slots=True)
class ApplicationConfig:
    fullscreen: bool = True
    saved_confirmation_seconds: float = 2.0


@dataclass(slots=True)
class GpioConfig:
    enabled: bool = True
    pin_bcm: int = 17
    bounce_seconds: float = 0.05
    double_press_seconds: float = 0.60
    long_press_seconds: float = 1.00


@dataclass(slots=True)
class BuzzerConfig:
    enabled: bool = False
    pin_bcm: int = 27


@dataclass(slots=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(slots=True)
class AudioConfig:
    enabled: bool = False
    output: str = "off"
    delay_milliseconds: int = 3_000
    settings_path: Path = field(default_factory=lambda: _expanded("~/.config/takt/audio.json"))


@dataclass(slots=True)
class DisplayConfig:
    chart_default_days: int = 30
    best_runs_limit: int = 5


@dataclass(slots=True)
class StorageConfig:
    database_path: Path = field(default_factory=lambda: _expanded("~/.local/share/takt/takt.db"))
    backup_enabled: bool = True
    backup_directory: Path = field(default_factory=lambda: _expanded("~/.local/share/takt/backups"))
    backup_retention_days: int = 30


@dataclass(slots=True)
class Config:
    application: ApplicationConfig = field(default_factory=ApplicationConfig)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    buzzer: BuzzerConfig = field(default_factory=BuzzerConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def default_config_path() -> Path:
    override = os.environ.get("TAKT_CONFIG")
    return _expanded(override) if override else _expanded("~/.config/takt/config.toml")


def load_config(path: Path | None = None) -> Config:
    """Load a small TOML configuration, falling back to safe defaults."""
    config = Config()
    config_path = path or default_config_path()
    if not config_path.exists():
        return config
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    app = raw.get("application", {})
    gpio = raw.get("gpio", {})
    buzzer = raw.get("buzzer", {})
    server = raw.get("server", {})
    audio = raw.get("audio", {})
    display = raw.get("display", {})
    storage = raw.get("storage", {})

    config.application.fullscreen = bool(app.get("fullscreen", config.application.fullscreen))
    confirmation = float(
        app.get("saved_confirmation_seconds", config.application.saved_confirmation_seconds)
    )
    config.application.saved_confirmation_seconds = min(max(confirmation, 0.5), 10.0)

    config.gpio.enabled = bool(gpio.get("enabled", config.gpio.enabled))
    config.gpio.pin_bcm = int(gpio.get("pin_bcm", config.gpio.pin_bcm))
    config.gpio.bounce_seconds = max(0.01, float(gpio.get("bounce_seconds", 0.05)))
    config.gpio.double_press_seconds = min(
        max(float(gpio.get("double_press_seconds", 0.60)), 0.2), 2.0
    )
    config.gpio.long_press_seconds = min(
        max(float(gpio.get("long_press_seconds", 1.00)), 0.5), 5.0
    )

    config.buzzer.enabled = bool(buzzer.get("enabled", config.buzzer.enabled))
    config.buzzer.pin_bcm = int(buzzer.get("pin_bcm", config.buzzer.pin_bcm))

    config.server.host = str(server.get("host", config.server.host))
    config.server.port = min(max(int(server.get("port", config.server.port)), 1), 65_535)

    config.audio.enabled = bool(audio.get("enabled", config.audio.enabled))
    output = str(audio.get("output", config.audio.output))
    config.audio.output = output if output in {"off", "aux", "bluetooth"} else "off"
    if "delay_milliseconds" in audio:
        delay_milliseconds = int(audio["delay_milliseconds"])
    else:
        delay_milliseconds = round(
            float(audio.get("delay_seconds", config.audio.delay_milliseconds / 1000)) * 1000
        )
    config.audio.delay_milliseconds = max(delay_milliseconds, 0)
    audio_settings_path = audio.get("settings_path")
    if audio_settings_path:
        config.audio.settings_path = _expanded(str(audio_settings_path))

    config.display.chart_default_days = int(
        display.get("chart_default_days", config.display.chart_default_days)
    )
    config.display.best_runs_limit = min(max(int(display.get("best_runs_limit", 5)), 1), 25)

    database_path = storage.get("database_path")
    if database_path:
        config.storage.database_path = _expanded(str(database_path))
    backup_directory = storage.get("backup_directory")
    if backup_directory:
        config.storage.backup_directory = _expanded(str(backup_directory))
    config.storage.backup_enabled = bool(
        storage.get("backup_enabled", config.storage.backup_enabled)
    )
    config.storage.backup_retention_days = min(
        max(int(storage.get("backup_retention_days", 30)), 1), 365
    )
    return config
