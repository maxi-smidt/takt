from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web

from takt.registry.app import create_registry_app
from takt.registry.auth import AdminAuth
from takt.registry.storage import RegistryStore


class DeploymentApiTests(unittest.TestCase):
    def test_deployment_api_keeps_csrf_and_auth_boundaries(self) -> None:
        asyncio.run(self._exercise())

    async def _exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RegistryStore(root)
            source = root / "release.tar.gz"
            source.write_bytes(b"release")
            release = store.add_release(
                version="0.2.0",
                filename=source.name,
                sha256=hashlib.sha256(b"release").hexdigest(),
                size=source.stat().st_size,
                source=source,
            )
            auth = AdminAuth("correct-horse-battery", root)
            runner = web.AppRunner(create_registry_app(store, auth))
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
            base_url = f"http://127.0.0.1:{port}"
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                    async with client.post(
                        f"{base_url}/api/session",
                        json={"password": "correct-horse-battery"},
                    ):
                        pass
                    async with client.get(f"{base_url}/api/session") as response:
                        csrf = (await response.json())["csrf_token"]
                    payload = {
                        "target": "127.0.0.1",
                        "ssh_user": "pi",
                        "device_name": "Lane 1",
                        "hostname": "",
                        "confirm_hostname_change": False,
                        "registry_url": "https://registry.example",
                        "release_id": release["id"],
                    }
                    async with client.post(
                        f"{base_url}/api/deployments", json=payload
                    ) as response:
                        self.assertEqual(response.status, 403)
                    async with client.post(
                        f"{base_url}/api/deployments",
                        json={**payload, "target": "not a host"},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 400)
                    async with client.post(
                        f"{base_url}/api/deployments",
                        json={**payload, "hostname": "takt-01"},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 400)
                    async with client.post(
                        f"{base_url}/api/deployments",
                        json=payload,
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 202)
                        deployment = (await response.json())["deployment"]
                    self.assertEqual(deployment["status"], "pending")
                    async with client.get(f"{base_url}/api/deployments") as response:
                        self.assertEqual(len((await response.json())["deployments"]), 1)
            finally:
                await runner.cleanup()
                store.close()


if __name__ == "__main__":
    unittest.main()
