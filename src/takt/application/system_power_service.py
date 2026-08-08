from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)


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
        # The installer sets up a passwordless sudo rule for exactly this
        # command, which is what actually works when takt.service runs
        # without a logind session (polkit would otherwise demand interactive
        # authentication for a plain, unprivileged `systemctl poweroff`).
        commands = (
            ["sudo", "-n", "systemctl", "poweroff"],
            ["systemctl", "poweroff"],
        )
        last_error: subprocess.CalledProcessError | None = None
        last_diagnostic = ""
        for command in commands:
            try:
                subprocess.run(
                    command,
                    check=True,
                    timeout=10,
                    capture_output=True,
                    text=True,
                )
                return
            except subprocess.CalledProcessError as error:
                last_error = error
                last_diagnostic = self._single_line_output(error)
                LOGGER.warning(
                    "shutdown_command_failed command=%s returncode=%s output=%s",
                    " ".join(command),
                    error.returncode,
                    last_diagnostic,
                )
                continue
            except FileNotFoundError as error:
                if command[0] == "systemctl":
                    raise RuntimeError(
                        "Der Systemdienst zum Herunterfahren wurde nicht gefunden."
                    ) from error
                LOGGER.warning("shutdown_command_missing command=%s", command[0])
                continue
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "Die Anfrage zum Herunterfahren hat zu lange gedauert."
                ) from error
        if last_error is not None:
            message = (
                "Raspberry Pi OS hat die Anfrage abgelehnt. "
                "Bitte die Berechtigung für systemctl poweroff prüfen."
            )
            if last_diagnostic:
                message = f"{message} ({last_diagnostic})"
            raise RuntimeError(message) from last_error

    @staticmethod
    def _single_line_output(error: subprocess.CalledProcessError) -> str:
        """Keep the process diagnostics useful without a multiline error message."""
        combined = " ".join(
            part.strip()
            for part in (error.stderr or "", error.stdout or "")
            if part and part.strip()
        )
        return " ".join(combined.split())[-500:]

    @classmethod
    def _read_model(cls) -> str:
        for path in cls._MODEL_PATHS:
            try:
                return path.read_text(encoding="utf-8").rstrip("\x00").strip()
            except (OSError, UnicodeError):
                continue
        return ""
