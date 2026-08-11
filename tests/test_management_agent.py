from __future__ import annotations

import io
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from takt.management.agent import AgentConfig, Identity, TaktAgent


class ManagementAgentTests(unittest.TestCase):
    def test_identity_is_stable_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "identity.json"
            first = Identity.load_or_create(path)
            second = Identity.load_or_create(path)
            self.assertEqual(first.device_id, second.device_id)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_snapshot_is_a_consistent_sqlite_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE runs(id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO runs DEFAULT VALUES")
            connection.commit()
            config = self._config(root, source)
            agent = TaktAgent(config)
            target = root / "snapshot.db"
            agent._create_snapshot(target)
            copy = sqlite3.connect(target)
            try:
                self.assertEqual(copy.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            finally:
                copy.close()
                connection.close()

    def test_release_archive_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            agent = TaktAgent(config)
            artifact = root / "unsafe.tar.gz"
            with tarfile.open(artifact, "w:gz") as archive:
                info = tarfile.TarInfo("../outside")
                info.size = 3
                archive.addfile(info, io.BytesIO(b"bad"))
            with self.assertRaisesRegex(RuntimeError, "Unsafe archive member"):
                agent._prepare_release(artifact, "0.2.0", "job")
            self.assertFalse((root.parent / "outside").exists())

    def test_release_is_staged_in_a_versioned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            agent = TaktAgent(config)
            artifact = root / "release.tar.gz"
            with tarfile.open(artifact, "w:gz") as archive:
                content = b"[project]\nname='takt'\nversion='0.2.0'\n"
                info = tarfile.TarInfo("takt/pyproject.toml")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            with patch("takt.management.agent.subprocess.run") as run:
                destination = agent._prepare_release(artifact, "0.2.0", "job")
            self.assertEqual(destination, config.release_root / "0.2.0")
            self.assertTrue((destination / "pyproject.toml").exists())
            self.assertEqual(run.call_count, 2)

    @staticmethod
    def _config(root: Path, database_path: Path) -> AgentConfig:
        return AgentConfig(
            registry_url="http://registry.test",
            identity_path=root / "identity.json",
            database_path=database_path,
            data_directory=root / "agent",
            release_root=root / "releases",
            current_link=root / "current",
            release_environment=root / "release.env",
        )


if __name__ == "__main__":
    unittest.main()
