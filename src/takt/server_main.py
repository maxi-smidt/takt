from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from aiohttp import web

from takt.application.system_power_service import SystemPowerService
from takt.application.timer_controller import TimerController
from takt.buzzer import GpioBuzzer, MockBuzzer, NullBuzzer
from takt.clock import SystemClock
from takt.config import load_config
from takt.input.gpio_button_input import GpioButtonInput
from takt.input.mock_button_input import MockButtonInput
from takt.logging_config import configure_logging
from takt.persistence.backup_service import create_daily_backup
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.app import create_web_app
from takt.web.runtime import WebRuntime

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAKT local web server")
    parser.add_argument("--host", help="listening address, default from config.toml")
    parser.add_argument("--port", type=int, help="listening port, default from config.toml")
    parser.add_argument("--mock-gpio", action="store_true", help="show browser mock button")
    parser.add_argument("--mock-buzzer", action="store_true", help="show browser buzzer signal")
    parser.add_argument("--database", help="alternative database path for tests")
    return parser


async def _serve(args: argparse.Namespace) -> None:
    configure_logging()
    LOGGER.info("server_start")
    config = load_config()
    if args.database:
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

    controller = TimerController(
        SystemClock(),
        repository,
        double_press_seconds=config.gpio.double_press_seconds,
    )
    power_service = SystemPowerService()
    loop = asyncio.get_running_loop()
    def on_mock_buzzer(event: str) -> None:
        LOGGER.info("browser_mock_buzzer event=%s", event)

    if args.mock_buzzer:
        buzzer = MockBuzzer(on_mock_buzzer)
    elif config.buzzer.enabled:
        try:
            buzzer = GpioBuzzer(config.buzzer.pin_bcm)
        except Exception:
            LOGGER.exception("buzzer unavailable")
            buzzer = NullBuzzer()
    else:
        buzzer = NullBuzzer()

    if args.mock_gpio:
        hardware_label = "Mock aktiv"
        hardware_available = True
    elif config.gpio.enabled:
        hardware_label = "wird verbunden"
        hardware_available = False
    else:
        hardware_label = "nur Browser"
        hardware_available = False

    runtime = WebRuntime(
        controller,
        repository,
        config,
        buzzer,
        power_service,
        hardware_label=hardware_label,
        hardware_available=hardware_available,
        show_mock_button=args.mock_gpio,
        show_mock_buzzer=args.mock_buzzer,
    )
    def physical_press() -> None:
        loop.call_soon_threadsafe(runtime.primary_press, "gpio-taster")

    if args.mock_gpio:
        button_input = MockButtonInput(physical_press)
        runtime.set_hardware_status("Mock aktiv", True)
    elif config.gpio.enabled:
        try:
            button_input = GpioButtonInput(
                config.gpio.pin_bcm,
                config.gpio.bounce_seconds,
                physical_press,
            )
            runtime.set_hardware_status("verbunden", True)
        except Exception:
            LOGGER.exception("GPIO unavailable, browser control remains active")
            button_input = MockButtonInput(physical_press)
            runtime.set_hardware_status("nicht verfügbar · Browser aktiv", False)
    else:
        button_input = MockButtonInput(physical_press)
        runtime.set_hardware_status("nur Browser", False)

    app = create_web_app(runtime)
    runtime.start()
    runner = web.AppRunner(app, access_log=LOGGER)
    await runner.setup()
    host = args.host or config.server.host
    port = args.port or config.server.port
    site = web.TCPSite(runner, host, port)
    stop_event = asyncio.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await site.start()
        LOGGER.info("server_ready url=http://%s:%s", host, port)
        await stop_event.wait()
    finally:
        await runtime.close()
        await runner.cleanup()
        buzzer.close()
        button_input.close()
        repository.close()
        LOGGER.info("server_shutdown")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
