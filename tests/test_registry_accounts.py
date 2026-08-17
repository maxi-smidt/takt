"""Regression tests for Registry accounts and authoritative run commands."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from takt.domain.duration import Duration
from takt.persistence.run_repository import SQLiteRunRepository
from takt.registry.auth import AdminAuth
from takt.registry.fastapi_app import create_fastapi_app
from takt.registry.storage import RegistryStore


class AccountStoreTests(unittest.TestCase):
    def test_passwords_sessions_and_acl_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RegistryStore(Path(directory), allow_thread_handoff=True)
            try:
                accounts = store.accounts
                admin = accounts.bootstrap_admin("Admin.User", "correct-horse-battery")
                row = store.connection.execute(
                    "SELECT password_hash FROM users WHERE id = ?", (admin["id"],)
                ).fetchone()
                self.assertNotIn("correct-horse-battery", row["password_hash"])
                self.assertEqual(
                    accounts.authenticate("admin.user", "correct-horse-battery")["id"], admin["id"]
                )
                self.assertIsNone(accounts.authenticate("admin.user", "wrong-password"))
                token, metadata = accounts.create_session(admin["id"])
                self.assertEqual(accounts.verify_session(token)["csrf"], metadata["csrf"])
                accounts.revoke_session(token)
                self.assertIsNone(accounts.verify_session(token))
                user = accounts.create_user("runner", "another-correct-password")
                device_id = "12345678-1234-1234-1234-123456789abc"
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id=device_id,
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                self.assertEqual(
                    accounts.grant_access(user["id"], device_id, "write")["access_level"], "write"
                )
                self.assertEqual(accounts.access_level(user["id"], device_id), "write")
                self.assertTrue(accounts.revoke_access(user["id"], device_id))
                self.assertIsNone(accounts.access_level(user["id"], device_id))
            finally:
                store.close()

    def test_disabled_user_and_last_admin_guards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RegistryStore(Path(directory))
            try:
                accounts = store.accounts
                admin = accounts.bootstrap_admin("admin", "correct-horse-battery")
                with self.assertRaises(ValueError):
                    accounts.set_user_state(admin["id"], disabled=True)
                second_admin = accounts.create_user(
                    "second-admin", "second-correct-password", is_admin=True
                )
                accounts.set_user_state(second_admin["id"], disabled=True)
                accounts.set_user_state(second_admin["id"], is_admin=False)
                user = accounts.create_user("runner", "another-correct-password")
                token, _ = accounts.create_session(user["id"])
                accounts.set_user_state(user["id"], disabled=True)
                self.assertIsNone(accounts.verify_session(token))
                with self.assertRaises(ValueError):
                    accounts.create_session(user["id"])
            finally:
                store.close()

    def test_account_login_and_admin_user_creation_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RegistryStore(root, allow_thread_handoff=True)
            store.accounts.bootstrap_admin("admin", "correct-horse-battery")
            try:
                with TestClient(
                    create_fastapi_app(store, AdminAuth("correct-horse-battery", root))
                ) as client:
                    login = client.post(
                        "/api/session",
                        json={"username": "admin", "password": "correct-horse-battery"},
                    )
                    self.assertEqual(login.status_code, 200)
                    csrf = client.get("/api/session").json()["csrf_token"]
                    created = client.post(
                        "/api/admin/users",
                        json={"username": "runner"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(created.status_code, 201)
                    self.assertTrue(created.json()["temporary_password"])
                    malformed_patch = client.patch(
                        f"/api/admin/users/{created.json()['user']['id']}",
                        content="{}",
                        headers={
                            "Content-Type": "text/plain",
                            "X-CSRF-Token": csrf,
                        },
                    )
                    self.assertEqual(malformed_patch.status_code, 415)
                    temporary_password = created.json()["temporary_password"]
                    self.assertEqual(client.get("/api/admin/users").status_code, 200)
                    self.assertEqual(
                        client.delete(
                            "/api/session", headers={"X-CSRF-Token": csrf}
                        ).status_code,
                        200,
                    )
                    runner_login = client.post(
                        "/api/session",
                        json={"username": "runner", "password": temporary_password},
                    )
                    self.assertEqual(runner_login.status_code, 200)
                    runner_session = client.get("/api/session").json()
                    self.assertTrue(runner_session["user"]["must_change_password"])
                    self.assertEqual(client.get("/api/portal/devices").status_code, 403)
                    password_change = client.post(
                        "/api/session/password",
                        json={
                            "current_password": temporary_password,
                            "new_password": "runner-new-password",
                        },
                        headers={"X-CSRF-Token": runner_session["csrf_token"]},
                    )
                    self.assertEqual(password_change.status_code, 200)
                    self.assertFalse(client.get("/api/session").json()["user"]["must_change_password"])
            finally:
                store.close()

    def test_admin_can_grant_several_devices_change_level_and_revoke_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RegistryStore(root, allow_thread_handoff=True)
            store.accounts.bootstrap_admin("admin", "correct-horse-battery")
            device_ids = [
                "12345678-1234-1234-1234-123456789ab1",
                "12345678-1234-1234-1234-123456789ab2",
            ]
            for index, device_id in enumerate(device_ids):
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id=device_id,
                    name=f"Lane {index + 1}",
                    hostname=f"takt-0{index + 1}",
                    token="a" * 64,
                )
            try:
                with TestClient(
                    create_fastapi_app(store, AdminAuth("correct-horse-battery", root))
                ) as client:
                    client.post(
                        "/api/session",
                        json={"username": "admin", "password": "correct-horse-battery"},
                    )
                    csrf = client.get("/api/session").json()["csrf_token"]
                    created = client.post(
                        "/api/admin/users",
                        json={"username": "operator"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    user_id = created.json()["user"]["id"]

                    granted_read = client.put(
                        f"/api/admin/users/{user_id}/devices/{device_ids[0]}",
                        json={"access": "read"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(granted_read.status_code, 200)
                    granted_write = client.put(
                        f"/api/admin/users/{user_id}/devices/{device_ids[1]}",
                        json={"access": "write"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(granted_write.status_code, 200)

                    users_after_grant = {
                        item["device_id"]: item["access_level"]
                        for item in client.get("/api/admin/users").json()["users"][1]["access"]
                    }
                    self.assertEqual(
                        users_after_grant,
                        {device_ids[0]: "read", device_ids[1]: "write"},
                    )

                    changed_level = client.put(
                        f"/api/admin/users/{user_id}/devices/{device_ids[0]}",
                        json={"access": "write"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(changed_level.status_code, 200)
                    self.assertEqual(
                        store.accounts.access_level(user_id, device_ids[0]), "write"
                    )

                    revoked = client.delete(
                        f"/api/admin/users/{user_id}/devices/{device_ids[1]}",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(revoked.status_code, 200)
                    self.assertIsNone(store.accounts.access_level(user_id, device_ids[1]))

                    missing_revoke = client.delete(
                        f"/api/admin/users/{user_id}/devices/{device_ids[1]}",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(missing_revoke.status_code, 404)

                    events = {
                        row["event"]
                        for row in store.connection.execute(
                            "SELECT event FROM audit_events WHERE target_user_id = ?", (user_id,)
                        ).fetchall()
                    }
                    self.assertIn("device_access_changed", events)
                    self.assertIn("device_access_revoked", events)
            finally:
                store.close()

    def test_portal_summary_ignores_keyset_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RegistryStore(root, allow_thread_handoff=True)
            store.accounts.bootstrap_admin("admin", "correct-horse-battery")
            device_id = "12345678-1234-1234-1234-123456789abc"
            code = store.create_enrollment_code()
            store.enroll_device(
                code=code,
                device_id=device_id,
                name="Lane 1",
                hostname="takt-01",
                token="a" * 64,
            )
            mirror_repository = SQLiteRunRepository(root / "mirror.db")
            try:
                for offset in (0, 1):
                    start = datetime(2026, 8, 5 + offset, 9, 0, tzinfo=UTC)
                    mirror_repository.create_and_save(
                        started_at=start,
                        stopped_at=start + timedelta(seconds=80),
                        saved_at=start + timedelta(seconds=81),
                        actual_time=Duration(80_000 + offset),
                        added_time=Duration(0),
                    )
            finally:
                mirror_repository.close()
            mirror_path = root / "mirror.db"
            digest = hashlib.sha256(mirror_path.read_bytes()).hexdigest()
            store.record_mirror(
                device_id, mirror_path, digest, mirror_path.stat().st_size, run_count=2
            )
            try:
                with TestClient(
                    create_fastapi_app(store, AdminAuth("correct-horse-battery", root))
                ) as client:
                    self.assertEqual(
                        client.post(
                            "/api/session",
                            json={"username": "admin", "password": "correct-horse-battery"},
                        ).status_code,
                        200,
                    )
                    first = client.get(
                        f"/api/portal/devices/{device_id}/runs?limit=1"
                    ).json()
                    self.assertEqual(first["summary"]["count"], 2)
                    second = client.get(
                        f"/api/portal/devices/{device_id}/runs?limit=1"
                        f"&cursor={first['next_cursor']}"
                    ).json()
                    self.assertEqual(second["summary"]["count"], 2)
            finally:
                store.close()

    def test_portal_devices_reflects_read_write_and_no_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RegistryStore(root, allow_thread_handoff=True)
            store.accounts.bootstrap_admin("admin", "correct-horse-battery")
            read_device_id = "12345678-1234-1234-1234-123456789ab1"
            write_device_id = "12345678-1234-1234-1234-123456789ab2"
            unmirrored_device_id = "12345678-1234-1234-1234-123456789ab3"
            for index, device_id in enumerate(
                (read_device_id, write_device_id, unmirrored_device_id)
            ):
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id=device_id,
                    name=f"Lane {index + 1}",
                    hostname=f"takt-0{index + 1}",
                    token="a" * 64,
                )

            def create_logged_in_user(
                client: TestClient, csrf: str, username: str
            ) -> tuple[str, dict[str, Any]]:
                created = client.post(
                    "/api/admin/users",
                    json={"username": username},
                    headers={"X-CSRF-Token": csrf},
                )
                self.assertEqual(created.status_code, 201)
                return created.json()["user"]["id"], created.json()

            try:
                with TestClient(
                    create_fastapi_app(store, AdminAuth("correct-horse-battery", root))
                ) as client:
                    client.post(
                        "/api/session",
                        json={"username": "admin", "password": "correct-horse-battery"},
                    )
                    admin_csrf = client.get("/api/session").json()["csrf_token"]

                    user_id, created = create_logged_in_user(client, admin_csrf, "operator")
                    temporary_password = created["temporary_password"]
                    self.assertEqual(
                        client.put(
                            f"/api/admin/users/{user_id}/devices/{read_device_id}",
                            json={"access": "read"},
                            headers={"X-CSRF-Token": admin_csrf},
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        client.put(
                            f"/api/admin/users/{user_id}/devices/{write_device_id}",
                            json={"access": "write"},
                            headers={"X-CSRF-Token": admin_csrf},
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        client.put(
                            f"/api/admin/users/{user_id}/devices/{unmirrored_device_id}",
                            json={"access": "read"},
                            headers={"X-CSRF-Token": admin_csrf},
                        ).status_code,
                        200,
                    )

                    _no_access_id, no_access_created = create_logged_in_user(
                        client, admin_csrf, "outsider"
                    )
                    no_access_password = no_access_created["temporary_password"]

                    client.delete("/api/session", headers={"X-CSRF-Token": admin_csrf})
                    client.post(
                        "/api/session",
                        json={"username": "operator", "password": temporary_password},
                    )
                    operator_session = client.get("/api/session").json()
                    client.post(
                        "/api/session/password",
                        json={
                            "current_password": temporary_password,
                            "new_password": "operator-new-password",
                        },
                        headers={"X-CSRF-Token": operator_session["csrf_token"]},
                    )

                    devices = {
                        item["id"]: item
                        for item in client.get("/api/portal/devices").json()["devices"]
                    }
                    self.assertEqual(
                        set(devices), {read_device_id, write_device_id, unmirrored_device_id}
                    )
                    self.assertEqual(devices[read_device_id]["access"], "read")
                    self.assertEqual(devices[write_device_id]["access"], "write")
                    self.assertEqual(devices[unmirrored_device_id]["access"], "read")
                    self.assertEqual(devices[unmirrored_device_id]["mirror_state"], "missing")
                    self.assertIsNone(devices[unmirrored_device_id]["last_mirrored_at"])

                    client.delete(
                        "/api/session", headers={"X-CSRF-Token": operator_session["csrf_token"]}
                    )
                    client.post(
                        "/api/session",
                        json={"username": "outsider", "password": no_access_password},
                    )
                    outsider_session = client.get("/api/session").json()
                    client.post(
                        "/api/session/password",
                        json={
                            "current_password": no_access_password,
                            "new_password": "outsider-new-password",
                        },
                        headers={"X-CSRF-Token": outsider_session["csrf_token"]},
                    )
                    self.assertEqual(
                        client.get("/api/portal/devices").json()["devices"], []
                    )
            finally:
                store.close()


class RemoteCurationTests(unittest.TestCase):
    def test_curation_is_idempotent_and_rejects_stale_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRunRepository(Path(directory) / "runs.db")
            try:
                start = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
                run = repository.create_and_save(
                    started_at=start,
                    stopped_at=start + timedelta(seconds=80),
                    saved_at=start + timedelta(seconds=81),
                    actual_time=Duration(80_000),
                    added_time=Duration(10_000),
                )
                expected = repository.connection.execute(
                    "SELECT updated_at FROM runs WHERE id = ?", (run.id,)
                ).fetchone()[0]
                result = repository.apply_remote_curation(
                    command_id="cmd-1",
                    operation="adjust_added_time",
                    run_id=run.id,
                    expected_updated_at=expected,
                    desired_added_time_ms=5_000,
                )
                self.assertEqual(
                    repository.apply_remote_curation(
                        command_id="cmd-1",
                        operation="adjust_added_time",
                        run_id=run.id,
                        expected_updated_at=expected,
                        desired_added_time_ms=5_000,
                    ),
                    result,
                )
                self.assertEqual(repository.get_run(run.id).added_time, Duration(5_000))
                with self.assertRaisesRegex(ValueError, "changed"):
                    repository.apply_remote_curation(
                        command_id="cmd-2",
                        operation="delete",
                        run_id=run.id,
                        expected_updated_at=expected,
                    )
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
