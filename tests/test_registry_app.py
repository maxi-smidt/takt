from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, FormData, web

from takt.persistence.run_repository import SQLiteRunRepository
from takt.registry.app import create_registry_app
from takt.registry.auth import AdminAuth
from takt.registry.storage import RegistryStore


class RegistryApplicationTests(unittest.TestCase):
    def test_enrollment_release_job_and_mirror_flow(self) -> None:
        asyncio.run(self._exercise_registry())

    async def _exercise_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory) / "registry"
            data_directory.mkdir()
            store = RegistryStore(data_directory)
            auth = AdminAuth("correct-horse-battery", data_directory)
            runner = web.AppRunner(create_registry_app(store, auth))
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            sockets = site._server.sockets  # type: ignore[union-attr]
            base_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                    async with client.post(
                        f"{base_url}/api/session",
                        data='{"password":"correct-horse-battery"}',
                        headers={"Content-Type": "text/plain"},
                    ) as response:
                        self.assertEqual(response.status, 415)
                    csrf = await self._login(client, base_url)
                    async with client.post(
                        f"{base_url}/api/enrollment-codes",
                        json={"label": "Lane 1"},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 201)
                        enrollment = await response.json()
                        enrollment_code = enrollment["code"]
                        self.assertEqual(enrollment["expires_in_minutes"], 60)

                    device_id = "12345678-1234-1234-1234-123456789abc"
                    proposed_token = "a" * 64
                    enrollment_payload = {
                        "enrollment_code": enrollment_code,
                        "device_id": device_id,
                        "name": "Lane 1",
                        "hostname": "takt-01",
                        "device_token": proposed_token,
                    }
                    async with client.post(
                        f"{base_url}/agent/enroll",
                        json=enrollment_payload,
                    ) as response:
                        self.assertEqual(response.status, 201)
                        device_token = (await response.json())["device_token"]
                        self.assertEqual(device_token, proposed_token)
                    # A lost enrollment response can be retried with the same identity secret.
                    async with client.post(
                        f"{base_url}/agent/enroll", json=enrollment_payload
                    ) as response:
                        self.assertEqual(response.status, 201)
                        self.assertEqual((await response.json())["device_token"], proposed_token)
                    agent_headers = {
                        "X-Device-ID": device_id,
                        "Authorization": f"Bearer {device_token}",
                    }

                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={"poll_seconds": {"invalid": True}},
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 400)

                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "app_version": "0.1.0",
                            "agent_version": "0.1.0",
                            "health": {"ok": True, "state": "ready"},
                            "protocol_version": 1,
                            "agent_session_id": "session-a",
                            "poll_seconds": 10,
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsNone((await response.json())["job"])

                    form = FormData()
                    form.add_field("version", "0.2.0")
                    release_archive = self._release_archive()
                    form.add_field(
                        "artifact",
                        io.BytesIO(release_archive),
                        filename="takt-0.2.0.tar.gz",
                        content_type="application/gzip",
                    )
                    async with client.post(
                        f"{base_url}/api/releases",
                        data=form,
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 201)
                        release = (await response.json())["release"]

                    async with client.post(
                        f"{base_url}/api/devices/{device_id}/jobs",
                        json={
                            "action": "install_release",
                            "payload": {"release_id": release["id"]},
                        },
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 201)
                        job = (await response.json())["job"]

                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "app_version": "0.1.0",
                            "agent_version": "0.1.0",
                            "protocol_version": 1,
                            "agent_session_id": "session-a",
                        },
                        headers=agent_headers,
                    ) as response:
                        claimed = (await response.json())["job"]
                        self.assertEqual(claimed["id"], job["id"])
                        self.assertEqual(claimed["release"]["version"], "0.2.0")
                        lease_id = claimed["lease_id"]

                    # A second process using the credential cannot take over the active lease.
                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "app_version": "0.1.0",
                            "agent_version": "0.1.0",
                            "protocol_version": 1,
                            "agent_session_id": "session-b",
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertIsNone((await response.json())["job"])

                    async with client.get(
                        f"{base_url}/agent/jobs/{job['id']}/artifact",
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 404)
                    async with client.get(
                        f"{base_url}/agent/jobs/{job['id']}/artifact",
                        headers={**agent_headers, "X-Job-Lease": "wrong"},
                    ) as response:
                        self.assertEqual(response.status, 404)
                    async with client.get(
                        f"{base_url}/agent/jobs/{job['id']}/artifact",
                        headers={**agent_headers, "X-Job-Lease": lease_id},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(await response.read(), release_archive)

                    async with client.post(
                        f"{base_url}/agent/jobs/{job['id']}",
                        json={"status": "succeeded", "progress": 100, "message": "healthy"},
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 409)
                    async with client.post(
                        f"{base_url}/agent/jobs/{job['id']}",
                        json={
                            "status": "succeeded",
                            "progress": 100,
                            "message": "healthy",
                            "lease_id": lease_id,
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)

                    database_path = Path(temporary_directory) / "takt.db"
                    repository = SQLiteRunRepository(database_path)
                    repository.close()
                    database_bytes = database_path.read_bytes()
                    async with client.post(
                        f"{base_url}/agent/mirror",
                        data=database_bytes,
                        headers={
                            **agent_headers,
                            "X-TAKT-SHA256": hashlib.sha256(database_bytes).hexdigest(),
                        },
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual((await response.json())["run_count"], 0)

                    async with client.get(f"{base_url}/api/devices") as response:
                        device = (await response.json())["devices"][0]
                        self.assertEqual(device["app_version"], "0.1.0")
                        self.assertEqual(device["run_count"], 0)
                        self.assertIsNotNone(device["last_mirror_at"])

                    async with client.get(f"{base_url}/api/devices/{device_id}/mirror") as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(await response.read(), database_bytes)

                    async with client.post(
                        f"{base_url}/api/devices/{device_id}/revoke",
                        json={},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsNotNone((await response.json())["device"]["revoked_at"])
                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={"agent_session_id": "session-a"},
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 401)
            finally:
                await runner.cleanup()
                store.close()

    async def _login(self, client: ClientSession, base_url: str) -> str:
        async with client.post(
            f"{base_url}/api/session", json={"password": "correct-horse-battery"}
        ) as response:
            self.assertEqual(response.status, 200)
        async with client.get(f"{base_url}/api/session") as response:
            session = await response.json()
            self.assertTrue(session["authenticated"])
            return str(session["csrf_token"])

    @staticmethod
    def _release_archive() -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, content in (
                ("takt/pyproject.toml", b"[project]\nname='takt'\nversion='0.2.0'\n"),
                ("takt/src/takt/web/static/index.html", b"<!doctype html>"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
