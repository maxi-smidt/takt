from __future__ import annotations

import unittest

from takt.management.redaction import redact_mapping, redact_text

PRIVATE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gt
ZWQyNTUxOQAAACDSuperSecretKeyMaterialThatMustNeverLeaveTheDevice
-----END OPENSSH PRIVATE KEY-----"""


class RedactTextTests(unittest.TestCase):
    def test_enrollment_codes_are_redacted(self) -> None:
        text = "enrollment_code = TAKT-4tPk9xQm2LwZaB7cRn3D"
        redacted = redact_text(text)
        self.assertNotIn("4tPk9xQm2LwZaB7cRn3D", redacted)

    def test_bearer_tokens_are_redacted(self) -> None:
        text = "Authorization: Bearer 4f2a9c8e1b7d3a6f5e0c9b8a7d6e5f4c"
        redacted = redact_text(text)
        self.assertNotIn("4f2a9c8e1b7d3a6f5e0c9b8a7d6e5f4c", redacted)
        self.assertIn("Bearer", redacted)

    def test_wifi_passwords_are_redacted(self) -> None:
        for line in ("psk=hunter2hunter2", "password = 'sehr-geheim-123'", 'psk: "abc12345"'):
            with self.subTest(line=line):
                redacted = redact_text(line)
                self.assertNotIn("hunter2hunter2", redacted)
                self.assertNotIn("sehr-geheim-123", redacted)
                self.assertNotIn("abc12345", redacted)

    def test_admin_passwords_are_redacted(self) -> None:
        text = "TAKT_REGISTRY_ADMIN_PASSWORD=correct-horse-battery-staple"
        self.assertNotIn("correct-horse-battery-staple", redact_text(text))

    def test_private_keys_are_redacted_as_a_whole_block(self) -> None:
        redacted = redact_text(f"key material follows\n{PRIVATE_KEY}\ntrailing line")
        self.assertNotIn("SuperSecretKeyMaterial", redacted)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", redacted)
        self.assertIn("trailing line", redacted)

    def test_known_secrets_are_redacted_by_exact_match(self) -> None:
        token = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        text = f"device token {token} appears verbatim in this log line"
        self.assertNotIn(token, redact_text(text, secrets=[token]))

    def test_credentials_embedded_in_urls_are_redacted(self) -> None:
        redacted = redact_text("registry at https://admin:s3cr3tpw@takt.local:8090/health")
        self.assertNotIn("s3cr3tpw", redacted)
        self.assertIn("takt.local:8090/health", redacted)

    def test_redaction_is_idempotent(self) -> None:
        text = f"Bearer abcdef1234567890\npsk=hunter2hunter2\n{PRIVATE_KEY}"
        once = redact_text(text)
        self.assertEqual(redact_text(once), once)

    def test_ordinary_diagnostics_are_preserved(self) -> None:
        text = (
            "takt 0.2.0 started on takt-01\n"
            "release sha256 3b1f0c2d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c\n"
            "disk free 4096 MB, temperature 51.2 C\n"
        )
        self.assertEqual(redact_text(text), text)

    def test_short_known_secrets_do_not_scrub_common_words(self) -> None:
        # A too-short "secret" would otherwise blank out ordinary log text.
        self.assertEqual(redact_text("timer is ready", secrets=["ready"]), "timer is ready")


class RedactMappingTests(unittest.TestCase):
    def test_secret_keys_are_redacted_recursively(self) -> None:
        config = {
            "agent": {
                "registry_url": "https://takt.local:8090",
                "enrollment_code": "TAKT-4tPk9xQm2LwZaB7cRn3D",
                "device_token": "a1b2c3d4e5f6a7b8c9d0e1f2",
                "verify_tls": True,
                "poll_seconds": 10,
            },
            "networks": [{"ssid": "Timing Hall", "psk": "hunter2hunter2"}],
        }
        redacted = redact_mapping(config)
        self.assertEqual(redacted["agent"]["registry_url"], "https://takt.local:8090")
        self.assertEqual(redacted["agent"]["verify_tls"], True)
        self.assertEqual(redacted["agent"]["poll_seconds"], 10)
        self.assertNotIn("4tPk9xQm2LwZaB7cRn3D", str(redacted))
        self.assertNotIn("a1b2c3d4e5f6a7b8c9d0e1f2", str(redacted))
        self.assertNotIn("hunter2hunter2", str(redacted))
        self.assertEqual(redacted["networks"][0]["ssid"], "Timing Hall")

    def test_lease_identifiers_are_redacted(self) -> None:
        redacted = redact_mapping({"lease_id": "Xy7-lease-token-value", "job_id": "abc123"})
        self.assertNotIn("Xy7-lease-token-value", str(redacted))
        self.assertEqual(redacted["job_id"], "abc123")


if __name__ == "__main__":
    unittest.main()
