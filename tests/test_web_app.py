from __future__ import annotations

import asyncio
import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, web

from takt import __version__
from takt.application.timer_controller import TimerController
from takt.buzzer import NullBuzzer
from takt.config import Config
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.app import STATIC_ROOT, _is_loopback_address, create_web_app
from takt.web.runtime import WebRuntime
from tests.helpers import FakeClock


class UnavailablePowerService:
    available = False
    model = ""

    def shutdown(self) -> None:
        raise AssertionError("shutdown must not be called")


class WebApplicationTests(unittest.TestCase):
    def test_maintenance_address_filter_accepts_only_ip_loopback_addresses(self) -> None:
        self.assertTrue(_is_loopback_address("127.0.0.1"))
        self.assertTrue(_is_loopback_address("::1"))
        self.assertFalse(_is_loopback_address("192.168.1.20"))
        self.assertFalse(_is_loopback_address("localhost"))

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
                        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                        self.assertIn(
                            "frame-ancestors 'none'",
                            response.headers["Content-Security-Policy"],
                        )
                        self.assertEqual(
                            response.headers["X-Content-Type-Options"],
                            "nosniff",
                        )
                        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                        health = await response.json()
                        self.assertTrue(health["ok"])
                        self.assertEqual(health["version"], __version__)
                        self.assertEqual(health["database_schema_version"], 1)

                    async with client.get(f"{base_url}/api/bootstrap?days=30") as response:
                        self.assertEqual(response.status, 200)
                        bootstrap = await response.json()
                        self.assertEqual(bootstrap["state"]["state"], "ready")
                        self.assertEqual(bootstrap["history"]["today"], [])
                        self.assertEqual(bootstrap["system"]["audio"]["output"], "off")

                    async with client.ws_connect(f"{base_url}/api/events") as websocket:
                        initial_event = await websocket.receive_json()
                        self.assertEqual(initial_event["type"], "state")
                        await websocket.send_str("ping")
                        self.assertEqual((await websocket.receive()).data, "pong")

                    async with client.get(f"{base_url}/api/database/export") as response:
                        self.assertEqual(response.status, 400)

                    async with client.get(
                        f"{base_url}/api/database/export?format=csv",
                        headers={"Sec-Fetch-Site": "cross-site"},
                    ) as response:
                        self.assertEqual(response.status, 403)

                    async with client.get(f"{base_url}/api/database/export?format=db") as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.content_type, "application/vnd.sqlite3")
                        self.assertRegex(
                            response.headers["Content-Disposition"],
                            r'attachment; filename="takt-\d{4}-\d{2}-\d{2}\.db"',
                        )
                        exported_db = await response.read()
                    exported_path = Path(temporary_directory) / "exported.db"
                    exported_path.write_bytes(exported_db)
                    with sqlite3.connect(exported_path) as connection:
                        self.assertEqual(
                            connection.execute("PRAGMA integrity_check").fetchone()[0],
                            "ok",
                        )
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                            0,
                        )
                    async with client.get(f"{base_url}/api/database/export?format=csv") as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.content_type, "text/csv")
                        self.assertRegex(
                            response.headers["Content-Disposition"],
                            r'attachment; filename="takt-runs-\d{4}-\d{2}-\d{2}\.csv"',
                        )
                        csv_rows = list(csv.DictReader(io.StringIO(await response.text())))
                        self.assertEqual(csv_rows, [])
                    async with client.post(
                        f"{base_url}/api/audio/settings",
                        json={
                            "enabled": False,
                            "output": "off",
                            "delay_milliseconds": 2_500,
                            "device_address": None,
                            "device_name": None,
                            "run_signals_enabled": False,
                        },
                    ) as response:
                        self.assertEqual(response.status, 200)
                        audio_settings = await response.json()
                        self.assertEqual(
                            audio_settings["system"]["audio"]["delay_milliseconds"],
                            2_500,
                        )
                        self.assertFalse(
                            audio_settings["system"]["audio"]["run_signals_enabled"]
                        )

                    async with client.post(
                        f"{base_url}/internal/maintenance/acquire",
                        json={
                            "request_id": "install-job-1",
                            "owner": "takt-agent",
                            "reason": "Install release 0.2.0",
                            "ttl_seconds": 30,
                        },
                    ) as response:
                        self.assertEqual(response.status, 200)
                        maintenance = await response.json()
                        lease_token = maintenance["lease_token"]
                        self.assertTrue(maintenance["maintenance"]["held"])

                    async with client.post(
                        f"{base_url}/internal/maintenance/acquire",
                        json={
                            "request_id": "install-job-1",
                            "owner": "takt-agent",
                            "ttl_seconds": 30,
                        },
                    ) as response:
                        self.assertEqual(response.status, 200)
                        replay = await response.json()
                        self.assertTrue(replay["reused"])
                        self.assertEqual(replay["lease_token"], lease_token)

                    async with client.post(
                        f"{base_url}/api/action",
                        json={"action": "primary"},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        action = await response.json()
                        self.assertFalse(action["ok"])
                        self.assertEqual(action["state"]["state"], "ready")

                    async with client.post(
                        f"{base_url}/api/action",
                        data='{"action":"primary"}',
                        headers={"Content-Type": "text/plain"},
                    ) as response:
                        self.assertEqual(response.status, 415)

                    async with client.post(
                        f"{base_url}/api/action",
                        json={"action": "primary"},
                        headers={"Origin": "https://attacker.example"},
                    ) as response:
                        self.assertEqual(response.status, 403)

                    async with client.post(
                        f"{base_url}/internal/maintenance/release",
                        json={"lease_token": "incorrect-token"},
                    ) as response:
                        self.assertEqual(response.status, 403)

                    async with client.post(
                        f"{base_url}/internal/maintenance/release",
                        json={"lease_token": lease_token},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        released = await response.json()
                        self.assertTrue(released["released"])
                        self.assertFalse(released["maintenance"]["held"])

                    async with client.post(
                        f"{base_url}/api/action",
                        json={"action": "primary"},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        action = await response.json()
                        self.assertEqual(action["state"]["state"], "running")

                    for export_format in ("db", "csv"):
                        async with client.get(
                            f"{base_url}/api/database/export?format={export_format}"
                        ) as response:
                            self.assertEqual(response.status, 409)
                    async with client.post(
                        f"{base_url}/internal/maintenance/acquire",
                        json={
                            "request_id": "install-job-while-running",
                            "owner": "takt-agent",
                        },
                    ) as response:
                        self.assertEqual(response.status, 409)
                        conflict = await response.json()
                        self.assertFalse(conflict["acquired"])
                        self.assertEqual(conflict["maintenance"]["timer_state"], "running")

                    if (
                        not (STATIC_ROOT / "index.html").is_file()
                        or not (STATIC_ROOT / "assets").is_dir()
                    ):
                        self.skipTest("Frontend assets are not built; run scripts/build_web_ui.sh")

                    async with client.get(base_url) as response:
                        self.assertEqual(response.status, 200)
                        page = await response.text()
                        self.assertIn("TAKT", page)
                        self.assertIn("./assets/", page)

                    javascript_asset = next((STATIC_ROOT / "assets").glob("*.js"))
                    async with client.get(f"{base_url}/assets/{javascript_asset.name}") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("javascript", response.content_type)
            finally:
                await runtime.close()
                await runner.cleanup()
                repository.close()


if __name__ == "__main__":
    unittest.main()
