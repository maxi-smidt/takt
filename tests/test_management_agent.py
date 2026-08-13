from __future__ import annotations

import asyncio
import io
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from takt.management.agent import (
    AgentConfig,
    CancelledJob,
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

    def test_recovery_failure_is_exposed_in_heartbeat_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            agent._recovery_error = "manual repair is required"
            agent._write_update_journal({"phase": "activated"})
            status = asyncio.run(agent._status(object()))  # type: ignore[arg-type]
            self.assertEqual(
                status["update_recovery"],
                {"stuck": True, "error": "manual repair is required", "phase": "activated"},
            )

    def test_recovery_reporting_uses_status_only_endpoint(self) -> None:
        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def text(self) -> str:
                return "ok"

        class Session:
            def __init__(self) -> None:
                self.url = ""

            def get(self, *_args, **_kwargs):
                raise OSError("TAKT is unavailable")

            def post(self, url, *_args, **_kwargs):
                self.url = url
                return Response()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            agent._recovery_error = "manual repair is required"
            session = Session()
            asyncio.run(agent._report_recovery_failure(session))  # type: ignore[arg-type]
            self.assertEqual(session.url, "http://registry.test/agent/status")

    def test_recovery_backoff_keeps_status_heartbeats_flowing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            config.poll_seconds = 0.02
            agent = TaktAgent(config)
            with patch.object(agent, "_report_recovery_failure", AsyncMock()) as report:
                asyncio.run(agent._wait_with_recovery_heartbeats(object(), 0.12))  # type: ignore[arg-type]
            self.assertGreaterEqual(report.await_count, 2)

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

    def test_install_cancellation_after_activating_progress_avoids_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            job_id = "d" * 24
            agent._active_job = {"id": job_id, "cancel_requested": False}

            async def publish_progress(*_args, **kwargs) -> None:
                if kwargs.get("stage") == "activating":
                    agent._active_job["cancel_requested"] = True

            async def download_release(*_args, **kwargs) -> None:
                kwargs["artifact"].write_bytes(b"release")

            release = {
                "version": "0.2.0",
                "sha256": "a" * 64,
                "size": 7,
            }
            with (
                patch.object(agent, "_local_health", AsyncMock(return_value={"state": "ready"})),
                patch.object(agent, "_progress_job", AsyncMock(side_effect=publish_progress)),
                patch.object(agent, "_download_release", AsyncMock(side_effect=download_release)),
                patch.object(agent, "_prepare_release", return_value=root / "prepared"),
                patch.object(agent, "_acquire_maintenance", AsyncMock()),
                patch.object(agent, "_systemctl", AsyncMock()) as systemctl,
            ):
                with self.assertRaises(CancelledJob):
                    asyncio.run(
                        agent._install_release(
                            object(),
                            {"id": job_id, "release": release},  # type: ignore[arg-type]
                        )
                    )
            systemctl.assert_not_awaited()

    def test_wifi_profile_is_sent_to_helper_over_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            config.wifi_helper_path = root / "takt-wifi-helper"
            config.wifi_helper_path.write_text("helper", encoding="utf-8")
            config.wifi_helper_path.chmod(0o755)
            agent = TaktAgent(config)
            process = MagicMock(returncode=0)
            process.communicate = AsyncMock(return_value=(b"", None))
            create_process = AsyncMock(return_value=process)
            password = "fleet-secret-123"
            with (
                patch.object(agent, "_wifi_profile_capable", return_value=True),
                patch(
                    "takt.management.agent.asyncio.create_subprocess_exec",
                    create_process,
                ),
            ):
                asyncio.run(
                    agent._add_wifi_network(
                        {
                            "payload": {"ssid": "Timing Hall", "priority": 0},
                            "credential": {"password": password},
                        }
                    )
                )
            command = create_process.await_args.args
            self.assertEqual(command, ("sudo", "-n", str(config.wifi_helper_path)))
            self.assertNotIn(password, command)
            document = process.communicate.await_args.args[0]
            self.assertEqual(
                document,
                b'{"ssid":"Timing Hall","password":"fleet-secret-123","priority":0}',
            )

    def test_wifi_capability_requires_running_network_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            config.wifi_helper_path = root / "takt-wifi-helper"
            config.wifi_helper_path.write_text("helper", encoding="utf-8")
            config.wifi_helper_path.chmod(0o755)
            with (
                patch("takt.management.agent.shutil.which", return_value="/usr/bin/nmcli"),
                patch("takt.management.agent.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                agent = TaktAgent(config)
                self.assertTrue(agent._wifi_profile_capable())
                self.assertTrue(agent._wifi_profile_capable())
                run.assert_called_once()

                run.return_value.returncode = 3
                another_agent = TaktAgent(config)
                self.assertFalse(another_agent._wifi_profile_capable())
                self.assertFalse(another_agent._wifi_profile_capable())
                self.assertEqual(run.call_count, 2)

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
