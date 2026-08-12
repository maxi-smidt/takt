#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

NMCLI = Path("/usr/bin/nmcli")
CONNECTION_DIRECTORY = Path("/etc/NetworkManager/system-connections")
MANAGED_MARKER = "# Managed by TAKT Fleet\n"
MAX_REQUEST_BYTES = 4096


class WifiHelperError(RuntimeError):
    pass


def load_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise WifiHelperError("Request is too large.")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WifiHelperError("Request is invalid.") from error
    if not isinstance(request, dict) or set(request) != {"ssid", "password", "priority"}:
        raise WifiHelperError("Request is invalid.")
    validate_request(request)
    return request


def validate_request(request: dict[str, object]) -> None:
    ssid = request["ssid"]
    password = request["password"]
    priority = request["priority"]
    if not isinstance(ssid, str) or not isinstance(password, str):
        raise WifiHelperError("Request is invalid.")
    try:
        ssid_size = len(ssid.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise WifiHelperError("Request is invalid.") from error
    if (
        not 1 <= ssid_size <= 32
        or any(ord(character) < 32 or ord(character) == 127 for character in ssid)
        or isinstance(priority, bool)
        or priority != 0
    ):
        raise WifiHelperError("Request is invalid.")
    raw_psk = re.fullmatch(r"[0-9A-Fa-f]{64}", password) is not None
    passphrase = 8 <= len(password) <= 63 and all(
        32 <= ord(character) <= 126 for character in password
    )
    if not raw_psk and not passphrase:
        raise WifiHelperError("Request is invalid.")


def apply_wifi_profile(
    request: dict[str, object],
    *,
    connection_directory: Path = CONNECTION_DIRECTORY,
    nmcli: Path = NMCLI,
) -> Path:
    validate_request(request)
    ssid = request["ssid"]
    password = request["password"]
    assert isinstance(ssid, str)
    assert isinstance(password, str)
    digest = hashlib.sha256(ssid.encode("utf-8")).hexdigest()
    profile_id = f"takt-{digest[:16]}"
    profile_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"takt:wifi:{digest}"))
    target = connection_directory / f"takt-{digest[:32]}.nmconnection"
    content = render_profile(profile_id, profile_uuid, ssid, password).encode("utf-8")

    connection_directory.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise WifiHelperError("Managed profile path is unsafe.")
    previous = target.read_bytes() if target.exists() else None
    if previous is not None:
        text = previous.decode("utf-8", errors="replace")
        if f"uuid={profile_uuid}" not in text.splitlines():
            raise WifiHelperError("Managed profile path is occupied.")

    _atomic_write(target, content)
    try:
        subprocess.run(
            [str(nmcli), "connection", "load", str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        if previous is None:
            target.unlink(missing_ok=True)
            _fsync_directory(connection_directory)
            try:
                subprocess.run(
                    [str(nmcli), "connection", "reload"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            _atomic_write(target, previous)
            try:
                subprocess.run(
                    [str(nmcli), "connection", "load", str(target)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        raise WifiHelperError("NetworkManager rejected the profile.") from error
    return target


def render_profile(profile_id: str, profile_uuid: str, ssid: str, password: str) -> str:
    return "".join(
        (
            MANAGED_MARKER,
            "[connection]\n",
            f"id={profile_id}\n",
            f"uuid={profile_uuid}\n",
            "type=wifi\n",
            "autoconnect=true\n",
            "autoconnect-priority=0\n",
            "\n[wifi]\n",
            "mode=infrastructure\n",
            f"ssid={_keyfile_value(ssid)}\n",
            "\n[wifi-security]\n",
            "key-mgmt=wpa-psk\n",
            f"psk={_keyfile_value(password)}\n",
            "\n[ipv4]\n",
            "method=auto\n",
            "\n[ipv6]\n",
            "method=auto\n",
        )
    )


def _keyfile_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    leading_spaces = len(escaped) - len(escaped.lstrip(" "))
    return "\\s" * leading_spaces + escaped[leading_spaces:]


def _atomic_write(target: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
        _fsync_directory(target.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 1:
        print("TAKT Wi-Fi helper must run as root.", file=sys.stderr)
        return 1
    try:
        request = load_request(sys.stdin.buffer)
        apply_wifi_profile(request)
    except (OSError, WifiHelperError):
        print("Wi-Fi profile could not be saved.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
