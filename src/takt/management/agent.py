from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import ipaddress
import json
import logging
import os
import platform
import random
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tarfile
import tempfile
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector

from takt import __version__
from takt.fleet_actions import (
    DIAGNOSTICS_CAPABILITY,
    HEALTH_CHECKS_CAPABILITY,
    LEASED_JOBS_CAPABILITY,
    POWER_CONTROL_CAPABILITY,
    RUN_CURATION_CAPABILITY,
    SERVICE_CONTROL_CAPABILITY,
    WIFI_PROFILE_CAPABILITY,
)
from takt.logging_config import configure_logging
from takt.management.redaction import REDACTION_VERSION, redact_mapping, redact_text
from takt.protocol import PROTOCOL_VERSION

LOGGER = logging.getLogger(__name__)
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
MAX_RELEASE_SIZE = 250 * 1024 * 1024
# Strips embedded basic-auth credentials from index URLs (e.g. a private
# package index) before pip failure output reaches the job log.
_PIP_CREDENTIAL_URL = re.compile(r"://[^/\s@]+@")
_FAST_INSTALL_TIMEOUT = 600
_DEPENDENCY_INSTALL_TIMEOUT = 900
DOWNLOAD_PROGRESS_INTERVAL_SECONDS = 1.0
MAX_DIAGNOSTICS_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTICS_MEMBER_BYTES = 2 * 1024 * 1024
MAX_LOG_CHARACTERS = 400_000
MAX_PENDING_RESULT_ATTEMPTS = 20
MAX_JOBS_PER_CYCLE = 5


def _now() -> float:
    return time.monotonic()


def _loopback_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized.partition("%")[0]).is_loopback
    except ValueError:
        return False


def expanded(value: str) -> Path:
    return Path(value).expanduser().resolve()


def expanded_symlink_path(value: str) -> Path:
    """Like `expanded()`, but never dereferences the final path component.

    Only for `current_link`, which `_switch_current` treats as a stable
    location it atomically replaces with a new symlink. `expanded()`'s
    `Path.resolve()` fully dereferences an *existing* symlink at that path,
    which would silently turn "the current-link path" into "whatever real
    release directory it currently points at" the moment current_link
    already exists (i.e. on every agent start after the very first
    successful install) -- and every later install would then try to
    atomically replace that real directory with a symlink, which the OS
    refuses (IsADirectoryError), deep into the install, after the live TAKT
    service has already been stopped.
    """
    path = Path(value).expanduser()
    return path.parent.resolve() / path.name


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def durable_unlink(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(slots=True)
class AgentConfig:
    registry_url: str = ""
    enrollment_code: str = ""
    device_name: str = ""
    verify_tls: bool = True
    allow_insecure_http: bool = False
    poll_seconds: float = 10.0
    mirror_seconds: float = 60.0
    identity_path: Path = field(
        default_factory=lambda: expanded("~/.config/takt/agent-identity.json")
    )
    database_path: Path = field(default_factory=lambda: expanded("~/.local/share/takt/takt.db"))
    data_directory: Path = field(default_factory=lambda: expanded("~/.local/share/takt-agent"))
    release_root: Path = field(default_factory=lambda: expanded("~/.local/share/takt/releases"))
    current_link: Path = field(
        default_factory=lambda: expanded_symlink_path("~/.local/share/takt/current")
    )
    release_environment: Path = field(
        default_factory=lambda: expanded("~/.config/takt/release.env")
    )
    maintenance_marker: Path = field(
        default_factory=lambda: expanded("~/.local/share/takt/maintenance.json")
    )
    wifi_helper_path: Path = field(
        default_factory=lambda: Path("/usr/local/libexec/takt-wifi-helper")
    )
    maintenance_helper_path: Path = field(
        default_factory=lambda: Path("/usr/local/libexec/takt-maintenance-helper")
    )
    log_directory: Path = field(default_factory=lambda: expanded("~/.local/state/takt/logs"))
    health_url: str = "http://127.0.0.1/health"
    service_name: str = "takt.service"
    agent_service_name: str = "takt-agent.service"
    ca_bundle: Path | None = None
    config_path: Path | None = None

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
                "agent_service_name",
            ):
                if key in raw:
                    setattr(config, key, str(raw[key]))
            for key in ("verify_tls", "allow_insecure_http"):
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
                "release_environment",
                "maintenance_marker",
                "wifi_helper_path",
                "maintenance_helper_path",
                "log_directory",
                "ca_bundle",
            ):
                if key in raw:
                    setattr(config, key, expanded(str(raw[key])))
            if "current_link" in raw:
                config.current_link = expanded_symlink_path(str(raw["current_link"]))
        config.config_path = path
        config.registry_url = os.environ.get("TAKT_REGISTRY_URL", config.registry_url).rstrip("/")
        config.enrollment_code = os.environ.get("TAKT_ENROLLMENT_CODE", config.enrollment_code)
        config.device_name = os.environ.get("TAKT_DEVICE_NAME", config.device_name)
        insecure_http = os.environ.get("TAKT_REGISTRY_ALLOW_INSECURE_HTTP")
        if insecure_http is not None:
            normalized = insecure_http.strip().lower()
            if normalized not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
                raise ValueError("TAKT_REGISTRY_ALLOW_INSECURE_HTTP must be true or false")
            config.allow_insecure_http = normalized in {"1", "true", "yes", "on"}
        return config


@dataclass(slots=True)
class Identity:
    device_id: str
    device_token: str
    enrolled: bool = False

    @classmethod
    def load_or_create(cls, path: Path) -> Identity:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            token_missing = not raw.get("device_token")
            identity = cls(
                str(raw["device_id"]),
                str(raw.get("device_token") or secrets_token()),
                bool(raw.get("enrolled", bool(raw.get("device_token")))),
            )
            if token_missing:
                identity.save(path)
            return identity
        identity = cls(str(uuid.uuid4()), secrets_token(), False)
        identity.save(path)
        return identity

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            json.dumps(
                {
                    "device_id": self.device_id,
                    "device_token": self.device_token,
                    "enrolled": self.enrolled,
                },
                indent=2,
            )
            + "\n",
        )


class DeferredJob(Exception):
    pass


class RolledBackJob(Exception):
    pass


class CancelledJob(Exception):
    pass


class RetryableJob(Exception):
    pass


class StaleJobResult(Exception):
    pass


def secrets_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


@dataclass(slots=True)
class AgentState:
    pending_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_mirror_signature: tuple[int, int, int, int] | None = None

    @classmethod
    def load(cls, path: Path) -> AgentState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            signature = raw.get("last_mirror_signature")
            return cls(
                pending_results={
                    str(key): dict(value)
                    for key, value in dict(raw.get("pending_results", {})).items()
                },
                last_mirror_signature=(
                    (
                        int(signature[0]),
                        int(signature[1]),
                        int(signature[2]),
                        int(signature[3]),
                    )
                    if isinstance(signature, list) and len(signature) == 4
                    else None
                ),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            json.dumps(
                {
                    "pending_results": self.pending_results,
                    "last_mirror_signature": list(self.last_mirror_signature)
                    if self.last_mirror_signature
                    else None,
                },
                indent=2,
            )
            + "\n",
        )


