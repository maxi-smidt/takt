from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import shlex
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import asyncssh

from takt.registry.storage import RegistryStore

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
TRANSFER_TIMEOUT = 15 * 60
HOST_KEY_TIMEOUT = 20
COMMAND_TIMEOUT = 60
INSTALL_TIMEOUT = 45 * 60
AGENT_TIMEOUT = 5 * 60
SECRET_PATTERN = re.compile(r"(?:TAKT-[A-Za-z0-9_-]{8,})")


@dataclass
class DeploymentCredentials:
    ssh_password: str = ""
    ssh_private_key: str = ""
    ssh_key_passphrase: str = ""
    sudo_password: str = ""

    def clear(self) -> None:
        self.ssh_password = ""
        self.ssh_private_key = ""
        self.ssh_key_passphrase = ""
        self.sudo_password = ""


def validate_registry_url(value: str, allow_insecure_http: bool) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Registry URL must be a plain HTTP(S) URL without credentials.")
    normalized = value.strip().rstrip("/")
    if parsed.scheme == "http" and not allow_insecure_http:
        hostname = parsed.hostname.lower().rstrip(".")
        loopback = hostname in {"localhost", "::1"} or hostname.startswith("127.")
        if not loopback:
            raise ValueError("HTTP requires explicit acknowledgement for a non-loopback registry.")
    return normalized


