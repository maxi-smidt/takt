from __future__ import annotations

import platform
import subprocess
from pathlib import Path


class SystemPowerService:
    """Request an orderly shutdown only on Raspberry Pi hardware."""

    _MODEL_PATHS = (
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    )

    def __init__(self) -> None:
        self._model = self._read_model()

    @property
    def available(self) -> bool:
        return platform.system() == "Linux" and "Raspberry Pi" in self._model

    @property
    def model(self) -> str:
        return self._model

    def shutdown(self) -> None:
        if not self.available:
            raise RuntimeError("Herunterfahren ist nur auf einem Raspberry Pi verfügbar.")
        try:
            subprocess.run(
                ["systemctl", "poweroff"],
                check=True,
                timeout=10,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                "Der Systemdienst zum Herunterfahren wurde nicht gefunden."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "Die Anfrage zum Herunterfahren hat zu lange gedauert."
            ) from error
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                "Raspberry Pi OS hat die Anfrage abgelehnt. "
                "Bitte die Berechtigung für systemctl poweroff prüfen."
            ) from error

    @classmethod
    def _read_model(cls) -> str:
        for path in cls._MODEL_PATHS:
            try:
                return path.read_text(encoding="utf-8").rstrip("\x00").strip()
            except (OSError, UnicodeError):
                continue
        return ""

