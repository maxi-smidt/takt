from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from takt.fleet_actions import DISRUPTIVE_ACTIONS
from takt.registry.storage import SCHEMA_VERSION, RegistryStore, utc_iso, utc_now


class RegistryStorageTests(unittest.TestCase):
    def test_concurrent_requests_do_not_corrupt_the_shared_connection(self) -> None:
        # Regression test for #99: RegistryStore used to hand one raw
        # sqlite3.Connection to every FastAPI threadpool worker, which
        # corrupted its cursor/transaction state under real concurrency
        # (TypeError/InterfaceError/OperationalError, and spurious 401s).
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = RegistryStore(Path(temporary_directory), allow_thread_handoff=True)
            try:
                user = store.accounts.create_user(
                    "admin", "supersecretpassword", is_admin=True
                )
                token, _ = store.accounts.create_session(user["id"])
                device_id = "12345678-1234-1234-1234-123456789abc"
                code = store.create_enrollment_code()
                store.enroll_device(
                    code=code, device_id=device_id, name="Lane 1", hostname="takt-01"
                )
                store.update_heartbeat(
                    device_id,
                    {"protocol_version": 1, "poll_seconds": 10, "capabilities": ["leased-jobs"]},
                )

                errors: list[BaseException] = []

                def hammer() -> None:
                    try:
                        for _ in range(100):
                            self.assertIsNotNone(store.accounts.verify_session(token))
                            store.list_devices()
                            store.list_jobs()
                            store.accounts.has_users()
                    except BaseException as error:
                        errors.append(error)

                threads = [threading.Thread(target=hammer) for _ in range(12)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(errors, [])
            finally:
                store.close()

    def test_never_seen_device_cannot_receive_a_job(self) -> None:
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
                with self.assertRaisesRegex(ValueError, "online"):
                    store.create_job("12345678-1234-1234-1234-123456789abc", "restart_takt")
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
                with store.engine.connect() as conn:
                    columns = {
                        row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")
                    }
                    self.assertTrue({"stage", "target_version", "cancel_requested"} <= columns)
                    index = conn.exec_driver_sql(
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
                with store.engine.connect() as conn:
                    secret_count = conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM job_secrets"
                    ).fetchone()[0]
                self.assertEqual(secret_count, 0)
                queued = store.create_wifi_job(device_id, "Timing Hall", password)
                store.cancel_job(queued["id"])
                with store.engine.connect() as conn:
                    secret_count = conn.exec_driver_sql(
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
                with restored_store.engine.connect() as conn:
                    secret_count = conn.exec_driver_sql(
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
                with store.engine.begin() as conn:
                    conn.exec_driver_sql(
                        "INSERT INTO mirror_snapshots "
                        "(id, device_id, received_at, sha256, size, run_count, relative_path) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                store._prune_mirror_snapshots(device_id, recent=0, daily=0)
                with store.engine.connect() as conn:
                    count = conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM mirror_snapshots"
                    ).fetchone()[0]
                self.assertEqual(count, 0)
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

    def test_force_clear_job_unblocks_a_wedged_late_stage_install(self) -> None:
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
                job = store.create_job(device_id, "install_release", {"release_id": release["id"]})
                claimed = store.claim_next_job(device_id, "session-a")
                assert claimed is not None
                store.update_job(
                    job["id"],
                    device_id,
                    "running",
                    90,
                    "Activating",
                    claimed["lease_id"],
                    stage="activating",
                )

                with self.assertRaises(ValueError):
                    store.cancel_job(job["id"])
                with self.assertRaises(ValueError):
                    store.create_job(device_id, "restart_takt", {})

                cleared = store.force_clear_job(job["id"], actor="admin")
                self.assertEqual(cleared["status"], "failed")
                self.assertEqual(cleared["stage"], "intervention_required")
                self.assertIsNone(cleared["lease_id"])

                # Already-terminal jobs are left alone (idempotent).
                reclear = store.force_clear_job(job["id"], actor="admin")
                self.assertEqual(reclear["status"], "failed")

                # The device's queue is unblocked for a new disruptive job.
                next_job = store.create_job(device_id, "restart_takt", {})
                self.assertNotEqual(next_job["id"], job["id"])
                self.assertGreaterEqual(len(store.list_job_events(job["id"])), 3)
            finally:
                store.close()


class FleetMaintenanceStorageTests(unittest.TestCase):
    DEVICE_ID = "12345678-1234-1234-1234-123456789abc"

    def _store(self, root: Path, *, capabilities: list[str] | None = None) -> RegistryStore:
        store = RegistryStore(root)
        code = store.create_enrollment_code()
        store.enroll_device(
            code=code,
            device_id=self.DEVICE_ID,
            name="Lane 1",
            hostname="takt-01",
            token="a" * 64,
        )
        if capabilities is not None:
            store.update_heartbeat(
                self.DEVICE_ID,
                {
                    "protocol_version": 1,
                    "poll_seconds": 10,
                    "capabilities": capabilities,
                    "health": {"ok": True, "state": "ready"},
                },
            )
        return store

    def test_v8_database_rebuilds_the_disruptive_action_index(self) -> None:
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
            connection.execute(
                "CREATE UNIQUE INDEX idx_jobs_one_active_disruptive_operation ON jobs(device_id) "
                "WHERE action IN ('install_release', 'restart_takt') "
                "AND status IN ('queued', 'claimed', 'running')"
            )
            connection.execute("PRAGMA user_version = 8")
            connection.commit()
            connection.close()
            store = RegistryStore(root)
            try:
                with store.engine.connect() as conn:
                    self.assertEqual(
                        int(conn.exec_driver_sql("PRAGMA user_version").fetchone()[0]),
                        SCHEMA_VERSION,
                    )
                    self.assertTrue(list((root / "backups").glob("*pre-migration-v8.sqlite3")))
                    index_sql = str(
                        conn.exec_driver_sql(
                            "SELECT sql FROM sqlite_master WHERE type = 'index' "
                            "AND name = 'idx_jobs_one_active_disruptive_operation'"
                        ).fetchone()[0]
                    )
                    for action in DISRUPTIVE_ACTIONS:
                        self.assertIn(f"'{action}'", index_sql)
                    columns = {
                        row[1] for row in conn.exec_driver_sql("PRAGMA table_info(devices)")
                    }
                    self.assertIn("health_checks_json", columns)
                    self.assertIsNotNone(
                        conn.exec_driver_sql(
                            "SELECT name FROM sqlite_master WHERE name = 'diagnostics'"
                        ).fetchone()
                    )
            finally:
                store.close()

    def test_capability_gate_blocks_only_unsupported_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                # A stale agent keeps every baseline action it has always had...
                self.assertEqual(
                    store.create_job(self.DEVICE_ID, "mirror_now")["status"], "queued"
                )
                # ...but is refused the actions it cannot perform.
                for action in ("reboot_device", "stop_takt", "collect_diagnostics"):
                    with self.subTest(action=action), self.assertRaises(ValueError) as caught:
                        store.create_job(self.DEVICE_ID, action)
                    self.assertIn("does not support", str(caught.exception))
            finally:
                store.close()

    def test_override_is_recorded_only_for_overridable_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(
                Path(temporary_directory),
                capabilities=["leased-jobs", "power-control-v1", "health-checks-v1"],
            )
            try:
                job = store.create_job(self.DEVICE_ID, "reboot_device", override=True)
                self.assertTrue(job["payload"]["override"])
                untrusted = store.create_job(self.DEVICE_ID, "mirror_now", {"override": True})
                self.assertNotIn("override", untrusted["payload"])
                with store.engine.connect() as conn:
                    audited = conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM audit_events WHERE event = 'job_created'"
                    ).fetchone()[0]
                self.assertGreaterEqual(audited, 1, "job creation should be audited")
                with self.assertRaises(ValueError):
                    store.create_job(self.DEVICE_ID, "run_health_checks", override=True)
            finally:
                store.close()

    def test_retry_requires_fresh_override_consent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(
                Path(temporary_directory), capabilities=["leased-jobs", "power-control-v1"]
            )
            try:
                original = store.create_job(self.DEVICE_ID, "reboot_device", override=True)
                claimed = store.claim_next_job(self.DEVICE_ID, "session-a")
                assert claimed is not None
                store.update_job(
                    original["id"], self.DEVICE_ID, "failed", 100, "helper refused",
                    claimed["lease_id"], stage="failed",
                )
                retry = store.retry_job(original["id"])
                self.assertNotIn("override", retry["payload"])
                claimed_retry = store.claim_next_job(self.DEVICE_ID, "session-b")
                assert claimed_retry is not None
                store.update_job(
                    retry["id"], self.DEVICE_ID, "failed", 100, "helper refused",
                    claimed_retry["lease_id"], stage="failed",
                )
                explicit = store.retry_job(retry["id"], override=True)
                self.assertTrue(explicit["payload"]["override"])
            finally:
                store.close()


    def test_conflicting_disruptive_jobs_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(
                Path(temporary_directory),
                capabilities=["leased-jobs", "power-control-v1", "service-control-v1"],
            )
            try:
                store.create_job(self.DEVICE_ID, "reboot_device")
                with self.assertRaisesRegex(ValueError, "already queued"):
                    store.create_job(self.DEVICE_ID, "stop_takt")
                # A non-disruptive action is still allowed alongside it.
                store.update_heartbeat(
                    self.DEVICE_ID,
                    {"protocol_version": 1, "poll_seconds": 10, "capabilities": ["leased-jobs"]},
                )
                self.assertEqual(
                    store.create_job(self.DEVICE_ID, "mirror_now")["status"], "queued"
                )
            finally:
                store.close()

    def test_expired_power_lease_fails_instead_of_rebooting_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(
                Path(temporary_directory), capabilities=["leased-jobs", "power-control-v1"]
            )
            try:
                job = store.create_job(self.DEVICE_ID, "reboot_device")
                claimed = store.claim_next_job(self.DEVICE_ID, "session-a")
                assert claimed is not None
                self.assertEqual(claimed["id"], job["id"])
                # Simulate the agent dying mid-reboot without renewing its lease.
                with store.engine.begin() as conn:
                    conn.exec_driver_sql(
                        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                        (utc_iso(utc_now() - timedelta(seconds=1)), job["id"]),
                    )
                following = store.claim_next_job(self.DEVICE_ID, "session-b")
                self.assertIsNone(following, "a reboot must never be requeued and re-fired")
                final = store.get_job(job["id"])
                assert final is not None
                self.assertEqual(final["status"], "failed")
                self.assertIn("did not confirm", final["message"])
            finally:
                store.close()

    def test_expired_non_power_lease_is_still_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                job = store.create_job(self.DEVICE_ID, "mirror_now")
                store.claim_next_job(self.DEVICE_ID, "session-a")
                with store.engine.begin() as conn:
                    conn.exec_driver_sql(
                        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                        (utc_iso(utc_now() - timedelta(seconds=1)), job["id"]),
                    )
                reclaimed = store.claim_next_job(self.DEVICE_ID, "session-b")
                assert reclaimed is not None
                self.assertEqual(reclaimed["id"], job["id"])
            finally:
                store.close()

    def test_fleet_wide_sweep_resolves_a_stuck_job_without_the_device_reconnecting(self) -> None:
        # Regression coverage: a device that goes offline for good mid-job (bricked,
        # decommissioned, powered off) never calls claim_next_job again, so the
        # per-device lease-expiry check inside it would never run for that job.
        # The periodic fleet-wide sweep must resolve it anyway.
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                job = store.create_job(self.DEVICE_ID, "mirror_now")
                store.claim_next_job(self.DEVICE_ID, "session-a")
                with store.engine.begin() as conn:
                    conn.exec_driver_sql(
                        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                        (utc_iso(utc_now() - timedelta(seconds=1)), job["id"]),
                    )
                store.expire_stale_leased_jobs()
                requeued = store.get_job(job["id"])
                assert requeued is not None
                self.assertEqual(requeued["status"], "queued")
            finally:
                store.close()

    def test_fleet_wide_sweep_fails_a_stuck_power_job_without_the_device_reconnecting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(
                Path(temporary_directory), capabilities=["leased-jobs", "power-control-v1"]
            )
            try:
                job = store.create_job(self.DEVICE_ID, "reboot_device")
                store.claim_next_job(self.DEVICE_ID, "session-a")
                with store.engine.begin() as conn:
                    conn.exec_driver_sql(
                        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                        (utc_iso(utc_now() - timedelta(seconds=1)), job["id"]),
                    )
                store.sweep_stale_jobs()
                failed = store.get_job(job["id"])
                assert failed is not None
                self.assertEqual(failed["status"], "failed")
                self.assertIn("did not confirm", failed["message"])
            finally:
                store.close()

    def test_delete_job_removes_a_completed_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                job = store.create_job(self.DEVICE_ID, "mirror_now")
                cleared = store.force_clear_job(job["id"], actor="admin")
                self.assertEqual(cleared["status"], "failed")
                store.delete_job(job["id"])
                self.assertIsNone(store.get_job(job["id"]))
                self.assertEqual(store.list_job_events(job["id"]), [])
                self.assertNotIn(job["id"], [item["id"] for item in store.list_jobs()])
            finally:
                store.close()

    def test_delete_job_refuses_an_active_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                job = store.create_job(self.DEVICE_ID, "mirror_now")
                with self.assertRaisesRegex(ValueError, "Only a completed job"):
                    store.delete_job(job["id"])
                self.assertIsNotNone(store.get_job(job["id"]))
            finally:
                store.close()

    def test_delete_job_missing_job_raises_lookup_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["leased-jobs"])
            try:
                with self.assertRaises(LookupError):
                    store.delete_job("does-not-exist")
            finally:
                store.close()

    def test_diagnostics_bundles_are_stored_listed_and_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = self._store(root, capabilities=["leased-jobs", "diagnostics-v1"])
            try:
                stored_paths = []
                for index in range(7):
                    job = store.create_job(self.DEVICE_ID, "collect_diagnostics")
                    source = root / f"bundle-{index}.tar.gz"
                    source.write_bytes(f"bundle-{index}".encode())
                    bundle = store.record_diagnostics(
                        self.DEVICE_ID,
                        job["id"],
                        source,
                        hashlib.sha256(f"bundle-{index}".encode()).hexdigest(),
                        source.stat().st_size if source.exists() else 9,
                    )
                    stored_paths.append(root / bundle["relative_path"])
                    claimed = store.claim_next_job(self.DEVICE_ID, "session-a")
                    assert claimed is not None
                    store.update_job(
                        job["id"], self.DEVICE_ID, "succeeded", 100, "done", claimed["lease_id"]
                    )
                listed = store.list_diagnostics(self.DEVICE_ID)
                self.assertEqual(len(listed), 5, "only the newest five bundles are retained")
                self.assertFalse(
                    stored_paths[0].exists(), "pruned bundle blobs must be deleted from disk"
                )
                self.assertTrue(stored_paths[-1].exists())
                self.assertNotIn("relative_path", listed[0], "listings must not leak paths")
            finally:
                store.close()

    def test_health_report_is_stored_on_the_device(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory), capabilities=["health-checks-v1"])
            try:
                report = {"schema": 1, "summary": {"healthy": False, "fail": 1}, "checks": []}
                store.record_health_checks(self.DEVICE_ID, report)
                device = store.get_device(self.DEVICE_ID)
                assert device is not None
                self.assertEqual(device["health_checks"]["summary"]["fail"], 1)
            finally:
                store.close()

    def _report_stuck_recovery(self, store: RegistryStore, *, phase: str = "activated") -> None:
        store.update_heartbeat(
            self.DEVICE_ID,
            {
                "protocol_version": 1,
                "poll_seconds": 10,
                "update_recovery": {
                    "stuck": True,
                    "phase": phase,
                    "error": "manual repair is required",
                },
            },
        )

    def test_acknowledging_update_recovery_clears_the_alert_until_it_recurs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self._store(Path(temporary_directory))
            try:
                with self.assertRaisesRegex(ValueError, "no active"):
                    store.acknowledge_update_recovery(self.DEVICE_ID, actor="ops@example.com")

                self._report_stuck_recovery(store)
                device = store.get_device(self.DEVICE_ID)
                assert device is not None
                self.assertTrue(device["status"]["update_recovery"]["stuck"])

                acknowledged = store.acknowledge_update_recovery(
                    self.DEVICE_ID, actor="ops@example.com"
                )
                self.assertFalse(acknowledged["status"]["update_recovery"]["stuck"])
                self.assertEqual(
                    acknowledged["status"]["update_recovery"]["acknowledged_by"],
                    "ops@example.com",
                )

                # A repeated heartbeat reporting the same stale condition must not
                # silently reinstate the alert.
                self._report_stuck_recovery(store)
                device = store.get_device(self.DEVICE_ID)
                assert device is not None
                self.assertFalse(device["status"]["update_recovery"]["stuck"])

                with store.engine.connect() as conn:
                    events = [
                        row["event"]
                        for row in conn.exec_driver_sql(
                            "SELECT event FROM audit_events WHERE device_id = ?",
                            (self.DEVICE_ID,),
                        ).mappings()
                    ]
                self.assertIn("update_recovery_acknowledged", events)

                # Once recovery clears and a *new* failure is reported, the alert
                # must be raised again even though the phase/error look the same.
                store.update_heartbeat(
                    self.DEVICE_ID,
                    {
                        "protocol_version": 1,
                        "poll_seconds": 10,
                        "update_recovery": {"stuck": False},
                    },
                )
                self._report_stuck_recovery(store)
                device = store.get_device(self.DEVICE_ID)
                assert device is not None
                self.assertTrue(device["status"]["update_recovery"]["stuck"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
