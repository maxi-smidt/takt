from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from takt.registry.storage import SCHEMA_VERSION, RegistryStore


class RegistryStorageTests(unittest.TestCase):
    def test_never_seen_device_can_receive_first_safe_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = RegistryStore(Path(temporary_directory))
            try:
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id="12345678-1234-1234-1234-123456789abc",
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                job = store.create_job("12345678-1234-1234-1234-123456789abc", "restart_takt")
                self.assertEqual(job["status"], "queued")
            finally:
                store.close()

    def test_newer_database_schema_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            connection = sqlite3.connect(root / "registry.db")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "newer"):
                RegistryStore(root)

    def test_wifi_job_secret_is_encrypted_durable_and_removed_on_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            device_id = "12345678-1234-1234-1234-123456789abc"
            password = "durable-fleet-secret"
            store = RegistryStore(root)
            try:
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id=device_id,
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                with self.assertRaisesRegex(ValueError, "online"):
                    store.create_wifi_job(device_id, "Timing Hall", password)
                store.update_heartbeat(
                    device_id,
                    {
                        "protocol_version": 1,
                        "capabilities": ["leased-jobs"],
                        "poll_seconds": 10,
                    },
                )
                with self.assertRaisesRegex(ValueError, "cannot manage"):
                    store.create_wifi_job(device_id, "Timing Hall", password)
                store.update_heartbeat(
                    device_id,
                    {
                        "protocol_version": 1,
                        "capabilities": ["wifi-profile-v1"],
                        "poll_seconds": 10,
                    },
                )
                job = store.create_wifi_job(device_id, "Timing Hall", password)
                self.assertEqual(job["payload"], {"ssid": "Timing Hall", "priority": 0})
                self.assertNotIn("credential", job)
                self.assertEqual(root.joinpath("registry.db").stat().st_mode & 0o777, 0o600)
                self.assertEqual(root.joinpath("job-secrets.key").stat().st_mode & 0o777, 0o600)
                backup = store.backup_database(label="wifi-secret-test")
                for path in [backup, *root.glob("registry.db*")]:
                    self.assertNotIn(password.encode(), path.read_bytes())

                claimed = store.claim_next_job(device_id, "session-a")
                assert claimed is not None
                self.assertEqual(claimed["credential"], {"password": password})
                lease_id = claimed["lease_id"]
                store.close()

                store = RegistryStore(root)
                claimed_again = store.claim_next_job(device_id, "session-a")
                assert claimed_again is not None
                self.assertEqual(claimed_again["id"], job["id"])
                self.assertEqual(claimed_again["credential"], {"password": password})
                store.update_job(
                    job["id"], device_id, "succeeded", 100, "saved", lease_id=lease_id
                )
                secret_count = store.connection.execute(
                    "SELECT COUNT(*) FROM job_secrets"
                ).fetchone()[0]
                self.assertEqual(secret_count, 0)
            finally:
                store.close()

    def test_missing_duplicate_mirror_blob_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = RegistryStore(root)
            try:
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id="12345678-1234-1234-1234-123456789abc",
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                content = b"SQLite mirror placeholder"
                digest = hashlib.sha256(content).hexdigest()
                first = root / "first.sqlite3"
                first.write_bytes(content)
                store.record_mirror(
                    "12345678-1234-1234-1234-123456789abc",
                    first,
                    digest,
                    len(content),
                    1,
                )
                mirror = store.mirror_path("12345678-1234-1234-1234-123456789abc")
                mirror.unlink()
                replacement = root / "replacement.sqlite3"
                replacement.write_bytes(content)
                store.record_mirror(
                    "12345678-1234-1234-1234-123456789abc",
                    replacement,
                    digest,
                    len(content),
                    1,
                )
                self.assertEqual(mirror.read_bytes(), content)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