def redact_message(message: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    result = SECRET_PATTERN.sub("[redacted enrollment code]", str(message))
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[redacted secret]")
    return result[:4000]


class DeploymentManager:
    def __init__(self, store: RegistryStore) -> None:
        self.store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._credentials: dict[str, DeploymentCredentials] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for credentials in self._credentials.values():
            credentials.clear()
        self._credentials.clear()

    def start_discovery(self, deployment_id: str) -> None:
        self._start(deployment_id, self._discover(deployment_id))

    async def confirm_host_key(
        self, deployment_id: str, fingerprint: str, *, replace: bool = False
    ) -> dict[str, Any]:
        deployment = self._deployment(deployment_id)
        if deployment["status"] != "awaiting_host_key":
            raise ValueError("This deployment is not waiting for host-key confirmation.")
        if fingerprint != deployment.get("host_key_fingerprint"):
            raise ValueError("The confirmed host key does not match the presented key.")
        target_key = self.store.deployment_target_key(deployment["target"], deployment["port"])
        self.store.trust_ssh_host(
            target_key,
            deployment["host_key"],
            deployment["host_key_fingerprint"],
            replace=replace,
        )
        return self._event(
            deployment_id,
            "credentials",
            "Host key trusted. Enter SSH credentials to continue.",
            status="awaiting_credentials",
        )

    def submit_credentials(
        self, deployment_id: str, credentials: DeploymentCredentials
    ) -> None:
        deployment = self._deployment(deployment_id)
        if deployment["status"] != "awaiting_credentials":
            raise ValueError("This deployment is not waiting for credentials.")
        if not credentials.ssh_password and not credentials.ssh_private_key:
            raise ValueError("An SSH password or private key is required.")
        self._credentials[deployment_id] = credentials
        self._start(deployment_id, self._run(deployment_id))

    def retry(self, deployment_id: str) -> dict[str, Any]:
        deployment = self._deployment(deployment_id)
        if deployment["status"] not in TERMINAL_STATUSES:
            raise ValueError("Only a finished deployment can be retried.")
        credentials = self._credentials.pop(deployment_id, None)
        if credentials:
            credentials.clear()
        self.store.update_deployment(
            deployment_id,
            status="pending",
            stage="starting",
            message="Retry queued",
            host_key=None,
            host_key_fingerprint=None,
            device_id=None,
            completed_at=None,
        )
        self._event(deployment_id, "starting", "Retry queued", status="pending")
        self.start_discovery(deployment_id)
        return self._deployment(deployment_id)

    def cancel(self, deployment_id: str) -> dict[str, Any]:
        deployment = self._deployment(deployment_id)
        task = self._tasks.get(deployment_id)
        if deployment["status"] in TERMINAL_STATUSES:
            return deployment
        if task and not task.done():
            task.cancel()
        credentials = self._credentials.pop(deployment_id, None)
        if credentials:
            credentials.clear()
        return self._event(deployment_id, "cancelled", "Deployment cancelled", status="cancelled")

    def _start(self, deployment_id: str, coroutine: Any) -> None:
        old = self._tasks.get(deployment_id)
        if old and not old.done():
            return
        self._tasks[deployment_id] = asyncio.create_task(
            coroutine, name=f"deployment-{deployment_id}"
        )

    def _deployment(self, deployment_id: str) -> dict[str, Any]:
        deployment = self.store.get_deployment(deployment_id)
        if deployment is None:
            raise LookupError("Deployment does not exist.")
        return deployment

    def _event(
        self,
        deployment_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        status: str | None = None,
    ) -> dict[str, Any]:
        credentials = self._credentials.get(deployment_id)
        secrets = []
        if credentials:
            secrets.extend(
                [
                    credentials.ssh_password,
                    credentials.ssh_private_key,
                    credentials.ssh_key_passphrase,
                    credentials.sudo_password,
                ]
            )
        return self.store.record_deployment_event(
            deployment_id,
            stage,
            redact_message(message, secrets),
            level=level,
            status=status,
        )

    async def _discover(self, deployment_id: str) -> None:
        deployment = self._deployment(deployment_id)
        stage = "host-key"
        self._event(deployment_id, stage, "Discovering SSH host key.", status="running")
        try:
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(deployment["target"], deployment["port"]),
                timeout=HOST_KEY_TIMEOUT,
            )
            public_key = key.export_public_key("openssh").decode("utf-8").strip()
            fingerprint = key.get_fingerprint("sha256")
            self.store.update_deployment(
                deployment_id,
                host_key=public_key,
                host_key_fingerprint=fingerprint,
            )
            target_key = self.store.deployment_target_key(
                deployment["target"], deployment["port"]
            )
            trusted = self.store.get_trusted_ssh_host(target_key)
            if trusted and trusted["host_key"] != public_key:
                self._event(
                    deployment_id,
                    stage,
                    "SSH host key changed. Verify the device before replacing the trusted key.",
                    level="error",
                    status="awaiting_host_key",
                )
            elif trusted:
                self._event(
                    deployment_id,
                    "credentials",
                    "Known SSH host key verified. Enter credentials.",
                    status="awaiting_credentials",
                )
            else:
                self._event(
                    deployment_id,
                    stage,
                    f"SSH host key presented ({fingerprint}). Confirm it to continue.",
                    status="awaiting_host_key",
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._event(
                deployment_id,
                stage,
                f"Could not reach the SSH service: {error}",
                level="error",
                status="failed",
            )

    async def _run(self, deployment_id: str) -> None:
        credentials = self._credentials.get(deployment_id)
        if credentials is None:
            return
        connection: asyncssh.SSHClientConnection | None = None
        deployment = self._deployment(deployment_id)
        lock = self._locks.setdefault(
            self.store.deployment_target_key(deployment["target"], deployment["port"]),
            asyncio.Lock(),
        )
        try:
            async with lock:
                deployment = self._deployment(deployment_id)
                self._event(
                    deployment_id,
                    "connecting",
                    "Opening one bounded SSH session.",
                    status="running",
                )
                connection = await self._connect(deployment, credentials)
                await self._preflight(connection, deployment_id, deployment)
                code = self.store.create_enrollment_code(
                    deployment["device_name"], deployment_id=deployment_id
                )
                self._event(
                    deployment_id,
                    "transfer",
                    "Uploading the server-side Raspberry Pi package.",
                )
                await self._transfer(connection, deployment_id, deployment, code)
                await self._install(connection, deployment_id, deployment, credentials)
                await self._wait_for_agent(deployment_id, deployment["release_version"])
                self._event(
                    deployment_id,
                    "complete",
                    "Device is online with the requested release.",
                    status="succeeded",
                )
        except asyncio.CancelledError:
            current = self.store.get_deployment(deployment_id)
            if current and current["status"] not in TERMINAL_STATUSES:
                self._event(deployment_id, "cancelled", "Deployment cancelled.", status="cancelled")
            raise
        except Exception as error:
            current = self.store.get_deployment(deployment_id)
            self._event(
                deployment_id,
                current.get("stage", "failed") if current else "failed",
                f"Deployment failed: {error}",
                level="error",
                status="failed",
            )
        finally:
            if connection is not None:
                connection.close()
                with contextlib.suppress(Exception):
                    await connection.wait_closed()
            saved = self._credentials.pop(deployment_id, None)
            if saved:
                saved.clear()

    async def _connect(
        self, deployment: dict[str, Any], credentials: DeploymentCredentials
    ) -> asyncssh.SSHClientConnection:
        public_key = deployment.get("host_key")
        if not public_key:
            raise ValueError("SSH host key was not confirmed.")
        host = deployment["target"]
        host_line = (
            f"[{host}]:{deployment['port']} {public_key}"
            if deployment["port"] != 22
            else f"{host} {public_key}"
        )
        known_hosts = asyncssh.import_known_hosts(host_line.encode("utf-8"))
        options: dict[str, Any] = {
            "host": host,
            "port": deployment["port"],
            "username": deployment["ssh_user"],
            "known_hosts": known_hosts,
            "agent_path": None,
            "connect_timeout": HOST_KEY_TIMEOUT,
        }
        if credentials.ssh_private_key:
            key = asyncssh.import_private_key(
                credentials.ssh_private_key,
                passphrase=credentials.ssh_key_passphrase or None,
            )
            options["client_keys"] = [key]
        else:
            options["password"] = credentials.ssh_password
        return await asyncssh.connect(**options)


    async def _preflight(
        self,
        connection: asyncssh.SSHClientConnection,
        deployment_id: str,
        deployment: dict[str, Any],
    ) -> None:
        release = self.store.get_release(deployment["release_id"])
        if release is None:
            raise ValueError("Release no longer exists.")
        self._event(
            deployment_id,
            "preflight",
            "Checking architecture, OS, disk space, and registry reachability.",
        )
        registry = shlex.quote(deployment["registry_url"])
        required_bytes = release["size"] + 200 * 1024 * 1024
        command = (
            "set -eu; "
            "test \"$(uname -m)\" = aarch64; "
            "test -r /etc/os-release; "
            ". /etc/os-release; "
            "case \"$ID$ID_LIKE\" in *debian*|*raspbian*) ;; "
            "*) echo 'Raspberry Pi OS (Debian) is required' >&2; exit 1;; esac; "
            "free_bytes=$(df -Pk \"$HOME\" | awk 'NR==2 {print $4 * 1024}'); "
            f"test \"$free_bytes\" -ge {required_bytes}; "
            "python3 -c 'import sys,urllib.request; "
            "print(urllib.request.urlopen(sys.argv[1], timeout=10).status)' "
            + registry
        )
        stdout, stderr, exit_status = await self._command(
            connection, deployment_id, "preflight", command, timeout=COMMAND_TIMEOUT
        )
        if exit_status != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or "Preflight checks failed.")
        self._event(deployment_id, "preflight", "Preflight checks passed.")

    async def _transfer(
        self,
        connection: asyncssh.SSHClientConnection,
        deployment_id: str,
        deployment: dict[str, Any],
        code: str,
    ) -> None:
        remote_dir = f".local/share/takt/bootstrap/{deployment_id}"
        archive = f"{remote_dir}/release.tar.gz"
        config = f"{remote_dir}/bootstrap.json"
        work = f"{remote_dir}/work"
        release = self.store.get_release(deployment["release_id"])
        if release is None:
            raise ValueError("Release no longer exists.")
        quoted_dir = shlex.quote(remote_dir)
        stdout, stderr, exit_status = await self._command(
            connection,
            deployment_id,
            "transfer",
            f"rm -rf -- {quoted_dir} && mkdir -p -- {quoted_dir}",
        )
        if exit_status:
            raise RuntimeError(stderr.strip() or stdout.strip() or "Could not prepare transfer.")
        bootstrap = json.dumps(
            {
                "registry_url": deployment["registry_url"],
                "allow_insecure_http": bool(deployment["allow_insecure_http"]),
                "enrollment_code": code,
                "device_name": deployment["device_name"],
                "hostname": deployment["requested_hostname"],
            }
        ).encode("utf-8")
        try:
            async with connection.start_sftp_client() as sftp:
                await asyncio.wait_for(
                    sftp.put(str(self.store.release_path(release["id"])), archive),
                    timeout=TRANSFER_TIMEOUT,
                )
                await asyncio.wait_for(
                    sftp.putfo(io.BytesIO(bootstrap), config),
                    timeout=COMMAND_TIMEOUT,
                )
        except TimeoutError as error:
            raise TimeoutError("Release upload timed out.") from error
        digest = shlex.quote(release["sha256"])
        stdout, stderr, exit_status = await self._command(
            connection,
            deployment_id,
            "transfer",
            f"echo {digest}  {shlex.quote(archive)} | sha256sum -c - && "
            f"mkdir -p -- {shlex.quote(work)} && "
            f"tar -xzf {shlex.quote(archive)} --strip-components=1 -C {shlex.quote(work)}",
        )
        if exit_status:
            raise RuntimeError(stderr.strip() or stdout.strip() or "Package verification failed.")
        self._event(deployment_id, "transfer", "Package checksum verified and unpacked.")

    async def _install(
        self,
        connection: asyncssh.SSHClientConnection,
        deployment_id: str,
        deployment: dict[str, Any],
        credentials: DeploymentCredentials,
    ) -> None:
        remote_dir = f".local/share/takt/bootstrap/{deployment_id}"
        installer = (
            f"bash {shlex.quote(remote_dir + '/work/scripts/install_raspberry_pi.sh')} "
            f"--bootstrap-config {shlex.quote(remote_dir + '/bootstrap.json')} --non-interactive"
        )
        sudo_password = credentials.sudo_password or credentials.ssh_password
        if sudo_password:
            command = (
                "set -eu; IFS= read -r _takt_sudo_password; "
                "printf '%s\\n' \"$_takt_sudo_password\" | sudo -S -p '' -v; "
                "unset _takt_sudo_password; "
                "(while sleep 60; do sudo -n -v; done) & _takt_sudo_refresh=$!; "
                "trap 'kill $_takt_sudo_refresh 2>/dev/null || true' EXIT; "
                + installer
            )
        else:
            command = "set -eu; sudo -n -v; " + installer
        self._event(
            deployment_id,
            "authorizing",
            "Validating sudo credentials for the installer session.",
        )
        stdout, stderr, exit_status = await self._command(
            connection,
            deployment_id,
            "install",
            command,
            input_text=sudo_password or None,
            timeout=INSTALL_TIMEOUT,
        )
        if exit_status:
            raise RuntimeError(stderr.strip() or stdout.strip() or "Installer failed.")
        stdout, stderr, exit_status = await self._command(
            connection,
            deployment_id,
            "cleanup",
            f"rm -f -- {shlex.quote(remote_dir + '/release.tar.gz')} "
            f"{shlex.quote(remote_dir + '/bootstrap.json')}",
        )
        if exit_status:
            raise RuntimeError(
                stderr.strip() or stdout.strip() or "Could not remove bootstrap files."
            )
        self._event(deployment_id, "install", "Installer completed; waiting for the agent.")

    async def _wait_for_agent(self, deployment_id: str, release_version: str) -> None:
        self._event(deployment_id, "agent", "Waiting for the enrolled agent heartbeat.")
        deadline = asyncio.get_running_loop().time() + AGENT_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            deployment = self._deployment(deployment_id)
            device_id = deployment.get("device_id")
            device = self.store.get_device(device_id) if device_id else None
            status = (device or {}).get("status", {})
            health = status.get("health") or {}
            if (
                device
                and device.get("online")
                and status.get("app_version") == release_version
                and status.get("protocol_version") == 1
                and health.get("ok") is True
                and health.get("state") == "ready"
            ):
                return
            await asyncio.sleep(2)
        raise TimeoutError("The agent did not report the expected online version and health.")

    async def _command(
        self,
        connection: asyncssh.SSHClientConnection,
        deployment_id: str,
        stage: str,
        command: str,
        *,
        input_text: str | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> tuple[str, str, int]:
        process = await connection.create_process(command, encoding="utf-8")
        if input_text is not None:
            process.stdin.write(input_text + "\n")
            process.stdin.write_eof()
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(process.stdout.read(), process.stderr.read()), timeout=timeout
            )
        except TimeoutError as error:
            process.kill()
            raise TimeoutError(f"Remote command timed out after {timeout:g} seconds.") from error
        try:
            result = await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError as error:
            process.kill()
            raise TimeoutError("Remote command did not exit after output closed.") from error
        stdout = stdout or ""
        stderr = stderr or ""
        for line in stdout.splitlines()[-100:]:
            self._event(deployment_id, stage, line)
        for line in stderr.splitlines()[-100:]:
            self._event(deployment_id, stage, line, level="error")
        return stdout, stderr, int(result.exit_status)
