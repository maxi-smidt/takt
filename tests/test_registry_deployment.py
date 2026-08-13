from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from takt.registry.deployment import redact_message, validate_registry_url
from takt.registry.storage import RegistryStore


class DeploymentStorageTests(unittest.TestCase):
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
                code = store.create_enrollment_code(
                    "Lane 1", deployment_id=deployment["id"]
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
                self.assertEqual(store.list_deployment_events(deployment["id"])[0]["stage"], "starting")
            finally:
                store.close()

    def test_host_key_replacement_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, release = self._store_with_release(Path(temporary))
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
