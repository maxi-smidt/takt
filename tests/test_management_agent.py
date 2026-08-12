from __future__ import annotations

import asyncio
import io
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from takt.management.agent import (
    AgentConfig,
    DeferredJob,
    Identity,
    StaleJobResult,
    TaktAgent,
)


class ManagementAgentTests(unittest.TestCase):
    def test_remote_http_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            config.allow_insecure_http = False
            with self.assertRaisesRegex(ValueError, "Remote HTTP registry connections"):
                TaktAgent(config)
            config.registry_url = "http://127.0.0.1:8090"
            agent = TaktAgent(config)
            self.assertEqual(agent._registry_transport, "loopback-http")

    def test_identity_is_stable_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "identity.json"
            first = Identity.load_or_create(path)
            second = Identity.load_or_create(path)
            self.assertEqual(first.device_id, second.device_id)
            self.assertEqual(first.device_token, second.device_token)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_legacy_identity_persists_token_before_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "identity.json"
            path.write_text('{"device_id":"legacy-device"}\n', encoding="utf-8")
            first = Identity.load_or_create(path)
            second = Identity.load_or_create(path)
            self.assertEqual(first.device_token, second.device_token)
            self.assertFalse(first.enrolled)

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
            install = run.call_args_list[-1]
            self.assertEqual(install.kwargs["cwd"], destination)
            self.assertNotIn("-e", install.args[0])

    def test_interrupted_update_does_not_restore_while_timer_may_be_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            previous = config.release_root / "0.1.0"
            previous.mkdir(parents=True)
            agent = TaktAgent(config)
            agent._write_update_journal(
                {
                    "job_id": "a" * 24,
                    "lease_id": "lease-a",
                    "version": "0.2.0",
                    "previous_target": str(previous),
                    "previous_version": "0.1.0",
                    "phase": "activated",
                }
            )
            with (
                patch.object(agent, "_service_is_active", AsyncMock(return_value=True)),
                patch.object(
                    agent,
                    "_acquire_maintenance",
                    AsyncMock(side_effect=DeferredJob("timer is running")),
                ),
                patch.object(agent, "_systemctl", AsyncMock()) as systemctl,
            ):
                with self.assertRaises(DeferredJob):
                    asyncio.run(agent._recover_interrupted_update(object()))  # type: ignore[arg-type]
            systemctl.assert_not_awaited()
            self.assertTrue(agent.update_journal_path.exists())

    def test_stale_outbox_result_does_not_block_future_heartbeats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            agent.state.pending_results["b" * 24] = {
                "status": "succeeded",
                "progress": 100,
                "message": "done",
                "lease_id": "expired-lease",
            }
            with patch.object(
                agent,
                "_send_job_event",
                AsyncMock(side_effect=StaleJobResult("lease replaced")),
            ):
                asyncio.run(agent._flush_pending_results(object()))  # type: ignore[arg-type]
            self.assertEqual(agent.state.pending_results, {})

    def test_mirror_write_during_upload_remains_dirty(self) -> None:
        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def text(self) -> str:
                return "ok"

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "takt.db"
            database.write_bytes(b"database")
            agent = TaktAgent(self._config(root, database))
            before = (1, 10, 2, 20)
            changed = (3, 11, 4, 21)
            with (
                patch.object(
                    agent,
                    "_create_snapshot",
                    side_effect=lambda path: path.write_bytes(b"snapshot"),
                ),
                patch.object(
                    agent,
                    "_database_signature",
                    side_effect=[before, changed, changed],
                ),
            ):
                asyncio.run(agent._upload_mirror(Session()))  # type: ignore[arg-type]
            self.assertIsNone(agent._last_mirror_signature)
            self.assertIsNone(agent.state.last_mirror_signature)

    def test_service_restart_keeps_persistent_maintenance_until_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            session = object()

            async def assert_marker_during_restart(*_args) -> None:
                self.assertTrue(agent.config.maintenance_marker.is_file())

            with (
                patch.object(agent, "_progress_job", AsyncMock()),
                patch.object(agent, "_renew_job_lease", AsyncMock()),
                patch.object(agent, "_remember_result", AsyncMock()),
                patch.object(
                    agent,
                    "_local_health",
                    AsyncMock(return_value={"ok": True, "version": "0.1.0"}),
                ),
                patch.object(agent, "_acquire_maintenance", AsyncMock(return_value="lease")),
                patch.object(
                    agent,
                    "_systemctl",
                    AsyncMock(side_effect=assert_marker_during_restart),
                ) as systemctl,
                patch.object(
                    agent,
                    "_wait_for_health",
                    AsyncMock(side_effect=assert_marker_during_restart),
                ) as wait_for_health,
            ):
                asyncio.run(
                    agent._execute_job(
                        session,  # type: ignore[arg-type]
                        {
                            "id": "c" * 24,
                            "lease_id": "restart-lease",
                            "action": "restart_takt",
                        },
                    )
                )
            systemctl.assert_awaited_once_with("restart", agent.config.service_name)
            wait_for_health.assert_awaited_once_with(session, "0.1.0")
            self.assertFalse(agent.config.maintenance_marker.exists())

    @staticmethod
    def _config(root: Path, database_path: Path) -> AgentConfig:
        return AgentConfig(
            registry_url="http://registry.test",
            allow_insecure_http=True,
            identity_path=root / "identity.json",
            database_path=database_path,
            data_directory=root / "agent",
            release_root=root / "releases",
            current_link=root / "current",
            release_environment=root / "release.env",
            maintenance_marker=root / "maintenance.json",
        )


if __name__ == "__main__":
    unittest.main()
