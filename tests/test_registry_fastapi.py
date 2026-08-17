from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from takt.registry.api_models import HeartbeatRequest, WifiNetworkRequest
from takt.registry.auth import COOKIE_NAME, AdminAuth
from takt.registry.fastapi_app import create_fastapi_app
from takt.registry.storage import RegistryStore


class FastApiRegistryTests(unittest.TestCase):
    def test_health_openapi_login_and_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with TestClient(create_fastapi_app(store, auth)) as client:
                    health = client.get("/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertTrue(health.json()["ok"])
                    self.assertEqual(health.headers["X-Frame-Options"], "DENY")

                    openapi = client.get("/openapi.json")
                    self.assertEqual(openapi.status_code, 200)
                    openapi_payload = openapi.json()
                    self.assertIn("/agent/heartbeat", openapi_payload["paths"])
                    self.assertIn("LoginRequest", openapi_payload["components"]["schemas"])
                    self.assertFalse(
                        any(
                            parameter["name"] == "csrf"
                            for operation in openapi_payload["paths"].values()
                            for parameters in operation.values()
                            if isinstance(parameters, dict)
                            for parameter in parameters.get("parameters", [])
                        )
                    )

                    unknown_route = client.post(
                        "/api/not-a-route/jobs",
                        content="{}",
                        headers={"Content-Type": "text/plain"},
                    )
                    self.assertEqual(unknown_route.status_code, 404)

                    wrong_content_type = client.post(
                        "/api/session",
                        content='{"password":"correct-horse-battery"}',
                        headers={"Content-Type": "text/plain"},
                    )
                    self.assertEqual(wrong_content_type.status_code, 415)

                    login = client.post(
                        "/api/session",
                        json={"password": "correct-horse-battery"},
                    )
                    self.assertEqual(login.status_code, 200)
                    csrf = client.get("/api/session").json()["csrf_token"]

                    with patch.object(
                        store, "create_job", return_value={"id": "job"}
                    ) as create_job:
                        created_job = client.post(
                            "/api/devices/device-1/jobs",
                            json={
                                "action": "restart_takt",
                                "payload": {},
                                "override": True,
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                    self.assertEqual(created_job.status_code, 201)
                    self.assertTrue(create_job.call_args.kwargs["override"])

                    with patch.object(
                        store, "retry_job", return_value={"id": "retry"}
                    ) as retry_job:
                        retried_job = client.post(
                            "/api/jobs/job-1/retry",
                            json={"override": True},
                            headers={"X-CSRF-Token": csrf},
                        )
                    self.assertEqual(retried_job.status_code, 201)
                    self.assertTrue(retry_job.call_args.kwargs["override"])

                    with patch.object(
                        store, "force_clear_job", return_value={"id": "job-1", "status": "failed"}
                    ) as force_clear_job:
                        force_cleared_job = client.post(
                            "/api/jobs/job-1/force-clear",
                            headers={"X-CSRF-Token": csrf},
                        )
                    self.assertEqual(force_cleared_job.status_code, 200)
                    self.assertEqual(force_clear_job.call_args.args[0], "job-1")
                    self.assertEqual(force_clear_job.call_args.kwargs["actor"], "admin")

                    with patch.object(
                        store, "force_clear_job", side_effect=LookupError("Job does not exist.")
                    ):
                        missing_force_clear = client.post(
                            "/api/jobs/missing/force-clear",
                            headers={"X-CSRF-Token": csrf},
                        )
                    self.assertEqual(missing_force_clear.status_code, 404)

                    release_archive = io.BytesIO()
                    with tarfile.open(fileobj=release_archive, mode="w:gz") as archive:
                        for name, content in (
                            ("takt/pyproject.toml", b"[project]\nversion='0.2.0'\n"),
                            ("takt/src/takt/web/static/index.html", b"<!doctype html>"),
                        ):
                            info = tarfile.TarInfo(name)
                            info.size = len(content)
                            archive.addfile(info, io.BytesIO(content))
                    release = client.post(
                        "/api/releases",
                        data={"version": "0.2.0"},
                        files={
                            "artifact": (
                                "takt-0.2.0.tar.gz",
                                release_archive.getvalue(),
                                "application/gzip",
                            )
                        },
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(release.status_code, 201)

                    malformed_multipart = client.post(
                        "/api/releases",
                        content=b"not-a-multipart-body",
                        headers={
                            "Content-Type": "multipart/form-data; boundary=invalid",
                            "X-CSRF-Token": csrf,
                        },
                    )
                    self.assertEqual(malformed_multipart.status_code, 400)

                    enrollment_response = client.post(
                        "/api/enrollment-codes",
                        json={"label": "Lane 1"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(enrollment_response.status_code, 201)
                    code = enrollment_response.json()["code"]

                    device_id = "12345678-1234-1234-1234-123456789abc"
                    token = "a" * 64
                    enrolled = client.post(
                        "/agent/enroll",
                        json={
                            "enrollment_code": code,
                            "device_id": device_id,
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "device_token": token,
                        },
                    )
                    self.assertEqual(enrolled.status_code, 201)
                    self.assertEqual(enrolled.json()["device_token"], token)

                    agent_headers = {
                        "X-Device-ID": device_id,
                        "Authorization": f"Bearer {token}",
                    }
                    invalid_heartbeat = client.post(
                        "/agent/heartbeat",
                        json={"poll_seconds": {"invalid": True}},
                        headers=agent_headers,
                    )
                    self.assertEqual(invalid_heartbeat.status_code, 400)

                    heartbeat = client.post(
                        "/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "future_field": "ignored",
                        },
                        headers=agent_headers,
                    )
                    self.assertEqual(heartbeat.status_code, 200)
                    self.assertIsNone(heartbeat.json()["job"])
            finally:
                store.close()

    def test_uninstalling_a_release_keeps_its_row_and_blocks_a_download_that_cannot_repair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with TestClient(create_fastapi_app(store, auth)) as client:
                    client.post("/api/session", json={"password": "correct-horse-battery"})
                    csrf = client.get("/api/session").json()["csrf_token"]

                    release_archive = io.BytesIO()
                    with tarfile.open(fileobj=release_archive, mode="w:gz") as archive:
                        for name, content in (
                            ("takt/pyproject.toml", b"[project]\nversion='0.2.0'\n"),
                            ("takt/src/takt/web/static/index.html", b"<!doctype html>"),
                        ):
                            info = tarfile.TarInfo(name)
                            info.size = len(content)
                            archive.addfile(info, io.BytesIO(content))
                    uploaded = client.post(
                        "/api/releases",
                        data={"version": "0.2.0"},
                        files={
                            "artifact": (
                                "takt-0.2.0.tar.gz",
                                release_archive.getvalue(),
                                "application/gzip",
                            )
                        },
                        headers={"X-CSRF-Token": csrf},
                    )
                    release_id = uploaded.json()["release"]["id"]
                    self.assertTrue(uploaded.json()["release"]["installed"])

                    missing_csrf = client.post(f"/api/releases/{release_id}/uninstall")
                    self.assertEqual(missing_csrf.status_code, 403)

                    unknown = client.post(
                        "/api/releases/does-not-exist/uninstall",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(unknown.status_code, 404)

                    uninstalled = client.post(
                        f"/api/releases/{release_id}/uninstall",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(uninstalled.status_code, 200)
                    self.assertFalse(uninstalled.json()["release"]["installed"])

                    listed = client.get("/api/releases").json()["releases"]
                    self.assertFalse(next(r for r in listed if r["id"] == release_id)["installed"])

                    device_id = "12345678-1234-1234-1234-123456789abc"
                    token = "a" * 64
                    code = client.post(
                        "/api/enrollment-codes",
                        json={"label": "Lane 1"},
                        headers={"X-CSRF-Token": csrf},
                    ).json()["code"]
                    client.post(
                        "/agent/enroll",
                        json={
                            "enrollment_code": code,
                            "device_id": device_id,
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "device_token": token,
                        },
                    )
                    agent_headers = {
                        "X-Device-ID": device_id,
                        "Authorization": f"Bearer {token}",
                    }
                    client.post(
                        "/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "app_version": "0.1.0",
                        },
                        headers=agent_headers,
                    )
                    client.post(
                        f"/api/devices/{device_id}/jobs",
                        json={
                            "action": "install_release",
                            "payload": {"release_id": release_id},
                            "override": False,
                        },
                        headers={"X-CSRF-Token": csrf},
                    )
                    claimed = client.post(
                        "/agent/heartbeat",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "app_version": "0.1.0",
                        },
                        headers=agent_headers,
                    ).json()["job"]
                    self.assertIsNotNone(claimed)

                    # This registry image has no bundled artifact to repair the
                    # uninstalled release from, so the download fails clearly
                    # instead of serving a missing file.
                    artifact_response = client.get(
                        f"/agent/jobs/{claimed['id']}/artifact",
                        headers={**agent_headers, "X-Job-Lease": claimed["lease_id"]},
                    )
                    self.assertEqual(artifact_response.status_code, 409)
            finally:
                store.close()

    def test_acknowledge_recovery_endpoint_clears_and_reraises_the_alert(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with TestClient(create_fastapi_app(store, auth)) as client:
                    client.post("/api/session", json={"password": "correct-horse-battery"})
                    csrf = client.get("/api/session").json()["csrf_token"]

                    code = client.post(
                        "/api/enrollment-codes",
                        json={"label": "Lane 1"},
                        headers={"X-CSRF-Token": csrf},
                    ).json()["code"]
                    device_id = "12345678-1234-1234-1234-123456789abc"
                    token = "a" * 64
                    client.post(
                        "/agent/enroll",
                        json={
                            "enrollment_code": code,
                            "device_id": device_id,
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "device_token": token,
                        },
                    )
                    agent_headers = {
                        "X-Device-ID": device_id,
                        "Authorization": f"Bearer {token}",
                    }

                    no_alert_yet = client.post(
                        f"/api/devices/{device_id}/acknowledge-recovery",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(no_alert_yet.status_code, 400)

                    client.post(
                        "/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "update_recovery": {
                                "stuck": True,
                                "phase": "activated",
                                "error": "manual repair is required",
                            },
                        },
                        headers=agent_headers,
                    )
                    devices = client.get("/api/devices").json()["devices"]
                    self.assertTrue(devices[0]["status"]["update_recovery"]["stuck"])

                    missing_csrf = client.post(f"/api/devices/{device_id}/acknowledge-recovery")
                    self.assertEqual(missing_csrf.status_code, 403)

                    acknowledged = client.post(
                        f"/api/devices/{device_id}/acknowledge-recovery",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(acknowledged.status_code, 200)
                    self.assertFalse(acknowledged.json()["device"]["status"]["update_recovery"]["stuck"])

                    # The stale heartbeat state must not silently bring the alert back.
                    client.post(
                        "/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "update_recovery": {
                                "stuck": True,
                                "phase": "activated",
                                "error": "manual repair is required",
                            },
                        },
                        headers=agent_headers,
                    )
                    devices = client.get("/api/devices").json()["devices"]
                    self.assertFalse(devices[0]["status"]["update_recovery"]["stuck"])

                    # Recovery clearing then a fresh failure raises the alert again.
                    client.post(
                        "/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "update_recovery": {"stuck": False},
                        },
                        headers=agent_headers,
                    )
                    client.post(
                        "/agent/status",
                        json={
                            "name": "Lane 1",
                            "hostname": "takt-01",
                            "protocol_version": 1,
                            "poll_seconds": 10,
                            "update_recovery": {
                                "stuck": True,
                                "phase": "activated",
                                "error": "manual repair is required",
                            },
                        },
                        headers=agent_headers,
                    )
                    devices = client.get("/api/devices").json()["devices"]
                    self.assertTrue(devices[0]["status"]["update_recovery"]["stuck"])
            finally:
                store.close()

    def test_pydantic_models_preserve_strict_wifi_and_heartbeat_rules(self) -> None:
        with self.assertRaises(ValueError):
            WifiNetworkRequest(ssid="Lane", password="short")
        with self.assertRaises(ValueError):
            HeartbeatRequest.model_validate({"poll_seconds": {"invalid": True}})
        heartbeat = HeartbeatRequest.model_validate(
            {"future_field": "ignored", "capabilities": ["x"] * 30, "poll_seconds": 10}
        )
        self.assertEqual(heartbeat.payload()["capabilities"], ["x"] * 20)

    def test_reload_keeps_a_valid_legacy_session_and_redirects_an_expired_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with TestClient(create_fastapi_app(store, auth)) as client:
                    login = client.post(
                        "/api/session", json={"password": "correct-horse-battery"}
                    )
                    self.assertEqual(login.status_code, 200)

                    # A page reload probes /api/session first and then fires the
                    # dashboard's data requests; with a still-valid session both
                    # must agree the user is signed in.
                    session_status = client.get("/api/session")
                    self.assertTrue(session_status.json()["authenticated"])
                    for path in ("/api/devices", "/api/releases", "/api/jobs"):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200, path)

                    # A genuinely dead session must be reported identically by
                    # the session probe and by the protected endpoints, using
                    # the same wording the account-based path uses.
                    client.cookies.set(COOKIE_NAME, "not-a-real-session-token")
                    expired_status = client.get("/api/session")
                    self.assertFalse(expired_status.json()["authenticated"])
                    with self.assertLogs("takt.registry.fastapi_app", level="WARNING") as logs:
                        expired_devices = client.get("/api/devices")
                    self.assertEqual(expired_devices.status_code, 401)
                    self.assertEqual(expired_devices.text, "Login required.")
                    self.assertIn("session_verify_401", logs.output[0])
                    self.assertIn("cookie_present=True", logs.output[0])
            finally:
                store.close()

    def test_reload_keeps_a_valid_account_session_and_redirects_an_expired_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            store.accounts.bootstrap_admin("admin", "correct-horse-battery")
            try:
                with TestClient(create_fastapi_app(store, auth)) as client:
                    login = client.post(
                        "/api/session",
                        json={"username": "admin", "password": "correct-horse-battery"},
                    )
                    self.assertEqual(login.status_code, 200)

                    session_status = client.get("/api/session")
                    self.assertTrue(session_status.json()["authenticated"])
                    for path in ("/api/devices", "/api/releases", "/api/jobs"):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200, path)

                    client.cookies.set(COOKIE_NAME, "not-a-real-session-token")
                    expired_status = client.get("/api/session")
                    self.assertFalse(expired_status.json()["authenticated"])
                    with self.assertLogs("takt.registry.fastapi_app", level="WARNING") as logs:
                        expired_devices = client.get("/api/devices")
                    self.assertEqual(expired_devices.status_code, 401)
                    self.assertEqual(expired_devices.text, "Login required.")
                    self.assertIn("session_verify_401", logs.output[0])
                    self.assertIn("cookie_present=True", logs.output[0])

                    client.cookies.delete(COOKIE_NAME)
                    with self.assertLogs("takt.registry.fastapi_app", level="WARNING") as logs:
                        missing_cookie = client.get("/api/devices")
                    self.assertEqual(missing_cookie.status_code, 401)
                    self.assertIn("cookie_present=False", logs.output[0])
            finally:
                store.close()

    def test_index_reports_missing_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with patch(
                    "takt.registry.fastapi_app.STATIC_ROOT",
                    data_directory / "missing",
                ), TestClient(create_fastapi_app(store, auth)) as client:
                    response = client.get("/")
                self.assertEqual(response.status_code, 500)
                self.assertIn("scripts/build_registry_ui.sh", response.text)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
