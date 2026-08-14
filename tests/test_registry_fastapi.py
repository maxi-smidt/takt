from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from takt.registry.api_models import HeartbeatRequest, WifiNetworkRequest
from takt.registry.auth import AdminAuth
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

    def test_pydantic_models_preserve_strict_wifi_and_heartbeat_rules(self) -> None:
        with self.assertRaises(ValueError):
            WifiNetworkRequest(ssid="Lane", password="short")
        with self.assertRaises(ValueError):
            HeartbeatRequest.model_validate({"poll_seconds": {"invalid": True}})
        heartbeat = HeartbeatRequest.model_validate(
            {"future_field": "ignored", "capabilities": ["x"] * 30, "poll_seconds": 10}
        )
        self.assertEqual(heartbeat.payload()["capabilities"], ["x"] * 20)

    def test_index_reports_missing_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = RegistryStore(data_directory, allow_thread_handoff=True)
            auth = AdminAuth("correct-horse-battery", data_directory)
            try:
                with patch(
                    "takt.registry.fastapi_app.STATIC_ROOT",
                    data_directory / "missing",
                ):
                    with TestClient(create_fastapi_app(store, auth)) as client:
                        response = client.get("/")
                self.assertEqual(response.status_code, 500)
                self.assertIn("scripts/build_registry_ui.sh", response.text)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
