from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from takt.registry.deployment import (
    OUTPUT_LIMIT,
    DeploymentManager,
    redact_message,
    validate_hostname,
    validate_registry_url,
)
from takt.registry.storage import RegistryStore


class DeploymentStorageTests(unittest.TestCase):
    def test_hostname_request_is_preserve_by_default_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, release = self._store_with_release(root)
            try:
                deployment = store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 1",
                    requested_hostname="",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                self.assertEqual(deployment["requested_hostname"], "")
                self.assertEqual(deployment["hostname_change_confirmed"], 0)
                store.record_hostname_change(
                    deployment["id"], old_hostname="raspberrypi", new_hostname="takt-01"
                )
                audit = store.connection.execute(
                    "SELECT event, details_json FROM audit_events WHERE event = 'hostname_changed'"
                ).fetchone()
                self.assertIsNotNone(audit)
                self.assertIn("takt-01", audit["details_json"])
            finally:
                store.close()

    def test_hostname_validation_allows_only_an_explicit_empty_value(self) -> None:
        self.assertEqual(validate_hostname("", allow_empty=True), "")
        with self.assertRaisesRegex(ValueError, "invalid"):
            validate_hostname("")
        with self.assertRaisesRegex(ValueError, "invalid"):
            validate_hostname("takt_01")
        self.assertEqual(validate_hostname("takt-01"), "takt-01")

    def _store_with_release(self, root: Path) -> tuple[RegistryStore, dict[str, str]]:
        store = RegistryStore(root)
        source = root / "source.tar.gz"
        source.write_bytes(b"release")
        release = store.add_release(
            version="0.2.0",
            filename="release.tar.gz",
            sha256="a" * 64,
            size=source.stat().st_size,
            source=source,
        )
        return store, release

    def test_deployment_links_to_idempotent_agent_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, release = self._store_with_release(root)
            try:
                deployment = store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 1",
                    requested_hostname="takt-01",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                abandoned_code = store.create_enrollment_code(
                    "Lane 1", deployment_id=deployment["id"]
                )
                code = store.create_enrollment_code(
                    "Lane 1", deployment_id=deployment["id"]
                )
                with self.assertRaisesRegex(ValueError, "invalid"):
                    store.enroll_device(
                        code=abandoned_code,
                        device_id="12345678-1234-1234-1234-123456789abc",
                        name="Lane 1",
                        hostname="takt-01",
                        token="a" * 64,
                    )
                store.enroll_device(
                    code=code,
                    device_id="12345678-1234-1234-1234-123456789abc",
                    name="Lane 1",
                    hostname="takt-01",
                    token="a" * 64,
                )
                linked = store.get_deployment(deployment["id"])
                assert linked is not None
                self.assertEqual(linked["device_id"], "12345678-1234-1234-1234-123456789abc")
                store.record_deployment_event(
                    deployment["id"], "preflight", "Checks passed", status="running"
                )
                events = store.list_deployment_events(deployment["id"])
                self.assertEqual(events[-1]["stage"], "preflight")
                self.assertTrue(events[-1]["created_at"])
            finally:
                store.close()

    def test_host_key_replacement_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, release = self._store_with_release(Path(temporary))
            try:
                store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 1",
                    requested_hostname="takt-01",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                key_name = store.deployment_target_key("pi.local", 22)
                store.trust_ssh_host(key_name, "ssh-ed25519 AAAAone", "SHA256:one")
                with self.assertRaisesRegex(ValueError, "explicit"):
                    store.trust_ssh_host(
                        key_name, "ssh-ed25519 AAAAtwo", "SHA256:two"
                    )
                store.trust_ssh_host(
                    key_name, "ssh-ed25519 AAAAtwo", "SHA256:two", replace=True
                )
                self.assertEqual(
                    store.get_trusted_ssh_host(key_name)["fingerprint"], "SHA256:two"
                )
            finally:
                store.close()

    def test_superseded_code_cannot_link_existing_device(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, release = self._store_with_release(Path(temporary))
            try:
                token = "a" * 64
                device_id = "12345678-1234-1234-1234-123456789abc"
                store.enroll_device(
                    code=store.create_enrollment_code(),
                    device_id=device_id,
                    name="Lane 1",
                    hostname="takt-01",
                    token=token,
                )
                deployment = store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 1",
                    requested_hostname="takt-01",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                abandoned = store.create_enrollment_code(
                    "Lane 1", deployment_id=deployment["id"]
                )
                store.create_enrollment_code("Lane 1", deployment_id=deployment["id"])
                store.enroll_device(
                    code=abandoned,
                    device_id=device_id,
                    name="Lane 1",
                    hostname="takt-01",
                    token=token,
                )
                linked = store.get_deployment(deployment["id"])
                assert linked is not None
                self.assertIsNone(linked["device_id"])
            finally:
                store.close()

    def test_active_target_is_serialized_and_restart_marks_it_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, release = self._store_with_release(root)
            deployment = store.create_deployment(
                target="pi.local",
                port=22,
                ssh_user="pi",
                device_name="Lane 1",
                requested_hostname="takt-01",
                registry_url="https://registry.example",
                allow_insecure_http=False,
                release_id=release["id"],
            )
            try:
                with self.assertRaisesRegex(ValueError, "already active"):
                    store.create_deployment(
                        target="pi.local",
                        port=22,
                        ssh_user="pi",
                        device_name="Lane 2",
                        requested_hostname="takt-02",
                        registry_url="https://registry.example",
                        allow_insecure_http=False,
                        release_id=release["id"],
                    )
            finally:
                store.close()
            store = RegistryStore(root)
            try:
                interrupted = store.get_deployment(deployment["id"])
                assert interrupted is not None
                self.assertEqual(interrupted["status"], "interrupted")
            finally:
                store.close()

    def test_retry_cannot_bypass_active_target_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, release = self._store_with_release(Path(temporary))
            try:
                finished = store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 1",
                    requested_hostname="takt-01",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                store.update_deployment(finished["id"], status="failed")
                store.create_deployment(
                    target="pi.local",
                    port=22,
                    ssh_user="pi",
                    device_name="Lane 2",
                    requested_hostname="takt-02",
                    registry_url="https://registry.example",
                    allow_insecure_http=False,
                    release_id=release["id"],
                )
                with self.assertRaisesRegex(ValueError, "already active"):
                    DeploymentManager(store).retry(finished["id"])
            finally:
                store.close()


class DeploymentManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_hostname_change_reconnects_and_audits(self) -> None:
        class Store:
            def __init__(self) -> None:
                self.changes = []

            def record_hostname_change(self, *args, **kwargs) -> None:
                self.changes.append((args, kwargs))

        manager = object.__new__(DeploymentManager)
        manager.store = Store()
        manager._event = lambda *args, **kwargs: None
        old_connection = object()
        new_connection = object()
        targets = []

        async def connect(deployment, _credentials):
            targets.append(deployment["target"])
            return new_connection

        async def wait_for_agent(*args, **kwargs):
            self.assertEqual(kwargs["expected_hostname"], "takt-01")
            return "boot-1"

        manager._connect = connect
        manager._wait_for_agent = wait_for_agent
        connections = [old_connection]
        connection, boot_id, expected = await manager._handle_hostname_change(
            old_connection,
            connections,
            "deployment",
            {
                "requested_hostname": "takt-01",
                "hostname_change_confirmed": 1,
                "release_version": "0.2.0",
            },
            object(),
            "raspberrypi",
        )

        self.assertIs(connection, new_connection)
        self.assertEqual(boot_id, "boot-1")
        self.assertEqual(expected, "takt-01")
        self.assertEqual(targets, ["takt-01.local"])
        self.assertEqual(len(manager.store.changes), 1)

        preserved_connection, preserved_boot_id, preserved_hostname = (
            await manager._handle_hostname_change(
                old_connection,
                connections,
                "deployment",
                {
                    "requested_hostname": "",
                    "hostname_change_confirmed": 0,
                    "release_version": "0.2.0",
                },
                object(),
                "raspberrypi",
            )
        )
        self.assertIs(preserved_connection, old_connection)
        self.assertIsNone(preserved_boot_id)
        self.assertEqual(preserved_hostname, "raspberrypi")
        self.assertEqual(targets, ["takt-01.local"])

        same_connection, same_boot_id, same_hostname = await manager._handle_hostname_change(
            old_connection,
            connections,
            "deployment",
            {
                "requested_hostname": "raspberrypi",
                "hostname_change_confirmed": 1,
                "release_version": "0.2.0",
            },
            object(),
            "raspberrypi",
        )
        self.assertIs(same_connection, old_connection)
        self.assertIsNone(same_boot_id)
        self.assertEqual(same_hostname, "raspberrypi")
        self.assertEqual(targets, ["takt-01.local"])

    async def test_hostname_change_failure_rolls_back_on_original_connection(self) -> None:
        manager = object.__new__(DeploymentManager)
        manager._event = lambda *args, **kwargs: None
        old_connection = object()
        rollback = []

        async def connect(_deployment, _credentials):
            raise OSError("mDNS not ready")

        async def rollback_hostname(connection, deployment_id, hostname, from_hostname):
            rollback.append((connection, deployment_id, hostname, from_hostname))

        manager._connect = connect
        manager._rollback_hostname = rollback_hostname
        with self.assertRaisesRegex(OSError, "mDNS"):
            await manager._handle_hostname_change(
                old_connection,
                [old_connection],
                "deployment",
                {
                    "requested_hostname": "takt-01",
                    "hostname_change_confirmed": 1,
                    "release_version": "0.2.0",
                },
                object(),
                "raspberrypi",
            )
        self.assertEqual(rollback, [(old_connection, "deployment", "raspberrypi", "takt-01")])

    async def test_signal_terminated_command_returns_failure_status(self) -> None:
        class Stream:
            async def read(self, _size: int) -> str:
                return ""

        class Process:
            stdout = Stream()
            stderr = Stream()

            async def wait(self) -> SimpleNamespace:
                return SimpleNamespace(exit_status=None)

        class Connection:
            async def create_process(self, command: str, encoding: str) -> Process:
                return Process()

        manager = object.__new__(DeploymentManager)
        manager._event = lambda *args, **kwargs: None
        _, _, status = await manager._command(Connection(), "deployment", "stage", "true")

        self.assertEqual(status, 1)

    async def test_remote_output_is_capped(self) -> None:
        class Stream:
            def __init__(self) -> None:
                self.chunks = ["x" * (OUTPUT_LIMIT + 1), "tail"]

            async def read(self, _size: int) -> str:
                return self.chunks.pop(0) if self.chunks else ""

        output = await DeploymentManager._read_output(Stream())

        self.assertEqual(len(output), OUTPUT_LIMIT)
        self.assertTrue(output.endswith("tail"))


class DeploymentValidationTests(unittest.TestCase):
    def test_secrets_are_redacted_from_event_text(self) -> None:
        message = redact_message(
            "ssh password and TAKT-secret-code",
            ["ssh password"],
        )

        self.assertNotIn("ssh password", message)
        self.assertNotIn("TAKT-secret-code", message)
        self.assertIn("[redacted", message)

    def test_registry_http_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            validate_registry_url("http://registry.example", False)
        self.assertEqual(
            validate_registry_url("http://registry.example/", True),
            "http://registry.example",
        )
