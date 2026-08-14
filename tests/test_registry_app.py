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
                            "capabilities": ["wifi-profile-v1"],
                            "agent_session_id": "session-a",
                            "poll_seconds": 10,
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsNone((await response.json())["job"])

                    wifi_password = "fleet-secret-123"
                    async with client.post(
                        f"{base_url}/api/devices/{device_id}/wifi-networks",
                        json={"ssid": "Timing Hall", "password": wifi_password},
                    ) as response:
                        self.assertEqual(response.status, 403)
                    async with client.post(
                        f"{base_url}/api/devices/{device_id}/wifi-networks",
                        json={"ssid": "Timing Hall", "password": "short"},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 400)
                    async with client.post(
                        f"{base_url}/api/devices/{device_id}/wifi-networks",
                        json={"ssid": "Timing Hall", "password": wifi_password},
                        headers={"X-CSRF-Token": csrf},
                    ) as response:
                        self.assertEqual(response.status, 201)
                        wifi_job = (await response.json())["job"]
                        self.assertEqual(
                            wifi_job["payload"], {"ssid": "Timing Hall", "priority": 0}
                        )
                        self.assertNotIn("credential", wifi_job)
                    async with client.get(f"{base_url}/api/jobs") as response:
                        public_jobs = await response.json()
                        self.assertNotIn(wifi_password, str(public_jobs))
                    backup = store.backup_database(label="wifi-secret-test")
                    self.assertNotIn(wifi_password.encode(), backup.read_bytes())
                    for database_file in data_directory.glob("registry.db*"):
                        self.assertNotIn(wifi_password.encode(), database_file.read_bytes())

                    async with client.post(
                        f"{base_url}/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "capabilities": ["wifi-profile-v1"],
                            "agent_session_id": "session-a",
                            "poll_seconds": 10,
                        },
                        headers=agent_headers,
                    ) as response:
                        claimed_wifi = (await response.json())["job"]
                        self.assertEqual(claimed_wifi["id"], wifi_job["id"])
                        self.assertEqual(claimed_wifi["credential"], {"password": wifi_password})
                        wifi_lease = claimed_wifi["lease_id"]
                    async with client.post(
                        f"{base_url}/agent/jobs/{wifi_job['id']}",
                        json={
                            "status": "succeeded",
                            "progress": 100,
                            "message": "saved",
                            "lease_id": wifi_lease,
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)

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
                        f"{base_url}/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "agent_session_id": "session-a",
                            "poll_seconds": 10,
                            "update_recovery": {
                                "stuck": True,
                                "phase": "activated",
                                "error": "manual repair is required",
                            },
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertNotIn("job", await response.json())
                    unclaimed = store.get_job(job["id"])
                    assert unclaimed is not None
                    self.assertEqual(unclaimed["status"], "queued")
                    self.assertEqual(unclaimed["attempt"], 0)
                    self.assertEqual(
                        store.get_device(device_id)["status"]["update_recovery"]["phase"],
                        "activated",
                    )

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
                        f"{base_url}/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "app_version": "0.2.0",
                            "agent_version": "0.2.0",
                            "health": {"ok": True, "state": "ready", "version": "0.2.0"},
                            "protocol_version": 1,
                            "agent_session_id": "session-a",
                            "poll_seconds": 10,
                        },
                        headers=agent_headers,
                    ) as response:
                        self.assertEqual(response.status, 200)

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
                        self.assertEqual(device["app_version"], "0.2.0")
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


