from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, web

from takt.application.timer_controller import TimerController
from takt.buzzer import NullBuzzer
from takt.config import Config
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.app import STATIC_ROOT, create_web_app
from takt.web.runtime import WebRuntime
from tests.helpers import FakeClock


class UnavailablePowerService:
    available = False
    model = ""

    def shutdown(self) -> None:
        raise AssertionError("shutdown must not be called")


class WebApplicationTests(unittest.TestCase):
    def test_health_bootstrap_and_timer_action(self) -> None:
        asyncio.run(self._exercise_application())

    async def _exercise_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = SQLiteRunRepository(Path(temporary_directory) / "test.db")
            controller = TimerController(FakeClock(), repository)
            config = Config()
            config.audio.settings_path = Path(temporary_directory) / "audio.json"
            runtime = WebRuntime(
                controller,
                repository,
                config,
                NullBuzzer(),
                UnavailablePowerService(),  # type: ignore[arg-type]
                hardware_label="Browser",
                hardware_available=True,
                show_mock_button=True,
                show_mock_buzzer=False,
            )
            runner = web.AppRunner(create_web_app(runtime))
            runtime.start()
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            sockets = site._server.sockets  # type: ignore[union-attr]
            port = sockets[0].getsockname()[1]
            base_url = f"http://127.0.0.1:{port}"
            try:
                async with ClientSession() as client:
                    async with client.get(f"{base_url}/health") as response:
                        self.assertEqual(response.status, 200)
                        health = await response.json()
                        self.assertTrue(health["ok"])
                        self.assertEqual(health["version"], "0.1.0")
                        self.assertEqual(health["database_schema_version"], 1)

                    async with client.get(f"{base_url}/api/bootstrap?days=30") as response:
                        self.assertEqual(response.status, 200)
                        bootstrap = await response.json()
                        self.assertEqual(bootstrap["state"]["state"], "ready")
                        self.assertEqual(bootstrap["history"]["today"], [])
                        self.assertEqual(bootstrap["system"]["audio"]["output"], "off")

                    async with client.post(
                        f"{base_url}/api/audio/settings",
                        json={
                            "enabled": False,
                            "output": "off",
                            "delay_milliseconds": 2_500,
                            "device_address": None,
                            "device_name": None,
                        },
                    ) as response:
                        self.assertEqual(response.status, 200)
                        audio_settings = await response.json()
                        self.assertEqual(
                            audio_settings["system"]["audio"]["delay_milliseconds"],
                            2_500,
                        )

                    async with client.post(
                        f"{base_url}/api/action",
                        json={"action": "primary"},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        action = await response.json()
                        self.assertEqual(action["state"]["state"], "running")

                    async with client.get(base_url) as response:
                        self.assertEqual(response.status, 200)
                        page = await response.text()
                        self.assertIn("TAKT", page)
                        self.assertIn("./assets/", page)

                    javascript_asset = next((STATIC_ROOT / "assets").glob("*.js"))
                    async with client.get(
                        f"{base_url}/assets/{javascript_asset.name}"
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("javascript", response.content_type)
            finally:
                await runtime.close()
                await runner.cleanup()
                repository.close()


if __name__ == "__main__":
    unittest.main()
