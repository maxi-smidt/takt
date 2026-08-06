from __future__ import annotations

import argparse
import logging
import sys

from takt.application.timer_controller import TimerController
from takt.buzzer import GpioBuzzer, MockBuzzer, NullBuzzer
from takt.clock import SystemClock
from takt.config import load_config
from takt.input.gpio_button_input import GpioButtonInput
from takt.input.mock_button_input import MockButtonInput
from takt.logging_config import configure_logging
from takt.persistence.backup_service import create_daily_backup
from takt.persistence.run_repository import SQLiteRunRepository

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAKT")
    parser.add_argument("--windowed", action="store_true", help="in einem Fenster starten")
    parser.add_argument(
        "--mock-gpio",
        action="store_true",
        help="Laptop-Taster statt Raspberry-Pi-GPIO verwenden",
    )
    parser.add_argument(
        "--mock-buzzer",
        action="store_true",
        help="Summer sichtbar und über den Systemton simulieren",
    )
    parser.add_argument(
        "--database",
        type=str,
        help="alternativer Datenbankpfad für Tests",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    LOGGER.info("application_start")
    config = load_config()
    if args.database:
        from pathlib import Path

        config.storage.database_path = Path(args.database).expanduser().resolve()

    repository = SQLiteRunRepository(config.storage.database_path)
    if config.storage.backup_enabled:
        try:
            create_daily_backup(
                repository,
                config.storage.backup_directory,
                config.storage.backup_retention_days,
            )
        except Exception:
            LOGGER.exception("daily_backup_failed")

    from PySide6.QtWidgets import QApplication

    from takt.ui.main_window import MainWindow
    from takt.ui.theme import APP_STYLE

    app = QApplication(sys.argv)
    app.setApplicationName("TAKT")
    app.setOrganizationName("TAKT")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    controller = TimerController(
        SystemClock(),
        repository,
        double_press_seconds=config.gpio.double_press_seconds,
    )
    if args.mock_gpio:
        hardware_label = "Mock aktiv"
    elif config.gpio.enabled:
        hardware_label = "wird verbunden"
    else:
        hardware_label = "nur Tastatur/Maus"
    window = MainWindow(
        controller,
        repository,
        config,
        hardware_label,
        show_mock_button=args.mock_gpio,
        show_mock_buzzer=args.mock_buzzer,
    )

    if args.mock_gpio:
        button_input = MockButtonInput(window.primary_press_requested.emit)
        window.set_hardware_status("Mock aktiv", available=True)
    elif config.gpio.enabled:
        try:
            button_input = GpioButtonInput(
                config.gpio.pin_bcm,
                config.gpio.bounce_seconds,
                window.primary_press_requested.emit,
            )
            window.set_hardware_status("verbunden", available=True)
        except Exception:
            LOGGER.exception("GPIO unavailable, falling back to keyboard and mouse")
            button_input = MockButtonInput(window.primary_press_requested.emit)
            window.set_hardware_status(
                "nicht verfügbar · Tastatur/Maus aktiv",
                available=False,
            )
    else:
        button_input = MockButtonInput(window.primary_press_requested.emit)
        window.set_hardware_status("nur Tastatur/Maus", available=False)

    if args.mock_buzzer:
        buzzer = MockBuzzer(window.show_mock_buzzer_signal)
    elif config.buzzer.enabled:
        try:
            buzzer = GpioBuzzer(config.buzzer.pin_bcm)
        except Exception:
            LOGGER.exception("buzzer unavailable")
            buzzer = NullBuzzer()
    else:
        buzzer = NullBuzzer()
    window.set_devices(button_input, buzzer)

    if args.windowed or not config.application.fullscreen:
        window.show()
    else:
        window.showFullScreen()

    exit_code = app.exec()
    buzzer.close()
    button_input.close()
    repository.close()
    LOGGER.info("application_shutdown exit_code=%s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