class FleetMaintenanceApiTests(unittest.TestCase):
    DEVICE_ID = "12345678-1234-1234-1234-123456789abc"
    TOKEN = "a" * 64
    PASSWORD = "correct-horse-battery"
    CAPABILITIES = [
        "leased-jobs",
        "service-control-v1",
        "power-control-v1",
        "diagnostics-v1",
        "health-checks-v1",
    ]

    def test_maintenance_api_enforces_authorization_capability_and_redaction(self) -> None:
        asyncio.run(self._exercise())

    async def _exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory) / "registry"
            data_directory.mkdir()
            store = RegistryStore(data_directory)
            runner = web.AppRunner(
                create_registry_app(store, AdminAuth(self.PASSWORD, data_directory))
            )
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            sockets = site._server.sockets  # type: ignore[union-attr]
            base_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                    csrf = await self._login(client, base_url)
                    admin = {"X-CSRF-Token": csrf}
                    agent = await self._enroll(client, base_url, admin)

                    await self._assert_capability_gating(client, base_url, admin, agent)
                    await self._assert_override_rules(client, base_url, admin)
                    job_id = await self._assert_health_report(client, base_url, admin, agent, store)
                    await self._assert_diagnostics(client, base_url, admin, agent, store)
                    await self._assert_unauthenticated_access_is_refused(base_url, job_id)
            finally:
                await runner.cleanup()
                store.close()

    async def _enroll(
        self, client: ClientSession, base_url: str, admin: dict[str, str]
    ) -> dict[str, str]:
        async with client.post(
            f"{base_url}/api/enrollment-codes", json={"label": "Lane 1"}, headers=admin
        ) as response:
            code = (await response.json())["code"]
        async with client.post(
            f"{base_url}/agent/enroll",
            json={
                "enrollment_code": code,
                "device_id": self.DEVICE_ID,
                "name": "Lane 1",
                "hostname": "takt-01",
                "device_token": self.TOKEN,
            },
        ) as response:
            self.assertEqual(response.status, 201)
        return {"X-Device-ID": self.DEVICE_ID, "Authorization": f"Bearer {self.TOKEN}"}

    async def _heartbeat(
        self, client: ClientSession, base_url: str, agent: dict[str, str], capabilities: list[str]
    ) -> None:
        async with client.post(
            f"{base_url}/agent/heartbeat",
            json={
                "health": {"ok": True, "state": "ready"},
                "protocol_version": 1,
                "capabilities": capabilities,
                "agent_session_id": "session-a",
                "poll_seconds": 10,
            },
            headers=agent,
        ) as response:
            self.assertEqual(response.status, 200)

    async def _assert_capability_gating(
        self,
        client: ClientSession,
        base_url: str,
        admin: dict[str, str],
        agent: dict[str, str],
    ) -> None:
        await self._heartbeat(client, base_url, agent, ["leased-jobs"])
        for action in ("reboot_device", "stop_takt", "collect_diagnostics"):
            async with client.post(
                f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
                json={"action": action},
                headers=admin,
            ) as response:
                self.assertEqual(response.status, 400, f"{action} must be refused")
        # A stale agent keeps the baseline actions it has always supported.
        async with client.post(
            f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
            json={"action": "mirror_now"},
            headers=admin,
        ) as response:
            self.assertEqual(response.status, 201)
        await self._heartbeat(client, base_url, agent, self.CAPABILITIES)

    async def _assert_override_rules(
        self, client: ClientSession, base_url: str, admin: dict[str, str]
    ) -> None:
        async with client.post(
            f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
            json={"action": "run_health_checks", "override": True},
            headers=admin,
        ) as response:
            self.assertEqual(response.status, 400, "a safe action cannot be overridden")
        async with client.post(
            f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
            json={"action": "run_health_checks", "override": "yes"},
            headers=admin,
        ) as response:
            self.assertEqual(response.status, 400, "override must be a boolean")

    async def _assert_health_report(
        self,
        client: ClientSession,
        base_url: str,
        admin: dict[str, str],
        agent: dict[str, str],
        store: RegistryStore,
    ) -> str:
        async with client.post(
            f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
            json={"action": "run_health_checks"},
            headers=admin,
        ) as response:
            self.assertEqual(response.status, 201)
            job = (await response.json())["job"]
        claimed = self._claim(store, job["id"])
        async with client.post(
            f"{base_url}/agent/jobs/{job['id']}",
            json={
                "status": "succeeded",
                "progress": 100,
                "message": "done",
                "lease_id": claimed["lease_id"],
                "stage": "succeeded",
                "result": {
                    "checks": [
                        {"id": "disk_space", "label": "Disk", "status": "warn", "detail": "low"},
                        {"id": "bogus", "label": "Bogus", "status": "not-a-status", "detail": ""},
                    ]
                },
            },
            headers=agent,
        ) as response:
            self.assertEqual(response.status, 200)
        async with client.get(f"{base_url}/api/devices") as response:
            device = next(
                item
                for item in (await response.json())["devices"]
                if item["id"] == self.DEVICE_ID
            )
        summary = device["health_checks"]["summary"]
        self.assertIs(summary["healthy"], True)
        self.assertEqual(summary["warn"], 1)
        self.assertEqual(
            [check["id"] for check in device["health_checks"]["checks"]],
            ["disk_space"],
            "checks with an unknown status are dropped",
        )
        return job["id"]

    async def _assert_diagnostics(
        self,
        client: ClientSession,
        base_url: str,
        admin: dict[str, str],
        agent: dict[str, str],
        store: RegistryStore,
    ) -> None:
        async with client.post(
            f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
            json={"action": "collect_diagnostics"},
            headers=admin,
        ) as response:
            self.assertEqual(response.status, 201)
            job = (await response.json())["job"]
        claimed = self._claim(store, job["id"])
        blob = b"diagnostics-bundle-bytes"
        digest = hashlib.sha256(blob).hexdigest()
        upload_url = f"{base_url}/agent/jobs/{job['id']}/artifact"

        async with client.put(
            upload_url,
            data=blob,
            headers={**agent, "X-Job-Lease": "wrong-lease", "X-TAKT-SHA256": digest},
        ) as response:
            self.assertEqual(response.status, 404, "a wrong lease must not upload")
        async with client.put(
            upload_url,
            data=blob,
            headers={**agent, "X-Job-Lease": claimed["lease_id"], "X-TAKT-SHA256": "0" * 64},
        ) as response:
            self.assertEqual(response.status, 400, "a bad checksum must be refused")
        async with client.put(
            upload_url,
            data=blob,
            headers={**agent, "X-Job-Lease": claimed["lease_id"], "X-TAKT-SHA256": digest},
        ) as response:
            self.assertEqual(response.status, 200)
            bundle_id = (await response.json())["diagnostics_id"]

        async with client.get(
            f"{base_url}/api/devices/{self.DEVICE_ID}/diagnostics"
        ) as response:
            listed = (await response.json())["diagnostics"]
            self.assertEqual(len(listed), 1)
            self.assertNotIn("relative_path", listed[0])
        async with client.get(
            f"{base_url}/api/devices/{self.DEVICE_ID}/diagnostics/{bundle_id}"
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), blob)

    async def _assert_unauthenticated_access_is_refused(self, base_url: str, job_id: str) -> None:
        async with ClientSession() as anonymous:
            for url in (
                f"{base_url}/api/devices/{self.DEVICE_ID}/diagnostics",
                f"{base_url}/api/devices/{self.DEVICE_ID}/diagnostics/anything",
            ):
                async with anonymous.get(url) as response:
                    self.assertEqual(response.status, 401, f"{url} must require an admin session")
            async with anonymous.post(
                f"{base_url}/api/devices/{self.DEVICE_ID}/jobs",
                json={"action": "reboot_device"},
            ) as response:
                self.assertEqual(response.status, 401)
            async with anonymous.put(
                f"{base_url}/agent/jobs/{job_id}/artifact", data=b"x"
            ) as response:
                self.assertEqual(response.status, 401)

    def _claim(self, store: RegistryStore, job_id: str) -> dict:
        claimed = store.claim_next_job(self.DEVICE_ID, "session-a")
        while claimed is not None and claimed["id"] != job_id:
            store.update_job(
                claimed["id"],
                self.DEVICE_ID,
                "succeeded",
                100,
                "cleared",
                claimed["lease_id"],
            )
            claimed = store.claim_next_job(self.DEVICE_ID, "session-a")
        assert claimed is not None, f"job {job_id} was never claimable"
        return claimed

    async def _login(self, client: ClientSession, base_url: str) -> str:
        async with client.post(
            f"{base_url}/api/session", json={"password": self.PASSWORD}
        ) as response:
            self.assertEqual(response.status, 200)
        async with client.get(f"{base_url}/api/session") as response:
            return str((await response.json())["csrf_token"])


if __name__ == "__main__":
    unittest.main()