class TaktAgent:
    def __init__(self, config: AgentConfig) -> None:
        if not config.registry_url:
            raise ValueError("registry_url is missing from the agent configuration")
        registry = urlsplit(config.registry_url)
        if (
            registry.scheme not in {"http", "https"}
            or not registry.hostname
            or registry.username
            or registry.password
            or registry.path not in {"", "/"}
            or registry.query
            or registry.fragment
        ):
            raise ValueError("registry_url must be a plain HTTP(S) server URL")
        if (
            registry.scheme == "http"
            and not _loopback_hostname(registry.hostname)
            and not config.allow_insecure_http
        ):
            raise ValueError(
                "Remote HTTP registry connections are disabled. Use HTTPS, or explicitly "
                "set allow_insecure_http = true for HTTP over a private VPN/isolated LAN."
            )
        self.config = config
        self._registry_transport = (
            "https"
            if registry.scheme == "https"
            else "loopback-http"
            if _loopback_hostname(registry.hostname)
            else "insecure-http-opt-in"
        )
        self.config.data_directory.mkdir(parents=True, exist_ok=True)
        self.config.release_root.mkdir(parents=True, exist_ok=True)
        self.identity = Identity.load_or_create(config.identity_path)
        self.state_path = self.config.data_directory / "state.json"
        self.update_journal_path = self.config.data_directory / "update-journal.json"
        self.state = AgentState.load(self.state_path)
        self._last_mirror_signature = self.state.last_mirror_signature
        self._last_mirror_time = 0.0
        self._session_id = uuid.uuid4().hex
        self._heartbeat_sequence = 0
        self._registry_rtt_ms: int | None = None
        self._connection_recoveries = 0
        self._active_job: dict[str, Any] | None = None
        self._recovery_error: str | None = None
        self._wifi_profile_capability = self._probe_wifi_profile_capability()
        self._helper_verbs = self._probe_maintenance_helper()
        self._active_health_report: dict[str, Any] | None = None

    async def run(self, *, once: bool = False, enroll_only: bool = False) -> None:
        ssl_option: ssl.SSLContext | bool = True
        if not self.config.verify_tls:
            ssl_option = False
        elif self.config.ca_bundle:
            ssl_option = ssl.create_default_context(cafile=str(self.config.ca_bundle))
        connector = TCPConnector(
            ssl=ssl_option,
            ttl_dns_cache=60,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        timeout = ClientTimeout(total=None, connect=20, sock_connect=20, sock_read=180)
        async with ClientSession(connector=connector, timeout=timeout) as session:
            failures = 0
            while True:
                try:
                    await self._ensure_enrolled(session)
                    if enroll_only:
                        return
                    try:
                        await self._recover_interrupted_update(session)
                        self._recovery_error = None
                    except Exception as recovery_error:
                        self._recovery_error = str(recovery_error)[:500]
                        with contextlib.suppress(Exception):
                            await self._report_recovery_failure(session)
                        raise
                    await self._cycle(session)
                    if failures:
                        self._connection_recoveries += 1
                        LOGGER.info("registry_connection_recovered failures=%s", failures)
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    failures += 1
                    if once or enroll_only:
                        raise
                    delay = self._reconnect_delay(failures)
                    LOGGER.warning(
                        "registry_connection_failed attempt=%s retry_seconds=%.1f error=%s",
                        failures,
                        delay,
                        error,
                    )
                    if self._recovery_error:
                        await self._wait_with_recovery_heartbeats(session, delay)
                    else:
                        await asyncio.sleep(delay)
                    continue
                if once:
                    return
                await asyncio.sleep(self.config.poll_seconds * random.uniform(0.9, 1.1))

    def _reconnect_delay(self, failures: int) -> float:
        # Capped well under a minute: queued jobs no longer time out on the
        # registry side, but a fast reconnect still matters for the
        # operator's sense of responsiveness and for lease renewal during an
        # in-progress job.
        base = min(60.0, self.config.poll_seconds * (2 ** min(failures - 1, 5)))
        return base * random.uniform(0.8, 1.2)

    async def _ensure_enrolled(self, session: ClientSession) -> None:
        if self.identity.enrolled:
            return
        if not self.config.enrollment_code:
            raise RuntimeError("Agent is not enrolled and no enrollment_code is configured.")
        hostname = socket.gethostname()
        payload = {
            "enrollment_code": self.config.enrollment_code,
            "device_id": self.identity.device_id,
            "name": self.config.device_name or hostname,
            "hostname": hostname,
            "device_token": self.identity.device_token,
        }
        async with session.post(
            f"{self.config.registry_url}/agent/enroll", json=payload
        ) as response:
            if response.status not in {200, 201}:
                raise RuntimeError(f"Enrollment failed: {await response.text()}")
            body = await response.json()
        if str(body["device_token"]) != self.identity.device_token:
            raise RuntimeError("Registry returned a different device secret during enrollment.")
        self.identity.enrolled = True
        self.identity.save(self.config.identity_path)
        self._clear_enrollment_code()
        LOGGER.info("agent_enrolled device_id=%s", self.identity.device_id)

    async def _cycle(self, session: ClientSession) -> None:
        # Heartbeat (and the job claim riding on it) always goes out first and
        # unconditionally, so a device that is online and polling normally
        # never looks broken to the operator because of an unrelated problem
        # reporting an old job's result. Pending results are flushed
        # afterwards, and failures there are logged rather than raised so
        # they can never block the next heartbeat.
        job = await self._heartbeat(session)
        jobs_handled = 0
        while job and jobs_handled < MAX_JOBS_PER_CYCLE:
            job_id = str(job.get("id", ""))
            if job_id in self.state.pending_results:
                await self._safe_flush_pending_results(session, only=job_id)
            else:
                await self._execute_job(session, job)
            jobs_handled += 1
            job = await self._heartbeat(session)
        await self._safe_flush_pending_results(session)
        loop_time = asyncio.get_running_loop().time()
        if loop_time - self._last_mirror_time >= self.config.mirror_seconds:
            await self._mirror_if_changed(session)
            self._last_mirror_time = loop_time

    async def _heartbeat(self, session: ClientSession) -> dict[str, Any] | None:
        status = await self._status(session)
        started = asyncio.get_running_loop().time()
        async with session.post(
            f"{self.config.registry_url}/agent/heartbeat",
            json=status,
            headers=self._headers(),
            timeout=ClientTimeout(total=25, connect=10, sock_read=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Heartbeat failed: {await response.text()}")
            response_body = await response.json()
            job = response_body.get("job")
        self._registry_rtt_ms = round((asyncio.get_running_loop().time() - started) * 1000)
        self._heartbeat_sequence += 1
        registry_protocol = int(response_body.get("protocol_version", 0))
        if registry_protocol < PROTOCOL_VERSION:
            raise RuntimeError(
                f"Registry protocol {registry_protocol} is older than this agent's minimum "
                f"supported protocol {PROTOCOL_VERSION}; update the registry."
            )
        return job

    async def _safe_flush_pending_results(
        self, session: ClientSession, *, only: str | None = None
    ) -> None:
        try:
            await self._flush_pending_results(session, only=only)
        except Exception as error:
            LOGGER.warning("pending_result_flush_failed error=%s", error)

    async def _report_recovery_failure(self, session: ClientSession) -> None:
        status = await self._status(session)
        async with session.post(
            f"{self.config.registry_url}/agent/status",
            json=status,
            headers=self._headers(),
            timeout=ClientTimeout(total=25, connect=10, sock_read=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Recovery failure heartbeat failed: {await response.text()}")

    async def _wait_with_recovery_heartbeats(self, session: ClientSession, delay: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(self.config.poll_seconds, remaining))
            if loop.time() >= deadline:
                return
            with contextlib.suppress(Exception):
                await self._report_recovery_failure(session)

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
        recovery_payload = None
        if self._recovery_error:
            phase = "unknown"
            with contextlib.suppress(Exception):
                phase = str((self._load_update_journal() or {}).get("phase") or "unknown")[:64]
            recovery_payload = {
                "stuck": True,
                "error": self._recovery_error,
                "phase": phase,
            }
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
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": self._capabilities(),
            "agent_session_id": self._session_id,
            "boot_id": self._boot_id(),
            "heartbeat_sequence": self._heartbeat_sequence,
            "poll_seconds": self.config.poll_seconds,
            "registry_rtt_ms": self._registry_rtt_ms,
            "wifi_signal_dbm": self._wifi_signal_dbm(),
            "connection_recoveries": self._connection_recoveries,
            "registry_transport": self._registry_transport,
            "mirror_pending": self._database_signature() != self._last_mirror_signature,
            "update_recovery": recovery_payload,
        }

    async def _execute_job(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        action = str(job["action"])
        lease_id = str(job.get("lease_id") or "")
        if not JOB_ID_PATTERN.fullmatch(job_id) or not lease_id:
            raise RuntimeError("Registry returned an invalid job identity or lease.")
        self._active_job = {
            "id": job_id,
            "lease_id": lease_id,
            "progress": 1,
            "message": f"Starting {action}",
            "control_lost": False,
            "cancel_requested": False,
        }
        await self._progress_job(
            session,
            job_id,
            1,
            f"Starting {action}",
            stage="waiting_for_safe_state" if action == "install_release" else None,
        )
        renew_task = asyncio.create_task(self._renew_job_lease(session), name=f"renew-job-{job_id}")
        try:
            if action == "install_release":
                await self._install_release(session, job)
            elif action == "mirror_now":
                await self._upload_mirror(session)
            elif action == "curate_run":
                await self._curate_run(session, job)
            elif action == "restart_takt":
                await self._restart_takt(session, job)
            elif action in {"start_takt", "stop_takt"}:
                await self._service_action(session, job)
            elif action in {"reboot_device", "shutdown_device"}:
                # Reports its own terminal result before the box goes down, so it
                # must not fall through to the trailing success report.
                await self._power_action(session, job)
                return
            elif action == "collect_diagnostics":
                await self._collect_diagnostics(session, job)
            elif action == "run_health_checks":
                await self._run_health_checks(session, job)
            elif action == "add_wifi_network":
                await self._add_wifi_network(job)
            else:
                raise RuntimeError(f"Unsupported job action: {action}")
        except DeferredJob as error:
            await self._queue_job(session, job_id, str(error), stage="waiting_for_safe_state")
            return
        except RetryableJob as error:
            await self._queue_job(
                session, job_id, f"Temporary connection problem: {error}", stage="retryable_failure"
            )
            return
        except CancelledJob as error:
            await self._remember_result(session, job_id, "cancelled", str(error), stage="cancelled")
            self._remove_maintenance_marker()
            self._clear_update_journal(job_id)
            return

        except RolledBackJob as error:
            await self._remember_result(
                session, job_id, "rolled_back", str(error), stage="rolled_back"
            )
            self._clear_update_journal(job_id)
            return
        except Exception as error:
            LOGGER.exception("job_failed id=%s action=%s", job_id, action)
            if action == "install_release" and self.update_journal_path.exists():
                LOGGER.warning("update_recovery_scheduled id=%s", job_id)
                return
            await self._remember_result(
                session,
                job_id,
                "failed",
                str(error),
                stage="intervention_required" if action == "install_release" else None,
            )
            return
        finally:
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renew_task
            self._active_job = None
        health_report = self._active_health_report
        self._active_health_report = None
        await self._remember_result(
            session,
            job_id,
            "succeeded",
            f"{action} completed",
            stage="succeeded" if action == "install_release" else None,
            lease_id=lease_id,
            result=health_report,
        )
        if action == "install_release":
            self._clear_update_journal(job_id)

    async def _health_report(self, session: ClientSession) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def record(identifier: str, label: str, status: str, detail: str) -> None:
            checks.append(
                {
                    "id": identifier,
                    "label": label,
                    "status": status,
                    "detail": redact_text(detail)[:400],
                }
            )

        health = await self._local_health(session)
        record(
            "takt_service",
            "TAKT service",
            "ok" if await self._service_is_active() else "fail",
            f"{self.config.service_name} active state",
        )
        if health.get("ok"):
            record("app_health", "TAKT application", "ok", f"timer state {health.get('state')}")
        else:
            record(
                "app_health",
                "TAKT application",
                "fail",
                f"health endpoint reported {health.get('state', 'unreachable')}",
            )
        disk = shutil.disk_usage(self.config.data_directory)
        free_mb = disk.free // (1024 * 1024)
        record(
            "disk_space",
            "Disk space",
            "fail" if free_mb < 500 else "warn" if free_mb < 2048 else "ok",
            f"{free_mb} MB free",
        )
        temperature = self._temperature()
        if temperature is None:
            record("temperature", "Temperature", "skipped", "no thermal sensor")
        else:
            record(
                "temperature",
                "Temperature",
                "fail" if temperature >= 85 else "warn" if temperature >= 75 else "ok",
                f"{temperature} C",
            )
        record(*self._database_integrity_check())
        record(*self._clock_check())
        rtt = self._registry_rtt_ms
        record(
            "registry_link",
            "Registry link",
            "warn" if self._registry_transport == "insecure-http-opt-in" else "ok",
            f"{rtt} ms over {self._registry_transport}" if rtt is not None else "not measured yet",
        )
        record(
            "maintenance_helper",
            "Maintenance helper",
            "ok" if self._helper_verbs else "warn",
            f"verbs: {', '.join(sorted(self._helper_verbs)) or 'unavailable'}",
        )
        record(
            "gpio",
            "GPIO button",
            "ok" if health.get("hardware_available") else "warn",
            "reported by the TAKT application",
        )
        wifi_dbm = self._wifi_signal_dbm()
        record(
            "network",
            "Wireless link",
            "ok" if wifi_dbm is not None else "skipped",
            f"{wifi_dbm} dBm" if wifi_dbm is not None else "no wireless interface",
        )
        counts = {"ok": 0, "warn": 0, "fail": 0, "skipped": 0}
        for check in checks:
            counts[str(check["status"])] += 1
        return {
            "schema": 1,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # "healthy", not "ok": the per-status counts below already use "ok".
            "summary": {"healthy": counts["fail"] == 0, **counts},
            "checks": checks,
        }

    def _database_integrity_check(self) -> tuple[str, str, str, str]:
        identifier, label = "database_integrity", "Run database"
        if not self.config.database_path.exists():
            return identifier, label, "skipped", "no database yet"
        try:
            # Read-only so a live run is never disturbed by the check itself.
            connection = sqlite3.connect(f"file:{self.config.database_path}?mode=ro", uri=True)
            try:
                result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            finally:
                connection.close()
        except sqlite3.Error as error:
            return identifier, label, "fail", str(error)
        return identifier, label, "ok" if result == "ok" else "fail", result

    def _clock_check(self) -> tuple[str, str, str, str]:
        identifier, label = "clock", "System clock"
        try:
            completed = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                encoding="utf-8",
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return identifier, label, "skipped", str(error)
        if completed.returncode:
            return identifier, label, "skipped", "timedatectl unavailable"
        synchronized = completed.stdout.strip() == "yes"
        return (
            identifier,
            label,
            "ok" if synchronized else "warn",
            "NTP synchronized" if synchronized else "clock is not NTP synchronized",
        )

    def _build_diagnostics_bundle(self, health_report: dict[str, Any]) -> Path:
        """Assemble a redacted diagnostics archive.

        Every member comes from a named source; the bundle is never produced by
        walking a directory, so a stray secret file cannot be swept in.
        """
        secrets = [self.identity.device_token, self.config.enrollment_code]
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="takt-diagnostics-", suffix=".tar.gz", dir=self.config.data_directory
        )
        os.close(file_descriptor)
        bundle = Path(temporary_name)
        try:
            with tarfile.open(bundle, "w:gz") as archive:

                def add_text(name: str, text: str) -> None:
                    payload = redact_text(text, secrets=secrets).encode("utf-8")
                    if len(payload) > MAX_DIAGNOSTICS_MEMBER_BYTES:
                        payload = payload[-MAX_DIAGNOSTICS_MEMBER_BYTES:]
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    info.mode = 0o600
                    archive.addfile(info, io.BytesIO(payload))

                add_text(
                    "manifest.json",
                    json.dumps(
                        {
                            "schema": 1,
                            "redaction_version": REDACTION_VERSION,
                            "device_id": self.identity.device_id,
                            "hostname": socket.gethostname(),
                            "agent_version": __version__,
                            "app_version": self._read_release_version(),
                            "helper_verbs": sorted(self._helper_verbs),
                            "collected_at": health_report.get("collected_at"),
                        },
                        indent=2,
                    ),
                )
                add_text("health.json", json.dumps(health_report, indent=2))
                add_text(
                    "system/facts.json",
                    json.dumps(
                        {
                            "model": self._model(),
                            "os": platform.platform(),
                            "architecture": platform.machine(),
                            "uptime_seconds": self._uptime_seconds(),
                            "boot_id": self._boot_id(),
                            "temperature_c": self._temperature(),
                            "wifi_signal_dbm": self._wifi_signal_dbm(),
                            "disk": shutil.disk_usage(self.config.data_directory)._asdict(),
                        },
                        indent=2,
                    ),
                )
                for config_path, name in (
                    (self.config.config_path, "config/agent.toml.json"),
                ):
                    if config_path and config_path.is_file():
                        try:
                            with config_path.open("rb") as handle:
                                parsed = tomllib.load(handle)
                        except (OSError, tomllib.TOMLDecodeError):
                            continue
                        add_text(
                            name,
                            json.dumps(redact_mapping(parsed, secrets=secrets), indent=2),
                        )
                for log_name in ("takt.log", "takt-agent.log"):
                    log_path = self.config.log_directory / log_name
                    if log_path.is_file():
                        with log_path.open("r", encoding="utf-8", errors="replace") as log_handle:
                            add_text(f"logs/{log_name}", log_handle.read()[-MAX_LOG_CHARACTERS:])
                if "journal" in self._helper_verbs:
                    for unit in (self.config.service_name, self.config.agent_service_name):
                        try:
                            result = self._call_helper_sync(
                                "journal", {"unit": unit, "lines": 1000}
                            )
                        except RuntimeError as error:
                            add_text(f"journal/{unit}.txt", f"unavailable: {error}")
                            continue
                        add_text(f"journal/{unit}.txt", str(result.get("text", "")))
            if bundle.stat().st_size > MAX_DIAGNOSTICS_BUNDLE_BYTES:
                raise RuntimeError("Diagnostics bundle exceeded the size limit.")
            return bundle
        except Exception:
            bundle.unlink(missing_ok=True)
            raise

    def _call_helper_sync(self, verb: str, arguments: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            ["sudo", "-n", str(self.config.maintenance_helper_path)],
            input=json.dumps({"verb": verb, "arguments": arguments}, separators=(",", ":")),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        if completed.returncode:
            raise RuntimeError(f"Maintenance helper refused '{verb}'.")
        body = json.loads(completed.stdout)
        if not body.get("ok"):
            raise RuntimeError(str(body.get("error") or f"Maintenance helper refused '{verb}'."))
        return dict(body.get("result") or {})

    async def _require_safe_state(
        self, session: ClientSession, job: dict[str, Any], reason: str
    ) -> str | None:
        """Refuse to disturb a running or unsaved run unless explicitly overridden.

        The local maintenance lock only grants a lease in the `ready` timer state,
        so this is the authoritative safety gate regardless of what the operator's
        browser believed when the job was created.
        """
        job_id = str(job["id"])
        if bool((job.get("payload") or {}).get("override")):
            LOGGER.warning(
                "maintenance_override id=%s action=%s", job_id, job.get("action")
            )
            return None
        if not await self._service_is_active():
            # TAKT is not running, so there is no run to interrupt and the local
            # maintenance endpoint is unreachable by definition.
            return None
        return await self._acquire_maintenance(session, job_id, reason)

    async def _curate_run(self, session: ClientSession, job: dict[str, Any]) -> None:
        payload = job.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("Run curation payload is missing.")
        operation = payload.get("operation")
        run_id = payload.get("run_id")
        expected_updated_at = payload.get("expected_updated_at")
        if operation not in {"adjust_added_time", "delete"}:
            raise RuntimeError("Run curation operation is invalid.")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise RuntimeError("Run curation run id is invalid.")
        if not isinstance(expected_updated_at, str) or not expected_updated_at:
            raise RuntimeError("Run curation version is missing.")
        job_id = str(job["id"])
        await self._progress_job(
            session, job_id, 35, "Applying run correction", stage="applying"
        )
        local_base = self.config.health_url.rsplit("/health", 1)[0]
        request_body = {
            "command_id": job_id,
            "operation": operation,
            "run_id": run_id,
            "expected_updated_at": expected_updated_at,
            "desired_added_time_ms": payload.get("desired_added_time_ms"),
        }
        async with session.post(
            f"{local_base}/internal/run-curation",
            json=request_body,
            timeout=ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Local run curation failed: {await response.text()}")
            body = await response.json()
            if body.get("ok") is not True:
                raise RuntimeError(f"Local run curation failed: {body}")
            result = body.get("result")
        await self._progress_job(
            session, job_id, 75, "Refreshing run mirror", stage="refreshing_mirror"
        )
        await self._upload_mirror(session)
        self._active_health_report = result if isinstance(result, dict) else {"result": result}

    async def _restart_takt(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        current_health = await self._local_health(session)
        await self._require_safe_state(session, job, "Restart TAKT")
        expected_version = str(current_health.get("version") or self._read_release_version() or "")
        self._write_maintenance_marker(job_id, expected_version or "service-restart")
        try:
            await self._progress_job(session, job_id, 40, "Restarting TAKT", stage="applying")
            await self._systemctl("restart", self.config.service_name)
            await self._progress_job(
                session, job_id, 70, "Checking TAKT health", stage="verifying"
            )
            await self._wait_for_health(session, expected_version)
        finally:
            self._remove_maintenance_marker()

    async def _service_action(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        action = str(job["action"])
        operation = "start" if action == "start_takt" else "stop"
        if operation == "stop":
            await self._require_safe_state(session, job, "Stop TAKT")
        await self._progress_job(
            session, job_id, 40, f"Running {operation} on {self.config.service_name}",
            stage="applying",
        )
        # systemd treats starting a running unit (or stopping a stopped one) as a
        # no-op success, which is exactly the idempotency this job needs.
        await self._call_helper(
            "service", {"unit": self.config.service_name, "operation": operation}
        )
        await self._progress_job(session, job_id, 75, "Verifying service state", stage="verifying")
        if operation == "start":
            await self._wait_for_health(session, "")
        elif await self._service_is_active():
            raise RuntimeError(f"{self.config.service_name} is still active after stop.")

    async def _power_action(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        action = str(job["action"])
        lease_id = str(job.get("lease_id") or "")
        mode = "reboot" if action == "reboot_device" else "poweroff"
        stage = "rebooting" if mode == "reboot" else "powering_off"
        await self._require_safe_state(session, job, f"Fleet {mode}")
        message = (
            "Reboot requested; the device will reconnect shortly."
            if mode == "reboot"
            else "Shutdown requested; the device will leave the fleet until it is powered on."
        )
        await self._progress_job(session, job_id, 90, message, stage=stage)
        await self._call_helper("power", {"mode": mode})
        # Do not report success until the helper accepts the operation; a
        # refused helper call is handled as a failed job by the outer runner.
        await self._remember_result(
            session, job_id, "succeeded", message, lease_id=lease_id, stage="succeeded"
        )

    async def _run_health_checks(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        await self._progress_job(session, job_id, 30, "Running health checks", stage="checking")
        report = await self._health_report(session)
        self._active_health_report = report

    async def _collect_diagnostics(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        lease_id = str(job.get("lease_id") or "")
        await self._progress_job(
            session, job_id, 20, "Collecting diagnostics", stage="collecting"
        )
        report = await self._health_report(session)
        bundle = await asyncio.to_thread(self._build_diagnostics_bundle, report)
        try:
            size = bundle.stat().st_size
            digest = await asyncio.to_thread(self._sha256, bundle)
            await self._progress_job(
                session,
                job_id,
                70,
                f"Uploading diagnostics ({size} bytes)",
                stage="uploading",
            )
            with bundle.open("rb") as handle:
                async with session.put(
                    f"{self.config.registry_url}/agent/jobs/{job_id}/artifact",
                    data=handle,
                    headers={
                        **self._headers(),
                        "X-Job-Lease": lease_id,
                        "X-TAKT-SHA256": digest,
                    },
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"Diagnostics upload failed: {await response.text()}"
                        )
        finally:
            bundle.unlink(missing_ok=True)

    async def _add_wifi_network(self, job: dict[str, Any]) -> None:
        payload = job.get("payload")
        credential = job.get("credential")
        if not isinstance(payload, dict) or not isinstance(credential, dict):
            raise RuntimeError("Wi-Fi profile data is missing.")
        ssid = payload.get("ssid")
        password = credential.get("password")
        priority = payload.get("priority")
        if not isinstance(ssid, str) or not isinstance(password, str):
            raise RuntimeError("Wi-Fi profile data is invalid.")
        self._validate_wifi_network(ssid, password, priority)
        if not self._wifi_profile_capable():
            raise RuntimeError("Wi-Fi profile helper is unavailable.")
        self._assert_job_control()
        document = json.dumps(
            {"ssid": ssid, "password": password, "priority": 0},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            str(self.config.wifi_helper_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(process.communicate(document), timeout=30)
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise RuntimeError("Wi-Fi profile helper timed out.") from error
        if process.returncode:
            raise RuntimeError("Wi-Fi profile helper failed.")

    def _capabilities(self) -> list[str]:
        capabilities = [
            LEASED_JOBS_CAPABILITY,
            "maintenance-lock",
            "resumable-releases",
            "sqlite-mirror-v2",
            RUN_CURATION_CAPABILITY,
            HEALTH_CHECKS_CAPABILITY,
            DIAGNOSTICS_CAPABILITY,
        ]
        if self._wifi_profile_capable():
            capabilities.append(WIFI_PROFILE_CAPABILITY)
        if "service" in self._helper_verbs:
            capabilities.append(SERVICE_CONTROL_CAPABILITY)
        if "power" in self._helper_verbs:
            capabilities.append(POWER_CONTROL_CAPABILITY)
        return sorted(capabilities)

    def _wifi_profile_capable(self) -> bool:
        return self._wifi_profile_capability

    def _probe_wifi_profile_capability(self) -> bool:
        if (
            not self.config.wifi_helper_path.is_file()
            or not os.access(self.config.wifi_helper_path, os.X_OK)
            or shutil.which("nmcli") is None
        ):
            return False
        try:
            return (
                subprocess.run(
                    ["systemctl", "is-active", "--quiet", "NetworkManager"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=3,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            return False

    def _probe_maintenance_helper(self) -> frozenset[str]:
        """Ask the root helper which verbs it offers.

        The capability set is derived from the helper's own answer rather than
        assumed from the agent version, so a Pi whose installer predates the
        helper simply reports nothing and the registry disables those actions
        instead of queuing jobs that would fail at execution time.
        """
        if not self.config.maintenance_helper_path.is_file() or not os.access(
            self.config.maintenance_helper_path, os.X_OK
        ):
            return frozenset()
        try:
            completed = subprocess.run(
                ["sudo", "-n", str(self.config.maintenance_helper_path)],
                input=json.dumps({"verb": "version", "arguments": {}}),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                encoding="utf-8",
                check=False,
                timeout=10,
            )
            if completed.returncode:
                return frozenset()
            body = json.loads(completed.stdout)
            if not body.get("ok"):
                return frozenset()
            verbs = body.get("result", {}).get("verbs", [])
            return frozenset(str(verb) for verb in verbs)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return frozenset()

    async def _call_helper(self, verb: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if verb not in self._helper_verbs:
            raise RuntimeError(f"The maintenance helper does not support '{verb}'.")
        document = json.dumps(
            {"verb": verb, "arguments": arguments}, separators=(",", ":")
        ).encode("utf-8")
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            str(self.config.maintenance_helper_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(document), timeout=90)
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"Maintenance helper timed out during '{verb}'.") from error
        try:
            body = json.loads(output)
        except (ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Maintenance helper returned an invalid response for '{verb}'.") \
                from error
        if not body.get("ok"):
            raise RuntimeError(str(body.get("error") or f"Maintenance helper refused '{verb}'."))
        return dict(body.get("result") or {})

    @staticmethod
    def _validate_wifi_network(ssid: str, password: str, priority: object) -> None:
        try:
            ssid_size = len(ssid.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise RuntimeError("Wi-Fi SSID is invalid.") from error
        if (
            not 1 <= ssid_size <= 32
            or any(ord(character) < 32 or ord(character) == 127 for character in ssid)
            or isinstance(priority, bool)
            or priority != 0
        ):
            raise RuntimeError("Wi-Fi profile data is invalid.")
        raw_psk = re.fullmatch(r"[0-9A-Fa-f]{64}", password) is not None
        passphrase = 8 <= len(password) <= 63 and all(
            32 <= ord(character) <= 126 for character in password
        )
        if not raw_psk and not passphrase:
            raise RuntimeError("Wi-Fi profile data is invalid.")

    async def _install_release(self, session: ClientSession, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        release = job.get("release")
        if not isinstance(release, dict):
            raise RuntimeError("Release metadata is missing.")
        version = str(release["version"])
        expected_sha256 = str(release.get("sha256", ""))
        expected_size = int(release.get("size", 0))
        if not VERSION_PATTERN.fullmatch(version) or ".." in version:
            raise RuntimeError("Release version is unsafe.")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise RuntimeError("Release checksum is invalid.")
        if expected_size <= 0 or expected_size > MAX_RELEASE_SIZE:
            raise RuntimeError("Release size is invalid or exceeds the agent limit.")
        if self.config.current_link.exists() and not self.config.current_link.is_symlink():
            # _switch_current does an atomic symlink swap (os.replace onto
            # current_link), which the OS refuses when current_link is a real
            # directory instead of a symlink -- it would fail deep into the
            # install, after already stopping the live TAKT service, with a raw
            # IsADirectoryError and no previous release to fall back to. Catching
            # it here, before anything disruptive happens, avoids that outage and
            # gives an operator a message they can actually act on over SSH.
            raise RuntimeError(
                f"{self.config.current_link} exists but is not a symlink; installs require "
                "it to be a symlink to the active release. Manual repair over SSH is required."
            )
        health = await self._local_health(session)
        if health.get("state") != "ready":
            await self._progress_job(
                session, job_id, 1, "Waiting for a safe timer state", stage="waiting_for_safe_state"
            )
            raise DeferredJob(
                f"Waiting for TAKT to be ready (current state: {health.get('state', 'unknown')})."
            )
        if health.get("ok") and health.get("version") == version:
            await self._progress_job(
                session, job_id, 95, f"TAKT {version} is already active", stage="health_checking"
            )
            await self._report_recovery_failure(session)
            return
        await self._progress_job(
            session,
            job_id,
            5,
            "Downloading release",
            stage="downloading",
            bytes_downloaded=0,
            bytes_total=expected_size,
        )
        artifact = self.config.data_directory / f"{expected_sha256}.tar.gz.part"
        download_complete = False
        project_directory: Path | None = None
        maintenance_lease: str | None = None
        try:
            await self._download_release(
                session,
                job_id=job_id,
                artifact=artifact,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            download_complete = True
            await self._progress_job(session, job_id, 25, "Staging release", stage="staging")
            prepared_directory, dependencies_changed = await asyncio.to_thread(
                self._prepare_release, artifact, version, job_id
            )
            project_directory = prepared_directory
            self._assert_job_control()
            await self._progress_job(
                session,
                job_id,
                45,
                "Installing dependencies" if dependencies_changed else "Verifying dependencies",
                stage="installing_dependencies",
            )
            await asyncio.to_thread(
                self._install_release_dependencies, project_directory, dependencies_changed
            )
            self._assert_job_control()
            maintenance_lease = await self._require_safe_state(
                session, job, f"Install TAKT release {version}"
            )
            try:
                previous_target = (
                    self.config.current_link.resolve(strict=True)
                    if self.config.current_link.is_symlink()
                    else None
                )
            except OSError:
                previous_target = None
            previous_version = self._read_release_version()
            journal: dict[str, Any] = {
                "job_id": job_id,
                "lease_id": str((self._active_job or {}).get("lease_id") or ""),
                "version": version,
                "previous_target": str(previous_target) if previous_target else None,
                "previous_version": previous_version,
                "new_target": str(project_directory),
                "backup": None,
                "phase": "prepared",
                "updated_at": time.time(),
            }
            self._write_update_journal(journal)
            self._write_maintenance_marker(job_id, version)
            backup: Path | None = None
            self._assert_job_control()
            await self._progress_job(
                session, job_id, 70, "Stopping TAKT safely", stage="activating"
            )
            self._assert_job_control()
            await self._systemctl("stop", self.config.service_name)
            self._update_journal_phase(journal, "stopped")
            try:
                await self._progress_job(
                    session, job_id, 75, "Backing up run data", stage="activating"
                )
                backup = await asyncio.to_thread(self._backup_before_update, version)
                journal["backup"] = str(backup) if backup else None
                self._update_journal_phase(journal, "backed_up")
                self._assert_job_control()
                await self._progress_job(
                    session, job_id, 80, "Activating release", stage="activating"
                )
                self._update_journal_phase(journal, "activating")
                self._switch_current(project_directory)
                self._write_release_version(version)
                self._update_journal_phase(journal, "activated")
                self._assert_job_control()
                await self._progress_job(session, job_id, 85, "Restarting TAKT", stage="restarting")
                await self._systemctl("start", self.config.service_name)
                await self._progress_job(
                    session, job_id, 90, "Checking TAKT health", stage="health_checking"
                )
                await self._wait_for_health(session, version)
                self._update_journal_phase(journal, "healthy")
                self._remove_maintenance_marker()
            except Exception as error:
                self._update_journal_phase(journal, "rolling_back")
                with contextlib.suppress(Exception):
                    await self._systemctl("stop", self.config.service_name)
                if previous_target is None:
                    with contextlib.suppress(Exception):
                        await self._systemctl("start", self.config.service_name)
                    raise RuntimeError(
                        f"Release {version} failed and no previous release exists: {error}"
                    ) from error
                self._switch_current(previous_target)
                self._write_release_version(previous_version or "unknown")
                if backup is not None:
                    await asyncio.to_thread(self._restore_database_backup, backup)
                await self._progress_job(session, job_id, 85, "Restarting TAKT", stage="restarting")
                await self._systemctl("start", self.config.service_name)
                await self._wait_for_health(session, previous_version or "")
                self._update_journal_phase(journal, "rolled_back")
                self._remove_maintenance_marker()
                raise RolledBackJob(
                    f"Release {version} failed and the previous release was restored: {error}"
                ) from error
            await self._report_recovery_failure(session)
            await self._progress_job(
                session, job_id, 95, f"TAKT {version} is healthy", stage="health_checking"
            )
            try:
                await self._upload_mirror(session)
            except Exception as error:
                LOGGER.warning("post_deployment_mirror_pending error=%s", error)
        except CancelledJob:
            if maintenance_lease:
                with contextlib.suppress(Exception):
                    await self._release_maintenance(session, maintenance_lease)
            if project_directory is not None:
                shutil.rmtree(project_directory, ignore_errors=True)
            raise
        finally:
            if download_complete:
                artifact.unlink(missing_ok=True)

    def _prepare_release(self, artifact: Path, version: str, job_id: str) -> tuple[Path, bool]:
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
            with (project / "pyproject.toml").open("rb") as handle:
                new_metadata = tomllib.load(handle)
            package_version = str(new_metadata.get("project", {}).get("version", ""))
            if package_version != version:
                raise RuntimeError(
                    f"Release package version {package_version or 'missing'} does not match "
                    f"requested version {version}."
                )
            new_dependencies = self._normalized_dependencies(new_metadata)
            if destination.exists():
                shutil.rmtree(destination)
            if project == staging:
                staging.replace(destination)
            else:
                project.replace(destination)
                shutil.rmtree(staging)
            venv = destination / ".venv"
            active_venv = self.config.current_link / ".venv"
            reused_venv = active_venv.is_dir()
            if reused_venv:
                shutil.copytree(active_venv, venv, symlinks=True)
            else:
                subprocess.run(
                    ["python3", "-m", "venv", "--system-site-packages", str(venv)],
                    check=True,
                    timeout=180,
                )
            # A freshly created venv never has the runtime dependencies installed
            # (only whatever --system-site-packages exposes), so it always needs
            # a full resolve, regardless of whether the dependency set changed.
            dependencies_changed = (
                not reused_venv or new_dependencies != self._previous_dependencies()
            )
            return destination, dependencies_changed
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            self._discard_prepared_release(destination)
            raise

    def _previous_dependencies(self) -> tuple[str, ...] | None:
        pyproject = self.config.current_link / "pyproject.toml"
        if not pyproject.is_file():
            return None
        with pyproject.open("rb") as handle:
            return self._normalized_dependencies(tomllib.load(handle))

    @staticmethod
    def _normalized_dependencies(metadata: dict[str, Any]) -> tuple[str, ...]:
        dependencies = metadata.get("project", {}).get("dependencies", [])
        return tuple(sorted(str(dependency) for dependency in dependencies))

    def _discard_prepared_release(self, destination: Path) -> None:
        if destination.exists() and (
            not self.config.current_link.is_symlink()
            or self.config.current_link.resolve() != destination
        ):
            shutil.rmtree(destination, ignore_errors=True)

    def _install_release_dependencies(self, destination: Path, dependencies_changed: bool) -> None:
        """Install the release into its venv, resolving dependencies from the
        configured index only when the dependency set actually changed.

        The offline-safe ``--no-deps`` fast path stays the default so most
        installs never touch the network; a changed dependency set falls back
        to a real resolve, which is the only way to pick up an added, removed
        or bumped runtime dependency.
        """
        venv = destination / ".venv"
        arguments = [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-input",
            "--no-build-isolation",
        ]
        if not dependencies_changed:
            arguments.append("--no-deps")
        arguments.append(".")
        timeout = _DEPENDENCY_INSTALL_TIMEOUT if dependencies_changed else _FAST_INSTALL_TIMEOUT
        try:
            subprocess.run(
                arguments,
                cwd=destination,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as error:
            self._discard_prepared_release(destination)
            raise RuntimeError(self._redact_pip_failure(error)) from error
        except subprocess.TimeoutExpired as error:
            self._discard_prepared_release(destination)
            raise RuntimeError(
                f"Dependency installation timed out after {timeout} seconds."
            ) from error
        except Exception:
            self._discard_prepared_release(destination)
            raise

    @staticmethod
    def _redact_pip_failure(error: subprocess.CalledProcessError) -> str:
        output = "\n".join(part for part in (error.stderr, error.stdout) if part).strip()
        output = _PIP_CREDENTIAL_URL.sub("://***@", output)
        tail = "\n".join(output.splitlines()[-20:])[-2000:]
        if tail:
            return f"Dependency installation failed (exit code {error.returncode}): {tail}"
        return f"Dependency installation failed (exit code {error.returncode})."

    def _backup_before_update(self, version: str) -> Path | None:
        if not self.config.database_path.exists():
            return None
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
        return backup

    def _restore_database_backup(self, backup: Path) -> None:
        for suffix in ("-wal", "-shm"):
            Path(f"{self.config.database_path}{suffix}").unlink(missing_ok=True)
        source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        destination = sqlite3.connect(self.config.database_path)
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
        signature_before = self._database_signature()
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="takt-mirror-", suffix=".sqlite3", dir=self.config.data_directory
        )
        os.close(file_descriptor)
        snapshot = Path(temporary_name)
        try:
            await asyncio.to_thread(self._create_snapshot, snapshot)
            signature_after_snapshot = self._database_signature()
            digest = await asyncio.to_thread(self._sha256, snapshot)
            with snapshot.open("rb") as handle:
                async with session.post(
                    f"{self.config.registry_url}/agent/mirror",
                    data=handle,
                    headers={**self._headers(), "X-TAKT-SHA256": digest},
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Mirror upload failed: {await response.text()}")
            signature_after_upload = self._database_signature()
            if (
                signature_before is not None
                and signature_before == signature_after_snapshot == signature_after_upload
            ):
                self._last_mirror_signature = signature_before
                self.state.last_mirror_signature = signature_before
                self.state.save(self.state_path)
            else:
                LOGGER.info("database_changed_during_mirror scheduling_follow_up=true")
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
        consecutive = 0
        for _ in range(30):
            self._assert_job_control()
            await asyncio.sleep(1)
            last = await self._local_health(session)
            if last.get("ok") and (not version or last.get("version") == version):
                consecutive += 1
                if consecutive >= 3 and await self._service_is_active():
                    return
            else:
                consecutive = 0
        raise RuntimeError(f"health endpoint did not report version {version}: {last}")

    async def _download_release(
        self,
        session: ClientSession,
        *,
        job_id: str,
        artifact: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        if artifact.exists() and artifact.stat().st_size > expected_size:
            artifact.unlink()
        offset = artifact.stat().st_size if artifact.exists() else 0
        if offset == expected_size:
            if await asyncio.to_thread(self._sha256, artifact) == expected_sha256:
                await self._progress_job(
                    session,
                    job_id,
                    25,
                    "Verifying release checksum",
                    stage="verifying",
                    bytes_downloaded=expected_size,
                    bytes_total=expected_size,
                )
                self._assert_job_control()
                return
            artifact.unlink()
            offset = 0
        free = shutil.disk_usage(self.config.data_directory).free
        if free < expected_size - offset + 64 * 1024 * 1024:
            raise RuntimeError("Not enough free disk space to stage this release safely.")
        headers = self._headers()
        if self._active_job and self._active_job["id"] == job_id:
            headers["X-Job-Lease"] = str(self._active_job["lease_id"])
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            async with session.get(
                f"{self.config.registry_url}/agent/jobs/{job_id}/artifact",
                headers=headers,
            ) as response:
                if response.status not in ({206} if offset else {200}):
                    if offset and response.status == 200:
                        offset = 0
                    elif response.status in {408, 429} or response.status >= 500:
                        raise RetryableJob(
                            f"release download returned HTTP {response.status}: "
                            f"{await response.text()}"
                        )
                    else:
                        raise RuntimeError(
                            f"release download was rejected with HTTP {response.status}: "
                            f"{await response.text()}"
                        )
                mode = "ab" if offset and response.status == 206 else "wb"
                downloaded = offset if mode == "ab" else 0
                last_progress_at = _now()
                with artifact.open(mode) as handle:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        downloaded += len(chunk)
                        if downloaded > expected_size:
                            artifact.unlink(missing_ok=True)
                            raise RuntimeError("Registry sent more release data than declared.")
                        handle.write(chunk)
                        self._assert_job_control()
                        now = _now()
                        if now - last_progress_at >= DOWNLOAD_PROGRESS_INTERVAL_SECONDS:
                            await self._progress_job(
                                session,
                                job_id,
                                5 + int(20 * downloaded / expected_size),
                                f"Downloading release ({downloaded} of {expected_size} bytes)",
                                stage="downloading",
                                bytes_downloaded=downloaded,
                                bytes_total=expected_size,
                            )
                            last_progress_at = _now()
                            self._assert_job_control()
        except (ClientError, TimeoutError) as error:
            raise RetryableJob(f"release transfer interrupted: {error}") from error
        actual_size = artifact.stat().st_size if artifact.exists() else 0
        if actual_size != expected_size:
            raise RetryableJob(
                f"release transfer is incomplete ({actual_size} of {expected_size} bytes)"
            )
        await self._progress_job(
            session,
            job_id,
            25,
            "Verifying release checksum",
            stage="verifying",
            bytes_downloaded=actual_size,
            bytes_total=expected_size,
        )
        self._assert_job_control()
        digest = await asyncio.to_thread(self._sha256, artifact)
        if digest != expected_sha256:
            artifact.unlink(missing_ok=True)
            raise RuntimeError("Downloaded release checksum does not match.")

    async def _progress_job(
        self,
        session: ClientSession,
        job_id: str,
        progress: int,
        message: str,
        *,
        stage: str | None = None,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
    ) -> None:
        if self._active_job and self._active_job["id"] == job_id:
            self._active_job["progress"] = progress
            self._active_job["message"] = message
            if stage is not None:
                self._active_job["stage"] = stage
        try:
            await self._send_job_event(
                session,
                job_id,
                "running",
                progress,
                message,
                stage=stage,
                bytes_downloaded=bytes_downloaded,
                bytes_total=bytes_total,
            )
        except StaleJobResult:
            raise
        except Exception as error:
            LOGGER.warning("job_progress_pending id=%s error=%s", job_id, error)

    async def _queue_job(
        self, session: ClientSession, job_id: str, message: str, *, stage: str | None = None
    ) -> None:
        try:
            await self._send_job_event(session, job_id, "queued", 0, message, stage=stage)
        except StaleJobResult:
            raise
        except Exception as error:
            LOGGER.warning("job_requeue_pending id=%s error=%s", job_id, error)

    async def _remember_result(
        self,
        session: ClientSession,
        job_id: str,
        status: str,
        message: str,
        *,
        lease_id: str = "",
        stage: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        if not lease_id and self._active_job and self._active_job["id"] == job_id:
            lease_id = str(self._active_job["lease_id"])
        self.state.pending_results[job_id] = {
            "status": status,
            "progress": 100,
            "message": message[:2000],
            "lease_id": lease_id,
            "stage": stage,
            "result": result,
        }
        while len(self.state.pending_results) > 100:
            self.state.pending_results.pop(next(iter(self.state.pending_results)))
        self.state.save(self.state_path)
        try:
            await self._flush_pending_results(session, only=job_id)
        except Exception as error:
            LOGGER.warning("job_result_pending id=%s status=%s error=%s", job_id, status, error)

    async def _flush_pending_results(
        self, session: ClientSession, *, only: str | None = None
    ) -> None:
        """Report stored job outcomes to the registry, never letting one bad
        result wedge the others or the caller.

        A `StaleJobResult` (see `_send_job_event`) means the registry will
        never accept this exact report, so the entry is dropped immediately.
        Any other failure (network blip, transient 5xx) is retried on
        subsequent calls up to `MAX_PENDING_RESULT_ATTEMPTS` times before
        being abandoned, so a single unreachable registry can't grow
        `pending_results` -- and the state file it's persisted in -- forever.
        """
        job_ids = [only] if only else list(self.state.pending_results)
        changed = False
        for job_id in job_ids:
            result = self.state.pending_results.get(job_id)
            if result is None:
                continue
            try:
                await self._send_job_event(
                    session,
                    job_id,
                    str(result["status"]),
                    int(result["progress"]),
                    str(result["message"]),
                    lease_id=str(result.get("lease_id") or ""),
                    stage=str(result.get("stage")) if result.get("stage") else None,
                    result=result.get("result"),
                )
            except StaleJobResult as error:
                LOGGER.warning("stale_job_result_dropped id=%s error=%s", job_id, error)
            except Exception as error:
                attempts = int(result.get("attempts") or 0) + 1
                if attempts < MAX_PENDING_RESULT_ATTEMPTS:
                    result["attempts"] = attempts
                    changed = True
                    LOGGER.warning(
                        "job_result_flush_retry id=%s attempts=%s error=%s",
                        job_id,
                        attempts,
                        error,
                    )
                    continue
                LOGGER.warning(
                    "job_result_flush_abandoned id=%s attempts=%s error=%s",
                    job_id,
                    attempts,
                    error,
                )
            self.state.pending_results.pop(job_id, None)
            changed = True
        if changed:
            self.state.save(self.state_path)

    async def _renew_job_lease(self, session: ClientSession) -> None:
        while True:
            await asyncio.sleep(30)
            active = self._active_job
            if active is None:
                return
            try:
                await self._send_job_event(
                    session,
                    str(active["id"]),
                    "running",
                    int(active["progress"]),
                    str(active["message"]),
                    stage=str(active.get("stage")) if active.get("stage") else None,
                )
            except StaleJobResult as error:
                active["control_lost"] = True
                LOGGER.error("job_control_lost id=%s error=%s", active["id"], error)
                return
            except Exception as error:
                LOGGER.warning("job_lease_renewal_failed id=%s error=%s", active["id"], error)

    async def _send_job_event(
        self,
        session: ClientSession,
        job_id: str,
        status: str,
        progress: int,
        message: str,
        *,
        lease_id: str = "",
        stage: str | None = None,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        if not lease_id and self._active_job and self._active_job["id"] == job_id:
            lease_id = str(self._active_job["lease_id"])
        async with session.post(
            f"{self.config.registry_url}/agent/jobs/{job_id}",
            json={
                "status": status,
                "progress": progress,
                "message": message,
                "lease_id": lease_id,
                "stage": stage,
                "bytes_downloaded": bytes_downloaded,
                "bytes_total": bytes_total,
                "result": result,
            },
            headers=self._headers(),
            timeout=ClientTimeout(total=25, connect=10, sock_read=15),
        ) as response:
            if response.status in {400, 401, 403, 404, 409}:
                # These are deterministic rejections (invalid transition/stage,
                # unknown lease, unknown job) -- retrying the identical request
                # cannot succeed, so treat them the same as a stale result
                # rather than raising and blocking the agent's heartbeat loop.
                raise StaleJobResult(await response.text())
            if response.status != 200:
                raise RuntimeError(f"Could not report job progress: {await response.text()}")
            body = await response.json()
            returned_job = body.get("job", {})
            if self._active_job and self._active_job["id"] == job_id:
                self._active_job["cancel_requested"] = bool(returned_job.get("cancel_requested"))

    async def _recover_interrupted_update(self, session: ClientSession) -> None:
        journal = self._load_update_journal()
        if journal is None:
            return
        job_id = str(journal.get("job_id") or "")
        lease_id = str(journal.get("lease_id") or "")
        phase = str(journal.get("phase") or "")
        if not JOB_ID_PATTERN.fullmatch(job_id) or not lease_id:
            raise RuntimeError("The update recovery journal is invalid.")
        LOGGER.warning("recovering_interrupted_update id=%s phase=%s", job_id, phase)
        if phase in {"healthy", "rolled_back"}:
            self._remove_maintenance_marker()
            status = "succeeded" if phase == "healthy" else "rolled_back"
            message = f"install_release recovered after agent restart ({phase})"
            await self._remember_result(session, job_id, status, message, lease_id=lease_id)
            self._clear_update_journal(job_id)
            return
        previous_target_text = journal.get("previous_target")
        previous_target = Path(str(previous_target_text)) if previous_target_text else None
        previous_version = str(journal.get("previous_version") or "")
        if phase == "prepared" and previous_target is not None:
            health = await self._local_health(session)
            try:
                current_target = self.config.current_link.resolve(strict=True)
            except OSError:
                current_target = None
            if health.get("ok") and current_target == previous_target:
                self._remove_maintenance_marker()
                self._clear_update_journal(job_id)
                await self._send_job_event(
                    session,
                    job_id,
                    "queued",
                    0,
                    "Agent restarted before activation; update safely requeued",
                    lease_id=lease_id,
                )
                return
        if previous_target is None or not previous_target.exists():
            # Nothing left to retry: no previous release was captured (or it has
            # since been removed), so automatic rollback is impossible -- the
            # agent already did its best by restarting whatever was running when
            # this first happened (see the "no previous release exists" branch
            # in _install_release). Raising here forever would silently wedge
            # every future heartbeat and job claim behind a dead end, since this
            # check runs at the top of every run() iteration. Report the job
            # failed once and clear the journal so normal operation resumes.
            LOGGER.error(
                "update_recovery_abandoned id=%s reason=no_previous_release", job_id
            )
            await self._remember_result(
                session,
                job_id,
                "failed",
                "An interrupted update had no previous release to restore; "
                "manually verify the running version.",
                lease_id=lease_id,
                stage="intervention_required",
            )
            self._clear_update_journal(job_id)
            self._remove_maintenance_marker()
            return
        marker_held = self._maintenance_marker_matches(job_id)
        if await self._service_is_active() and not marker_held:
            await self._acquire_maintenance(session, job_id, "Recover interrupted TAKT update")
        self._write_maintenance_marker(job_id, str(journal.get("version") or "unknown"))
        self._update_journal_phase(journal, "recovering")
        with contextlib.suppress(Exception):
            await self._systemctl("stop", self.config.service_name)
        self._switch_current(previous_target)
        self._write_release_version(previous_version or "unknown")
        backup_text = journal.get("backup")
        if backup_text and Path(str(backup_text)).is_file():
            await asyncio.to_thread(self._restore_database_backup, Path(str(backup_text)))
        await self._systemctl("start", self.config.service_name)
        await self._wait_for_health(session, previous_version)
        self._update_journal_phase(journal, "rolled_back")
        self._remove_maintenance_marker()
        await self._remember_result(
            session,
            job_id,
            "rolled_back",
            "Interrupted update was rolled back automatically after agent restart",
            lease_id=lease_id,
        )
        self._clear_update_journal(job_id)

    async def _acquire_maintenance(self, session: ClientSession, job_id: str, reason: str) -> str:
        local_base = self.config.health_url.rsplit("/health", 1)[0]
        async with session.post(
            f"{local_base}/internal/maintenance/acquire",
            json={
                "request_id": job_id,
                "owner": "takt-agent",
                "reason": reason,
                "ttl_seconds": 120,
            },
            timeout=ClientTimeout(total=5),
        ) as response:
            body = await response.json()
            if response.status == 409:
                maintenance = body.get("maintenance", {})
                raise DeferredJob(
                    f"Waiting for TAKT to be ready (current state: "
                    f"{maintenance.get('timer_state', 'unknown')})."
                )
            if response.status != 200 or not body.get("acquired"):
                raise RuntimeError(f"Could not acquire local maintenance lock: {body}")
            return str(body["lease_token"])

    async def _release_maintenance(self, session: ClientSession, lease_token: str) -> None:
        local_base = self.config.health_url.rsplit("/health", 1)[0]
        async with session.post(
            f"{local_base}/internal/maintenance/release",
            json={"lease_token": lease_token},
            timeout=ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Could not release local maintenance lock: {await response.text()}"
                )

    async def _systemctl(self, *arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            "/usr/bin/systemctl",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError as error:
            process.kill()
            output, _ = await process.communicate()
            raise RuntimeError(
                f"systemctl {' '.join(arguments)} timed out: "
                f"{output.decode('utf-8', errors='replace').strip()}"
            ) from error
        if process.returncode:
            raise RuntimeError(output.decode("utf-8", errors="replace").strip())

    async def _service_is_active(self) -> bool:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl",
            "is-active",
            "--quiet",
            self.config.service_name,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return False
        return process.returncode == 0

    def _load_update_journal(self) -> dict[str, Any] | None:
        if not self.update_journal_path.exists():
            return None
        try:
            payload = json.loads(self.update_journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("The update recovery journal cannot be read.") from error
        if not isinstance(payload, dict):
            raise RuntimeError("The update recovery journal is invalid.")
        return payload

    def _write_update_journal(self, journal: dict[str, Any]) -> None:
        atomic_write_text(self.update_journal_path, json.dumps(journal, indent=2) + "\n")

    def _update_journal_phase(self, journal: dict[str, Any], phase: str) -> None:
        journal["phase"] = phase
        journal["updated_at"] = time.time()
        self._write_update_journal(journal)

    def _clear_update_journal(self, job_id: str) -> None:
        journal = self._load_update_journal()
        if journal is not None and str(journal.get("job_id")) == job_id:
            durable_unlink(self.update_journal_path)

    def _write_maintenance_marker(self, job_id: str, version: str) -> None:
        marker = self.config.maintenance_marker
        atomic_write_text(
            marker,
            json.dumps(
                {
                    "job_id": job_id,
                    "version": version,
                    "created_at": time.time(),
                    "expires_at": time.time() + 30 * 60,
                },
                indent=2,
            )
            + "\n",
        )

    def _remove_maintenance_marker(self) -> None:
        durable_unlink(self.config.maintenance_marker)

    def _maintenance_marker_matches(self, job_id: str) -> bool:
        try:
            payload = json.loads(self.config.maintenance_marker.read_text(encoding="utf-8"))
            return (
                isinstance(payload, dict)
                and str(payload.get("job_id")) == job_id
                and float(payload.get("expires_at", 0)) > time.time()
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def _assert_job_control(self) -> None:
        if self._active_job and self._active_job.get("control_lost"):
            raise StaleJobResult("Registry revoked or reassigned this job lease.")
        if self._active_job and self._active_job.get("cancel_requested"):
            raise CancelledJob("Installation was cancelled before activation.")

    def _switch_current(self, target: Path) -> None:
        self.config.current_link.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.current_link.with_name(f".current-{uuid.uuid4().hex}")
        temporary.symlink_to(target)
        temporary.replace(self.config.current_link)
        _fsync_directory(self.config.current_link.parent)

    def _write_release_version(self, version: str) -> None:
        atomic_write_text(
            self.config.release_environment,
            f"TAKT_RELEASE_VERSION={version}\n",
        )

    def _read_release_version(self) -> str | None:
        try:
            line = self.config.release_environment.read_text(encoding="utf-8").strip()
            return line.partition("=")[2] or None
        except OSError:
            return None

    def _clear_enrollment_code(self) -> None:
        self.config.enrollment_code = ""
        path = self.config.config_path
        if path is None or not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        updated = [
            'enrollment_code = ""' if re.match(r"^\s*enrollment_code\s*=", line) else line
            for line in lines
        ]
        atomic_write_text(path, "\n".join(updated).rstrip() + "\n")

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

    @staticmethod
    def _boot_id() -> str | None:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            return None

    @staticmethod
    def _wifi_signal_dbm() -> float | None:
        try:
            lines = Path("/proc/net/wireless").read_text(encoding="utf-8").splitlines()[2:]
            for line in lines:
                fields = line.replace(":", " ").split()
                if len(fields) >= 4:
                    return float(fields[3].rstrip("."))
        except (OSError, ValueError):
            pass
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAKT Raspberry Pi management agent")
    parser.add_argument(
        "--config", default="~/.config/takt/agent.toml", help="agent TOML configuration"
    )
    parser.add_argument("--once", action="store_true", help="run one heartbeat cycle")
    parser.add_argument(
        "--enroll-only",
        action="store_true",
        help="enroll the device and exit before continuing provisioning",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging("takt-agent")
    try:
        config = AgentConfig.load(expanded(args.config))
        config.data_directory.mkdir(parents=True, exist_ok=True)
        lock_handle = _acquire_agent_lock(config.data_directory)
        try:
            asyncio.run(TaktAgent(config).run(once=args.once, enroll_only=args.enroll_only))
        finally:
            lock_handle.close()
    except KeyboardInterrupt:
        return 0
    except Exception:
        LOGGER.exception("agent_stopped")
        return 1
    return 0


def _acquire_agent_lock(data_directory: Path):
    handle = (data_directory / "agent.lock").open("a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:  # pragma: no cover - Raspberry Pi OS is Unix.
        return handle
    except BlockingIOError:
        handle.close()
        raise RuntimeError("Another TAKT agent process is already running.") from None
    return handle


if __name__ == "__main__":
    raise SystemExit(main())
