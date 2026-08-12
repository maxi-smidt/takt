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
        # Raspberry Pi OS' shutdown helper reliably reaches halt on older Pi
        # models as well. The installer grants passwordless sudo for these
        # exact commands because the web service has no interactive session.
        commands = (
            ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
            ["sudo", "-n", "/sbin/shutdown", "-h", "now"],
            ["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
            ["sudo", "-n", "/bin/systemctl", "poweroff"],
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
            except FileNotFoundError:
                LOGGER.warning("shutdown_command_missing command=%s", command[2])
                continue
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "Die Anfrage zum Herunterfahren hat zu lange gedauert."
                ) from error
        if last_error is not None:
            message = (
                "Raspberry Pi OS hat die Anfrage abgelehnt. "
                "Bitte die sudo-Berechtigung für shutdown -h now prüfen."
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
