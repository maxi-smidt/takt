from __future__ import annotations

import asyncio
import hashlib
import io
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from takt.management.agent import (
    MAX_JOBS_PER_CYCLE,
    MAX_PENDING_RESULT_ATTEMPTS,
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

    def test_curation_job_updates_local_db_and_mirrors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            job = {
                "id": "c" * 24,
                "payload": {
                    "operation": "adjust_added_time",
                    "run_id": 7,
                    "expected_updated_at": "version-a",
                    "desired_added_time_ms": 5000,
                },
            }

            class Response:
                status = 200

                async def json(self):
                    return {"ok": True, "result": {"operation": "adjust_added_time"}}

                async def text(self):
                    return "unexpected response"

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

            class Session:
                def __init__(self):
                    self.request = None

                def post(self, url, **kwargs):
                    self.request = (url, kwargs)
                    return Response()

            session = Session()
            progress = AsyncMock()
            upload = AsyncMock()
            with (
                patch.object(agent, "_progress_job", progress),
                patch.object(agent, "_upload_mirror", upload),
            ):
                asyncio.run(agent._curate_run(session, job))
            self.assertEqual(session.request[0], "http://127.0.0.1/internal/run-curation")
            self.assertEqual(session.request[1]["json"]["command_id"], "c" * 24)
            self.assertEqual(progress.await_args_list[0].kwargs["stage"], "applying")
            self.assertEqual(progress.await_args_list[1].kwargs["stage"], "refreshing_mirror")
            upload.assert_awaited_once()
            self.assertEqual(agent._active_health_report["operation"], "adjust_added_time")

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
                destination, dependencies_changed = agent._prepare_release(
                    artifact, "0.2.0", "job"
                )
            self.assertEqual(destination, config.release_root / "0.2.0")
            self.assertTrue((destination / "pyproject.toml").exists())
            # No previous release to copy a venv from, so this is treated like a
            # first install and always needs a full dependency resolve.
            self.assertTrue(dependencies_changed)
            self.assertEqual(run.call_count, 1)
            self.assertIn("venv", run.call_args.args[0])
            with patch("takt.management.agent.subprocess.run") as run:
                agent._install_release_dependencies(destination, dependencies_changed)
            install = run.call_args
            self.assertEqual(install.kwargs["cwd"], destination)
            self.assertNotIn("-e", install.args[0])
            self.assertNotIn("--no-deps", install.args[0])

    def test_prepare_release_skips_resolve_when_dependency_set_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            self._install_previous_release(config, dependencies=["aiohttp>=3.10,<4"])
            agent = TaktAgent(config)
            artifact = self._release_archive(
                root, "0.2.0", dependencies=["aiohttp>=3.10,<4"]
            )
            with patch("takt.management.agent.subprocess.run") as run:
                destination, dependencies_changed = agent._prepare_release(
                    artifact, "0.2.0", "job"
                )
            self.assertFalse(dependencies_changed)
            run.assert_not_called()  # the venv was copied, not (re)created
            with patch("takt.management.agent.subprocess.run") as run:
                agent._install_release_dependencies(destination, dependencies_changed)
            install = run.call_args
            self.assertIn("--no-deps", install.args[0])

    def test_prepare_release_detects_an_added_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            self._install_previous_release(config, dependencies=[])
            agent = TaktAgent(config)
            artifact = self._release_archive(root, "0.2.0", dependencies=["requests>=2,<3"])
            destination, dependencies_changed = agent._prepare_release(artifact, "0.2.0", "job")
            self.assertTrue(dependencies_changed)
            with patch("takt.management.agent.subprocess.run") as run:
                agent._install_release_dependencies(destination, dependencies_changed)
            install = run.call_args
            self.assertNotIn("--no-deps", install.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_dependency_install_failure_leaves_running_release_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            self._install_previous_release(config, dependencies=[])
            agent = TaktAgent(config)
            job_id = "e" * 24
            release = {
                "version": "0.2.0",
                "sha256": "a" * 64,
                "size": 7,
            }

            async def download_release(*_args, **kwargs) -> None:
                self._release_archive(
                    root, "0.2.0", dependencies=["requests>=2,<3"], destination=kwargs["artifact"]
                )

            pip_error = subprocess.CalledProcessError(
                1,
                ["pip", "install"],
                output="Collecting requests\n",
                stderr=(
                    "ERROR: Could not find a version that satisfies the requirement "
                    "requests>=2,<3 (from https://user:s3cret@pypi.example/simple)\n"
                ),
            )
            with (
                patch.object(agent, "_local_health", AsyncMock(return_value={"state": "ready"})),
                patch.object(agent, "_progress_job", AsyncMock()) as progress,
                patch.object(
                    agent, "_download_release", AsyncMock(side_effect=download_release)
                ),
                patch.object(agent, "_systemctl", AsyncMock()) as systemctl,
                patch(
                    "takt.management.agent.subprocess.run", side_effect=pip_error
                ),self.assertRaises(RuntimeError) as failure
            ):
                asyncio.run(
                    agent._install_release(
                        object(),  # type: ignore[arg-type]
                        {"id": job_id, "release": release},
                    )
                )
            systemctl.assert_not_awaited()
            self.assertIn("Dependency installation failed", str(failure.exception))
            self.assertNotIn("s3cret", str(failure.exception))
            self.assertIn("requests>=2,<3", str(failure.exception))
            self.assertFalse((config.release_root / "0.2.0").exists())
            self.assertEqual(
                config.current_link.resolve(), config.release_root / "0.1.0"
            )
            dependency_stages = [
                call.kwargs.get("stage") for call in progress.await_args_list
            ]
            self.assertIn("installing_dependencies", dependency_stages)

    @staticmethod
    def _install_previous_release(config: AgentConfig, *, dependencies: list[str]) -> Path:
        previous = config.release_root / "0.1.0"
        previous.mkdir(parents=True)
        (previous / ".venv").mkdir()
        dependency_list = ", ".join(f'"{dependency}"' for dependency in dependencies)
        (previous / "pyproject.toml").write_text(
            f"[project]\nname='takt'\nversion='0.1.0'\ndependencies=[{dependency_list}]\n",
            encoding="utf-8",
        )
        config.current_link.symlink_to(previous)
        return previous

    @staticmethod
    def _release_archive(
        root: Path,
        version: str,
        *,
        dependencies: list[str],
        destination: Path | None = None,
    ) -> Path:
        artifact = destination or (root / f"release-{version}.tar.gz")
        dependency_list = ", ".join(f'"{dependency}"' for dependency in dependencies)
        content = (
            f"[project]\nname='takt'\nversion='{version}'\ndependencies=[{dependency_list}]\n"
        ).encode()
        with tarfile.open(artifact, "w:gz") as archive:
            info = tarfile.TarInfo("takt/pyproject.toml")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return artifact

    def test_large_release_download_coalesces_progress_and_reports_final_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            job_id = "a" * 24
            agent._active_job = {
                "id": job_id,
                "lease_id": "lease",
                "control_lost": False,
                "cancel_requested": False,
            }
            chunk_size = 256 * 1024
            chunks = [bytes([index]) * chunk_size for index in range(24)]
            payload = b"".join(chunks)

            class Content:
                async def iter_chunked(self, _size: int):
                    for chunk in chunks:
                        yield chunk

            class Response:
                status = 200
                content = Content()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args: object) -> None:
                    return None

            class Session:
                def get(self, *_args: object, **_kwargs: object) -> Response:
                    return Response()

            clock = {"now": 0.0}

            def monotonic() -> float:
                clock["now"] += 0.2
                return clock["now"]

            progress = AsyncMock()
            session = Session()
            artifact = root / "release.tar.gz.part"
            with (
                patch.object(agent, "_progress_job", progress),
                patch("takt.management.agent._now", side_effect=monotonic),
            ):
                asyncio.run(
                    agent._download_release(
                        session,
                        job_id=job_id,
                        artifact=artifact,
                        expected_size=len(payload),
                        expected_sha256=hashlib.sha256(payload).hexdigest(),
                    )
                )

            self.assertEqual(artifact.read_bytes(), payload)
            download_progress_calls = [
                call
                for call in progress.await_args_list
                if call.kwargs.get("stage") == "downloading"
            ]
            self.assertGreaterEqual(len(download_progress_calls), 1)
            self.assertGreaterEqual(progress.await_count, 2)
            self.assertLess(progress.await_count, len(chunks))
            final_call = progress.await_args_list[-1]
            self.assertEqual(final_call.args[:2], (session, job_id))
            self.assertEqual(final_call.kwargs["stage"], "verifying")
            self.assertEqual(final_call.kwargs["bytes_downloaded"], len(payload))
            self.assertEqual(final_call.kwargs["bytes_total"], len(payload))

    def test_complete_partial_release_reports_final_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            job_id = "b" * 24
            payload = b"complete release"
            artifact = root / "release.tar.gz.part"
            artifact.write_bytes(payload)
            progress = AsyncMock()
            session = object()

            with patch.object(agent, "_progress_job", progress):
                asyncio.run(
                    agent._download_release(
                        session,  # type: ignore[arg-type]
                        job_id=job_id,
                        artifact=artifact,
                        expected_size=len(payload),
                        expected_sha256=hashlib.sha256(payload).hexdigest(),
                    )
                )

            progress.assert_awaited_once_with(
                session,
                job_id,
                25,
                "Verifying release checksum",
                stage="verifying",
                bytes_downloaded=len(payload),
                bytes_total=len(payload),
            )

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
                self.assertRaises(DeferredJob),
            ):
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

    def test_pending_result_flush_is_retried_then_abandoned_after_max_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            job_id = "c" * 24
            agent.state.pending_results[job_id] = {
                "status": "succeeded",
                "progress": 100,
                "message": "done",
                "lease_id": "lease",
            }
            with patch.object(
                agent, "_send_job_event", AsyncMock(side_effect=RuntimeError("offline"))
            ):
                for attempt in range(1, MAX_PENDING_RESULT_ATTEMPTS):
                    asyncio.run(agent._flush_pending_results(object()))  # type: ignore[arg-type]
                    self.assertIn(job_id, agent.state.pending_results)
                    self.assertEqual(
                        agent.state.pending_results[job_id]["attempts"], attempt
                    )
                asyncio.run(agent._flush_pending_results(object()))  # type: ignore[arg-type]
            self.assertNotIn(job_id, agent.state.pending_results)

    def test_send_job_event_treats_a_400_as_a_stale_result(self) -> None:
        class Response:
            status = 400

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def text(self) -> str:
                return "invalid transition"

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            with self.assertRaises(StaleJobResult):
                asyncio.run(  # type: ignore[arg-type]
                    agent._send_job_event(Session(), "d" * 24, "succeeded", 100, "done")
                )

    def test_cycle_sends_heartbeat_even_when_pending_result_flush_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            with (
                patch.object(agent, "_heartbeat", AsyncMock(return_value=None)) as heartbeat,
                patch.object(
                    agent,
                    "_flush_pending_results",
                    AsyncMock(side_effect=RuntimeError("registry unreachable")),
                ),
                patch.object(agent, "_mirror_if_changed", AsyncMock()),
            ):
                asyncio.run(agent._cycle(object()))  # type: ignore[arg-type]
            heartbeat.assert_awaited_once()

    def test_cycle_drains_multiple_queued_jobs_up_to_the_per_cycle_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = TaktAgent(self._config(root, root / "takt.db"))
            heartbeat_jobs = [
                {"id": f"job-{index}", "action": "mirror_now"}
                for index in range(MAX_JOBS_PER_CYCLE + 1)
            ]
            executed: list[str] = []

            async def fake_execute(_session, job) -> None:
                executed.append(job["id"])

            with (
                patch.object(agent, "_heartbeat", AsyncMock(side_effect=heartbeat_jobs)),
                patch.object(agent, "_execute_job", AsyncMock(side_effect=fake_execute)),
                patch.object(agent, "_flush_pending_results", AsyncMock()),
                patch.object(agent, "_mirror_if_changed", AsyncMock()),
            ):
                asyncio.run(agent._cycle(object()))  # type: ignore[arg-type]
            self.assertEqual(len(executed), MAX_JOBS_PER_CYCLE)

    def test_reconnect_delay_is_capped_around_sixty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = self._config(root, root / "takt.db")
            config.poll_seconds = 10.0
            agent = TaktAgent(config)
            delays = [agent._reconnect_delay(failures) for failures in range(1, 10)]
            self.assertTrue(all(delay <= 72.0 for delay in delays))
            self.assertGreater(max(delays), 40.0)

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
            session = object()
            prepared = root / "prepared"
            prepared.mkdir()

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
                patch.object(agent, "_prepare_release", return_value=(prepared, False)),
                patch.object(agent, "_install_release_dependencies"),
                patch.object(agent, "_service_is_active", AsyncMock(return_value=True)),
                patch.object(
                    agent, "_acquire_maintenance", AsyncMock(return_value="maintenance-token")
                ),
                patch.object(agent, "_release_maintenance", AsyncMock()) as release_maintenance,
                patch.object(agent, "_systemctl", AsyncMock()) as systemctl,
                self.assertRaises(CancelledJob),
            ):
                asyncio.run(
                    agent._install_release(
                        session,
                        {"id": job_id, "release": release},  # type: ignore[arg-type]
                    )
                )
            systemctl.assert_not_awaited()
            release_maintenance.assert_awaited_once_with(session, "maintenance-token")
            self.assertFalse(prepared.exists())

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
            maintenance_helper_path=root / "maintenance-helper",
            log_directory=root / "logs",
        )


