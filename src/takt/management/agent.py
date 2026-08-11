from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, TCPConnector

from takt import __version__
from takt.logging_config import configure_logging

LOGGER = logging.getLogger(__name__)


def expanded(value: str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(slots=True)
class AgentConfig:
    registry_url: str = ""
    enrollment_code: str = ""
    device_name: str = ""
    verify_tls: bool = True
    poll_seconds: float = 10.0
    mirror_seconds: float = 60.0
    identity_path: Path = field(
        default_factory=lambda: expanded("~/.config/takt/agent-identity.json")
    )
    database_path: Path = field(default_factory=lambda: expanded("~/.local/share/takt/takt.db"))
    data_directory: Path = field(default_factory=lambda: expanded("~/.local/share/takt-agent"))
    release_root: Path = field(default_factory=lambda: expanded("~/.local/share/takt/releases"))
    current_link: Path = field(default_factory=lambda: expanded("~/.local/share/takt/current"))
    release_environment: Path = field(
        default_factory=lambda: expanded("~/.config/takt/release.env")
    )
    health_url: str = "http://127.0.0.1/health"
    service_name: str = "takt.service"

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        config = cls()
        if path.exists():
            with path.open("rb") as handle:
                raw = tomllib.load(handle).get("agent", {})
            for key in (
                "registry_url",
                "enrollment_code",
                "device_name",
                "health_url",
                "service_name",
            ):
                if key in raw:
                    setattr(config, key, str(raw[key]))
            for key in ("verify_tls",):
                if key in raw:
                    setattr(config, key, bool(raw[key]))
            for key in ("poll_seconds", "mirror_seconds"):
                if key in raw:
                    setattr(config, key, max(2.0, float(raw[key])))
            for key in (
                "identity_path",
                "database_path",
                "data_directory",
                "release_root",
                "current_link",
                "release_environment",
            ):
                if key in raw:
                    setattr(config, key, expanded(str(raw[key])))
        config.registry_url = os.environ.get("TAKT_REGISTRY_URL", config.registry_url).rstrip("/")
        config.enrollment_code = os.environ.get("TAKT_ENROLLMENT_CODE", config.enrollment_code)
        config.device_name = os.environ.get("TAKT_DEVICE_NAME", config.device_name)
        return config


@dataclass(slots=True)
class Identity:
    device_id: str
    device_token: str | None = None

    @classmethod
    def load_or_create(cls, path: Path) -> Identity:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(str(raw["device_id"]), raw.get("device_token"))
        identity = cls(str(uuid.uuid4()))
        identity.save(path)
        return identity

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"device_id": self.device_id, "device_token": self.device_token},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)


class DeferredJob(Exception):
    pass


class RolledBackJob(Exception):
    pass


