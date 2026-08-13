from __future__ import annotations

import hashlib
import shutil
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

    def test_v7_registry_upgrades_before_creating_the_active_job_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            connection = sqlite3.connect(root / "registry.db")
            connection.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
                "action TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', "
                "status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, "
                "message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, claimed_at TEXT, completed_at TEXT, "
                "attempt INTEGER NOT NULL DEFAULT 0, lease_id TEXT, "
                "lease_expires_at TEXT, lease_owner_session TEXT)"
            )
            connection.execute("PRAGMA user_version = 7")
            connection.close()
            store = RegistryStore(root)
            try:
                columns = {row[1] for row in store.connection.execute("PRAGMA table_info(jobs)")}
                self.assertTrue({"stage", "target_version", "cancel_requested"} <= columns)
                index = store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND name = 'idx_jobs_one_active_disruptive_operation'"
                ).fetchone()
                self.assertIsNotNone(index)
            finally:
                store.close()

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
                queued = store.create_wifi_job(device_id, "Timing Hall", password)
                store.cancel_job(queued["id"])
                secret_count = store.connection.execute(
                    "SELECT COUNT(*) FROM job_secrets"
                ).fetchone()[0]
                self.assertEqual(secret_count, 0)
                with self.assertRaisesRegex(ValueError, "cannot be retried"):
                    store.retry_job(queued["id"])
            finally:
                store.close()

    def test_backup_without_job_secret_key_starts_and_fails_only_affected_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            restored = root / "restored"
            device_id = "12345678-1234-1234-1234-123456789abc"
            store = RegistryStore(source)
            try:
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code,
                    device_id=device_id,
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                store.update_heartbeat(
                    device_id,
                    {
                        "protocol_version": 1,
                        "capabilities": ["wifi-profile-v1"],
                        "poll_seconds": 10,
                    },
                )
                job = store.create_wifi_job(device_id, "Timing Hall", "durable-secret")
                backup = store.backup_database(label="restore-test")
            finally:
                store.close()

            restored.mkdir()
            shutil.copy2(backup, restored / "registry.db")
            restored_store = RegistryStore(restored)
            try:
                self.assertTrue(restored_store.health()["ok"])
                self.assertIsNone(restored_store.claim_next_job(device_id, "session-a"))
                failed = restored_store.get_job(job["id"])
                assert failed is not None
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["message"], "Stored Wi-Fi credential is unavailable")
                secret_count = restored_store.connection.execute(
                    "SELECT COUNT(*) FROM job_secrets"
                ).fetchone()[0]
                self.assertEqual(secret_count, 0)
            finally:
                restored_store.close()

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

    def test_pruning_mirror_snapshots_removes_every_expired_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            device_id = "12345678-1234-1234-1234-123456789abc"
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
                rows = []
                for index in range(2):
                    relative_path = Path("mirrors") / device_id / f"expired-{index}.sqlite3"
                    blob = root / relative_path
                    blob.parent.mkdir(parents=True, exist_ok=True)
                    blob.write_bytes(f"snapshot-{index}".encode())
                    rows.append(
                        (
                            f"snapshot-{index}",
                            device_id,
                            f"2020-01-0{index + 1}T00:00:00+00:00",
                            f"{index + 1:064x}",
                            10,
                            index,
                            str(relative_path),
                        )
                    )
                store.connection.executemany(
                    "INSERT INTO mirror_snapshots "
                    "(id, device_id, received_at, sha256, size, run_count, relative_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                store._prune_mirror_snapshots(device_id, recent=0, daily=0)
                self.assertEqual(
                    store.connection.execute("SELECT COUNT(*) FROM mirror_snapshots").fetchone()[0],
                    0,
                )
                self.assertFalse((root / rows[0][-1]).exists())
                self.assertFalse((root / rows[1][-1]).exists())
            finally:
                store.close()

    def test_install_job_is_idempotent_cancellable_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            device_id = "12345678-1234-1234-1234-123456789abc"
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
                store.update_heartbeat(
                    device_id, {"protocol_version": 1, "app_version": "0.1.0", "poll_seconds": 10}
                )
                source = root / "release.tar.gz"
                source.write_bytes(b"release")
                release = store.add_release(
                    version="0.2.0",
                    filename=source.name,
                    sha256=hashlib.sha256(b"release").hexdigest(),
                    size=source.stat().st_size,
                    source=source,
                )
                first = store.create_job(
                    device_id, "install_release", {"release_id": release["id"]}
                )
                duplicate = store.create_job(
                    device_id, "install_release", {"release_id": release["id"]}
                )
                self.assertEqual(duplicate["id"], first["id"])
                self.assertTrue(duplicate["reused"])
                self.assertEqual(first["current_version"], "0.1.0")
                self.assertEqual(first["target_version"], "0.2.0")

                claimed = store.claim_next_job(device_id, "session-a")
                assert claimed is not None
                store.update_job(
                    first["id"],
                    device_id,
                    "running",
                    10,
                    "Downloading",
                    claimed["lease_id"],
                    stage="downloading",
                    bytes_downloaded=1,
                    bytes_total=release["size"],
                )
                requested = store.cancel_job(first["id"])
                self.assertTrue(requested["cancel_requested"])
                cancelled = store.update_job(
                    first["id"],
                    device_id,
                    "cancelled",
                    100,
                    "Cancelled",
                    claimed["lease_id"],
                    stage="cancelled",
                )
                self.assertEqual(cancelled["status"], "cancelled")
                retry = store.retry_job(first["id"])
                self.assertEqual(retry["retry_of"], first["id"])
                self.assertEqual(retry["stage"], "queued")
                self.assertGreaterEqual(len(store.list_job_events(first["id"])), 3)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