class FleetMaintenanceAgentTests(unittest.TestCase):
    def _agent(self, root: Path, *, helper_verbs: frozenset[str] = frozenset()) -> TaktAgent:
        config = ManagementAgentTests._config(root, root / "takt.db")
        with (
            patch.object(TaktAgent, "_probe_wifi_profile_capability", return_value=False),
            patch.object(TaktAgent, "_probe_maintenance_helper", return_value=helper_verbs),
        ):
            return TaktAgent(config)

    def test_capabilities_follow_the_helper_verb_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            without = self._agent(root)
            self.assertNotIn("service-control-v1", without._capabilities())
            self.assertNotIn("power-control-v1", without._capabilities())
            # Diagnostics and health checks need no privilege, so they stay
            # available even on a Pi whose installer predates the helper.
            self.assertIn("diagnostics-v1", without._capabilities())
            self.assertIn("health-checks-v1", without._capabilities())

            with_helper = self._agent(root, helper_verbs=frozenset({"service", "power", "journal"}))
            self.assertIn("service-control-v1", with_helper._capabilities())
            self.assertIn("power-control-v1", with_helper._capabilities())

    def test_missing_helper_probe_degrades_to_no_verbs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = ManagementAgentTests._config(root, root / "takt.db")
            with patch.object(TaktAgent, "_probe_wifi_profile_capability", return_value=False):
                agent = TaktAgent(config)
            self.assertEqual(agent._helper_verbs, frozenset())

    def test_calling_an_unsupported_helper_verb_is_refused_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = self._agent(Path(temporary_directory))
            with (
                patch("takt.management.agent.asyncio.create_subprocess_exec") as spawn,
                self.assertRaisesRegex(RuntimeError, "does not support"),
            ):
                asyncio.run(agent._call_helper("power", {"mode": "reboot"}))
            self.assertEqual(spawn.call_count, 0)

    def test_reboot_reports_its_result_after_power_helper_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = self._agent(root, helper_verbs=frozenset({"power"}))
            order: list[str] = []

            async def record_event(*args: object, **kwargs: object) -> None:
                order.append(f"report:{kwargs.get('status') or args[2]}")

            async def record_helper(verb: str, arguments: dict[str, object]) -> dict[str, object]:
                order.append(f"helper:{verb}:{arguments['mode']}")
                return {}

            job = {
                "id": "a" * 24,
                "action": "reboot_device",
                "lease_id": "lease-1",
                "payload": {"override": True},
            }
            with (
                patch.object(TaktAgent, "_call_helper", side_effect=record_helper),
                patch.object(TaktAgent, "_progress_job", AsyncMock()),
                patch.object(TaktAgent, "_send_job_event", side_effect=record_event),
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=True)),
            ):
                asyncio.run(agent._power_action(AsyncMock(), job))
            self.assertEqual(order[-1], "report:succeeded")
            self.assertLess(
                order.index("helper:power:reboot"),
                order.index("report:succeeded"),
                "a power action must not be successful before the helper accepts it",
            )

    def test_reboot_helper_failure_is_not_recorded_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = self._agent(Path(temporary_directory), helper_verbs=frozenset({"power"}))
            job = {"id": "a" * 24, "action": "reboot_device", "lease_id": "lease-1", "payload": {}}
            with (
                patch.object(
                    TaktAgent, "_call_helper",
                    AsyncMock(side_effect=RuntimeError("helper refused")),
                ),
                patch.object(TaktAgent, "_progress_job", AsyncMock()),
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=False)),
                self.assertRaisesRegex(RuntimeError, "helper refused"),
            ):
                asyncio.run(agent._power_action(AsyncMock(), job))


    def test_reboot_result_stays_durable_when_reporting_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = self._agent(root, helper_verbs=frozenset({"power"}))
            state_at_power: dict[str, str] = {}

            async def capture(verb: str, arguments: dict[str, object]) -> dict[str, object]:
                return {}

            job = {
                "id": "a" * 24,
                "action": "reboot_device",
                "lease_id": "lease-1",
                "payload": {"override": True},
            }
            with (
                patch.object(TaktAgent, "_call_helper", side_effect=capture),
                patch.object(TaktAgent, "_progress_job", AsyncMock()),
                # The network is gone, so the registry never hears the result.
                patch.object(
                    TaktAgent, "_send_job_event", AsyncMock(side_effect=RuntimeError("offline"))
                ),
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=True)),
            ):
                asyncio.run(agent._power_action(AsyncMock(), job))
            # It must survive the reboot on disk so the next boot can replay it.
            state_at_power["state"] = agent.state_path.read_text(encoding="utf-8")
            self.assertIn("succeeded", state_at_power["state"])
            self.assertIn("a" * 24, state_at_power["state"])

    def test_power_action_defers_while_a_run_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = self._agent(root, helper_verbs=frozenset({"power"}))
            job = {"id": "b" * 24, "action": "reboot_device", "lease_id": "l", "payload": {}}
            with (
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=True)),
                patch.object(
                    TaktAgent,
                    "_acquire_maintenance",
                    AsyncMock(side_effect=DeferredJob("Waiting for TAKT to be ready")),
                ),
                patch.object(TaktAgent, "_call_helper", AsyncMock()) as helper,
                self.assertRaises(DeferredJob),
            ):
                asyncio.run(agent._power_action(AsyncMock(), job))
            self.assertEqual(helper.call_count, 0, "a busy timer must never be powered down")

    def test_override_skips_the_maintenance_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = self._agent(Path(temporary_directory))
            job = {"id": "c" * 24, "action": "stop_takt", "payload": {"override": True}}
            with (
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=True)),
                patch.object(TaktAgent, "_acquire_maintenance", AsyncMock()) as acquire,
            ):
                asyncio.run(agent._require_safe_state(AsyncMock(), job, "Stop"))
            self.assertEqual(acquire.call_count, 0)

    def test_safe_state_gate_is_skipped_when_the_service_is_already_down(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = self._agent(Path(temporary_directory))
            job = {"id": "d" * 24, "action": "start_takt", "payload": {}}
            with (
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=False)),
                patch.object(TaktAgent, "_acquire_maintenance", AsyncMock()) as acquire,
            ):
                asyncio.run(agent._require_safe_state(AsyncMock(), job, "Start"))
            self.assertEqual(
                acquire.call_count, 0, "a stopped service has no run to interrupt"
            )

    def test_health_report_never_opens_the_live_database_for_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent = self._agent(root)
            connection = sqlite3.connect(root / "takt.db")
            connection.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            with (
                patch.object(
                    TaktAgent,
                    "_local_health",
                    AsyncMock(return_value={"ok": True, "state": "ready"}),
                ),
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=True)),
            ):
                report = asyncio.run(agent._health_report(AsyncMock()))
            statuses = {check["id"]: check["status"] for check in report["checks"]}
            self.assertEqual(statuses["database_integrity"], "ok")
            self.assertEqual(statuses["takt_service"], "ok")
            self.assertTrue(report["summary"]["healthy"])
            self.assertIsInstance(report["summary"]["healthy"], bool)

    def test_failing_checks_mark_the_report_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = self._agent(Path(temporary_directory))
            with (
                patch.object(
                    TaktAgent,
                    "_local_health",
                    AsyncMock(return_value={"ok": False, "state": "unreachable"}),
                ),
                patch.object(TaktAgent, "_service_is_active", AsyncMock(return_value=False)),
            ):
                report = asyncio.run(agent._health_report(AsyncMock()))
            self.assertFalse(report["summary"]["healthy"])
            self.assertGreaterEqual(report["summary"]["fail"], 2)

    def test_diagnostics_bundle_is_redacted_and_excludes_the_identity_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            token = "b" * 64
            enrollment_code = "TAKT-4tPk9xQm2LwZaB7cRn3D"
            logs = root / "logs"
            logs.mkdir()
            (logs / "takt.log").write_text(
                f"takt started\nAuthorization: Bearer {token}\npsk=hunter2hunter2\n",
                encoding="utf-8",
            )
            config_path = root / "agent.toml"
            config_path.write_text(
                '[agent]\nregistry_url = "http://registry.test"\n'
                f'enrollment_code = "{enrollment_code}"\n',
                encoding="utf-8",
            )
            agent = self._agent(root)
            agent.config.config_path = config_path
            agent.config.enrollment_code = enrollment_code
            agent.identity.device_token = token

            bundle = agent._build_diagnostics_bundle({"collected_at": "now", "checks": []})
            try:
                with tarfile.open(bundle, "r:gz") as archive:
                    names = archive.getnames()
                    contents = b""
                    for name in names:
                        extracted = archive.extractfile(name)
                        if extracted is not None:
                            contents += extracted.read()
                text = contents.decode("utf-8", errors="replace")
                self.assertNotIn(token, text)
                self.assertNotIn("hunter2hunter2", text)
                self.assertNotIn(enrollment_code, text)
                self.assertIn("takt started", text, "ordinary log lines must survive")
                self.assertNotIn(
                    "agent-identity.json", " ".join(names), "the identity file is never bundled"
                )
                self.assertIn("manifest.json", names)
            finally:
                bundle.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
