from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from takt.registry.storage import SCHEMA_VERSION, RegistryStore


class RegistryStorageTests(unittest.TestCase):
    def test_newer_database_schema_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            connection = sqlite3.connect(root / "registry.db")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "newer"):
                RegistryStore(root)

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