class TaktAgent:
    def __init__(self, config: AgentConfig) -> None:
        if not config.registry_url:
            raise ValueError("registry_url is missing from the agent configuration")
        self.config = config
        self.config.data_directory.mkdir(parents=True, exist_ok=True)
        self.config.release_root.mkdir(parents=True, exist_ok=True)
        self.identity = Identity.load_or_create(config.identity_path)
        self._last_mirror_signature: tuple[int, int, int, int] | None = None
        self._last_mirror_time = 0.0

    async def run(self, *, once: bool = False) -> None:
        connector = TCPConnector(ssl=None if self.config.verify_tls else False)
        timeout = ClientTimeout(total=120, connect=20)
        async with ClientSession(connector=connector, timeout=timeout) as session:
            await self._ensure_enrolled(session)
            while True:
                try:
                    await self._cycle(session)
                except Exception:
                    LOGGER.exception("agent_cycle_failed")
                if once:
                    return
                await asyncio.sleep(self.config.poll_seconds)

    async def _ensure_enrolled(self, session: ClientSession) -> None:
        if self.identity.device_token:
            return
        if not self.config.enrollment_code:
            raise RuntimeError("Agent is not enrolled and no enrollment_code is configured.")
        hostname = socket.gethostname()
        payload = {
            "enrollment_code": self.config.enrollment_code,
            "device_id": self.identity.device_id,
            "name": self.config.device_name or hostname,
            "hostname": hostname,
        }
        async with session.post(
            f"{self.config.registry_url}/agent/enroll", json=payload
        ) as response:
            if response.status != 201:
                raise RuntimeError(f"Enrollment failed: {await response.text()}")
            body = await response.json()
        self.identity.device_token = str(body["device_token"])
        self.identity.save(self.config.identity_path)
        LOGGER.info("agent_enrolled device_id=%s", self.identity.device_id)

    async def _cycle(self, session: ClientSession) -> None:
        status = await self._status(session)
        async with session.post(
            f"{self.config.registry_url}/agent/heartbeat",
            json=status,
            headers=self._headers(),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Heartbeat failed: {await response.text()}")
            job = (await response.json()).get("job")
        if job:
            await self._execute_job(session, job)
        loop_time = asyncio.get_running_loop().time()
        if loop_time - self._last_mirror_time >= self.config.mirror_seconds:
            await self._mirror_if_changed(session)
            self._last_mirror_time = loop_time

    async def _status(self, session: ClientSession) -> dict[str, Any]:
        health: dict[str, Any] = {"ok": False, "state": "unreachable"}
        try:
            async with session.get(
                self.config.health_url, timeout=ClientTimeout(total=3)
            ) as response:
                if response.status == 200:
                    health = await response.json()
        except Exception:
            pass
        disk = shutil.disk_usage(self.config.data_directory)
        return {
            "name": self.config.device_name or socket.gethostname(),
            "hostname": socket.gethostname(),
            "app_version": health.get("version"),
            "agent_version": __version__,
            "health": health,
            "model": self._model(),
            "os": platform.platform(),
            "architecture": platform.machine(),
            "uptime_seconds": self._uptime_seconds(),
            "disk_free_bytes": disk.free,
            "temperature_c": self._temperature(),
        }

    async def _execute_job(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        action = str(job["action"])
        await self._job_event(session, job_id, "running", 1, f"Starting {action}")
        try:
            if action == "install_release":
                await self._install_release(session, job)
            elif action == "mirror_now":
                await self._upload_mirror(session)
            elif action == "restart_takt":
                await self._systemctl("restart", self.config.service_name)
            elif action == "reboot":
                await self._job_event(session, job_id, "succeeded", 100, "Reboot requested")
                await self._systemctl("reboot")
                return
            else:
                raise RuntimeError(f"Unsupported job action: {action}")
        except DeferredJob as error:
            await self._job_event(session, job_id, "queued", 0, str(error))
            return
        except RolledBackJob as error:
            await self._job_event(session, job_id, "rolled_back", 100, str(error))
            return
        except Exception as error:
            LOGGER.exception("job_failed id=%s action=%s", job_id, action)
            await self._job_event(session, job_id, "failed", 100, str(error))
            return
        await self._job_event(session, job_id, "succeeded", 100, f"{action} completed")

    async def _install_release(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        release = job.get("release")
        if not isinstance(release, dict):
            raise RuntimeError("Release metadata is missing.")
        version = str(release["version"])
        if not version or ".." in version or "/" in version:
            raise RuntimeError("Release version is unsafe.")
        health = await self._local_health(session)
        if health.get("state") != "ready":
            raise DeferredJob(
                f"Waiting for TAKT to be ready (current state: {health.get('state', 'unknown')})."
            )
        if health.get("ok") and health.get("version") == version:
            await self._job_event(
                session, job_id, "running", 95, f"TAKT {version} is already active"
            )
            return
        await self._job_event(session, job_id, "running", 5, "Downloading release")
        artifact = self.config.data_directory / f"{job_id}.tar.gz"
        try:
            digest = hashlib.sha256()
            async with session.get(
                f"{self.config.registry_url}/agent/jobs/{job_id}/artifact",
                headers=self._headers(),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"Release download failed: {await response.text()}")
                with artifact.open("wb") as handle:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        digest.update(chunk)
                        handle.write(chunk)
            if digest.hexdigest() != release["sha256"]:
                raise RuntimeError("Downloaded release checksum does not match.")
            await self._job_event(session, job_id, "running", 25, "Preparing release")
            project_directory = await asyncio.to_thread(
                self._prepare_release, artifact, version, job_id
            )
            health = await self._local_health(session)
            if health.get("state") != "ready":
                raise DeferredJob(
                    "Release is prepared; waiting until TAKT is ready before activation."
                )
            previous_target = (
                self.config.current_link.resolve()
                if self.config.current_link.is_symlink()
                else None
            )
            previous_version = self._read_release_version()
            activated = False
            await self._job_event(session, job_id, "running", 70, "Stopping TAKT safely")
            await self._systemctl("stop", self.config.service_name)
            try:
                await self._job_event(session, job_id, "running", 75, "Backing up run data")
                await asyncio.to_thread(self._backup_before_update, version)
                await self._job_event(session, job_id, "running", 80, "Activating release")
                self._switch_current(project_directory)
                self._write_release_version(version)
                activated = True
                await self._systemctl("start", self.config.service_name)
                await self._wait_for_health(session, version)
            except Exception as error:
                if activated and previous_target is not None:
                    self._switch_current(previous_target)
                    self._write_release_version(previous_version or "unknown")
                await self._systemctl("restart", self.config.service_name)
                raise RolledBackJob(
                    f"Release {version} failed health checks and was rolled back: {error}"
                ) from error
            await self._job_event(session, job_id, "running", 95, f"TAKT {version} is healthy")
            await self._upload_mirror(session)
        finally:
            artifact.unlink(missing_ok=True)

    def _prepare_release(self, artifact: Path, version: str, job_id: str) -> Path:
        staging = self.config.release_root / f".{version}-{job_id}.staging"
        destination = self.config.release_root / version
        if (
            self.config.current_link.is_symlink()
            and self.config.current_link.resolve() == destination
        ):
            raise RuntimeError(f"Release {version} is already the active release.")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            with tarfile.open(artifact, "r:gz") as archive:
                members = archive.getmembers()
                total = 0
                for member in members:
                    path = Path(member.name)
                    total += max(member.size, 0)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise RuntimeError(f"Unsafe archive member: {member.name}")
                    if total > 500 * 1024 * 1024:
                        raise RuntimeError("Expanded release is too large.")
                archive.extractall(staging, members=members)
            candidates = [staging, *[path.parent for path in staging.glob("*/pyproject.toml")]]
            project = next(
                (path for path in candidates if (path / "pyproject.toml").is_file()), None
            )
            if project is None:
                raise RuntimeError("Release does not contain pyproject.toml.")
            venv = project / ".venv"
            active_venv = self.config.current_link / ".venv"
            if active_venv.is_dir():
                shutil.copytree(active_venv, venv, symlinks=True)
            else:
                subprocess.run(
                    ["python3", "-m", "venv", "--system-site-packages", str(venv)],
                    check=True,
                    timeout=180,
                )
            subprocess.run(
                [
                    str(venv / "bin" / "python"),
                    "-m",
                    "pip",
                    "install",
                    "--no-input",
                    "--no-deps",
                    "--no-build-isolation",
                    "-e",
                    ".",
                ],
                cwd=project,
                check=True,
                timeout=600,
            )
            if destination.exists():
                shutil.rmtree(destination)
            if project == staging:
                staging.replace(destination)
                return destination
            project.replace(destination)
            shutil.rmtree(staging)
            return destination
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _backup_before_update(self, version: str) -> None:
        if not self.config.database_path.exists():
            return
        backup = (
            self.config.database_path.parent
            / "backups"
            / f"pre-update-{version}-{uuid.uuid4().hex[:8]}.db"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.config.database_path)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    async def _mirror_if_changed(self, session: ClientSession) -> None:
        signature = self._database_signature()
        if signature is not None and signature != self._last_mirror_signature:
            await self._upload_mirror(session)

    async def _upload_mirror(self, session: ClientSession) -> None:
        if not self.config.database_path.exists():
            raise RuntimeError("TAKT database does not exist yet.")
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="takt-mirror-", suffix=".sqlite3", dir=self.config.data_directory
        )
        os.close(file_descriptor)
        snapshot = Path(temporary_name)
        try:
            await asyncio.to_thread(self._create_snapshot, snapshot)
            digest = await asyncio.to_thread(self._sha256, snapshot)
            with snapshot.open("rb") as handle:
                async with session.post(
                    f"{self.config.registry_url}/agent/mirror",
                    data=handle,
                    headers={**self._headers(), "X-TAKT-SHA256": digest},
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Mirror upload failed: {await response.text()}")
            self._last_mirror_signature = self._database_signature()
            LOGGER.info("database_mirrored sha256=%s", digest)
        finally:
            snapshot.unlink(missing_ok=True)

    def _create_snapshot(self, target: Path) -> None:
        source = sqlite3.connect(self.config.database_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def _database_signature(self) -> tuple[int, int, int, int] | None:
        if not self.config.database_path.exists():
            return None
        database = self.config.database_path.stat()
        wal_path = Path(f"{self.config.database_path}-wal")
        wal = wal_path.stat() if wal_path.exists() else None
        return (
            database.st_mtime_ns,
            database.st_size,
            wal.st_mtime_ns if wal else 0,
            wal.st_size if wal else 0,
        )

    async def _local_health(self, session: ClientSession) -> dict[str, Any]:
        try:
            async with session.get(
                self.config.health_url, timeout=ClientTimeout(total=3)
            ) as response:
                if response.status == 200:
                    return await response.json()
        except Exception:
            pass
        return {"state": "unreachable"}

    async def _wait_for_health(self, session: ClientSession, version: str) -> None:
        last: dict[str, Any] = {}
        for _ in range(30):
            await asyncio.sleep(1)
            last = await self._local_health(session)
            if last.get("ok") and last.get("version") == version:
                return
        raise RuntimeError(f"health endpoint did not report version {version}: {last}")

    async def _job_event(
        self,
        session: ClientSession,
        job_id: str,
        status: str,
        progress: int,
        message: str,
    ) -> None:
        async with session.post(
            f"{self.config.registry_url}/agent/jobs/{job_id}",
            json={"status": status, "progress": progress, "message": message},
            headers=self._headers(),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Could not report job progress: {await response.text()}")

    async def _systemctl(self, *arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            "/usr/bin/systemctl",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        if process.returncode:
            raise RuntimeError(output.decode("utf-8", errors="replace").strip())

    def _switch_current(self, target: Path) -> None:
        self.config.current_link.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.current_link.with_name(f".current-{uuid.uuid4().hex}")
        temporary.symlink_to(target)
        temporary.replace(self.config.current_link)

    def _write_release_version(self, version: str) -> None:
        self.config.release_environment.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.release_environment.with_suffix(".tmp")
        temporary.write_text(f"TAKT_RELEASE_VERSION={version}\n", encoding="utf-8")
        temporary.replace(self.config.release_environment)

    def _read_release_version(self) -> str | None:
        try:
            line = self.config.release_environment.read_text(encoding="utf-8").strip()
            return line.partition("=")[2] or None
        except OSError:
            return None

    def _headers(self) -> dict[str, str]:
        assert self.identity.device_token is not None
        return {
            "X-Device-ID": self.identity.device_id,
            "Authorization": f"Bearer {self.identity.device_token}",
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _model() -> str:
        path = Path("/proc/device-tree/model")
        if path.exists():
            return path.read_text(encoding="utf-8").rstrip("\x00")
        return platform.node()

    @staticmethod
    def _uptime_seconds() -> int | None:
        try:
            return int(float(Path("/proc/uptime").read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _temperature() -> float | None:
        try:
            value = Path("/sys/class/thermal/thermal_zone0/temp").read_text()
            return round(int(value) / 1000, 1)
        except (OSError, ValueError):
            return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAKT Raspberry Pi management agent")
    parser.add_argument(
        "--config", default="~/.config/takt/agent.toml", help="agent TOML configuration"
    )
    parser.add_argument("--once", action="store_true", help="run one heartbeat cycle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging("takt-agent")
    try:
        config = AgentConfig.load(expanded(args.config))
        asyncio.run(TaktAgent(config).run(once=args.once))
    except KeyboardInterrupt:
        return 0
    except Exception:
        LOGGER.exception("agent_stopped")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
