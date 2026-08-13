#!/usr/bin/env python3
"""Privileged Fleet maintenance operations for a TAKT Raspberry Pi.

Runs as root via a single argument-less sudoers grant, reads one JSON request on
stdin and writes one JSON response on stdout. It intentionally cannot replace
itself or any unit file: the boundary between the unprivileged agent user and
root only holds while the verb set is fixed at install time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, BinaryIO

HELPER_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024
MAX_OUTPUT_BYTES = 512 * 1024

SYSTEMCTL = "/usr/bin/systemctl"
JOURNALCTL = "/usr/bin/journalctl"

MANAGED_UNITS = frozenset({"takt.service", "takt-agent.service"})
SERVICE_OPERATIONS = frozenset({"start", "stop", "restart"})
POWER_MODES = {"reboot": "reboot", "poweroff": "poweroff"}
VERBS = ("version", "service", "power", "journal")


class MaintenanceHelperError(RuntimeError):
    pass


def load_request(stream: BinaryIO) -> dict[str, Any]:
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise MaintenanceHelperError("Request is too large.")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaintenanceHelperError("Request is invalid.") from error
    if not isinstance(request, dict) or set(request) != {"verb", "arguments"}:
        raise MaintenanceHelperError("Request is invalid.")
    verb = request["verb"]
    arguments = request["arguments"]
    if verb not in VERBS or not isinstance(arguments, dict):
        raise MaintenanceHelperError("Request is invalid.")
    return request


def _managed_unit(arguments: dict[str, Any]) -> str:
    unit = arguments.get("unit")
    if not isinstance(unit, str) or unit not in MANAGED_UNITS:
        raise MaintenanceHelperError("Request is invalid.")
    return unit


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MaintenanceHelperError("Command could not be executed.") from error


def handle_version(arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments:
        raise MaintenanceHelperError("Request is invalid.")
    return {
        "helper_version": HELPER_VERSION,
        "verbs": list(VERBS),
        "units": sorted(MANAGED_UNITS),
    }


def handle_service(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"unit", "operation"}:
        raise MaintenanceHelperError("Request is invalid.")
    unit = _managed_unit(arguments)
    operation = arguments["operation"]
    if not isinstance(operation, str) or operation not in SERVICE_OPERATIONS:
        raise MaintenanceHelperError("Request is invalid.")
    completed = _run([SYSTEMCTL, operation, unit], timeout=60)
    if completed.returncode:
        raise MaintenanceHelperError(f"systemctl {operation} failed for {unit}.")
    return {"unit": unit, "operation": operation}


def handle_power(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"mode"}:
        raise MaintenanceHelperError("Request is invalid.")
    mode = arguments["mode"]
    if not isinstance(mode, str) or mode not in POWER_MODES:
        raise MaintenanceHelperError("Request is invalid.")
    completed = _run([SYSTEMCTL, POWER_MODES[mode]], timeout=30)
    if completed.returncode:
        raise MaintenanceHelperError(f"systemctl {POWER_MODES[mode]} was refused.")
    return {"mode": mode}


def handle_journal(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"unit", "lines"}:
        raise MaintenanceHelperError("Request is invalid.")
    unit = _managed_unit(arguments)
    lines = arguments["lines"]
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 2000:
        raise MaintenanceHelperError("Request is invalid.")
    completed = _run(
        [
            JOURNALCTL,
            "--no-pager",
            "--output=short-iso",
            "--unit",
            unit,
            "--lines",
            str(lines),
        ],
        timeout=30,
    )
    if completed.returncode:
        raise MaintenanceHelperError(f"journalctl failed for {unit}.")
    text = completed.stdout or ""
    truncated = len(text.encode("utf-8")) > MAX_OUTPUT_BYTES
    if truncated:
        text = text.encode("utf-8")[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    return {"unit": unit, "text": text, "truncated": truncated}


HANDLERS = {
    "version": handle_version,
    "service": handle_service,
    "power": handle_power,
    "journal": handle_journal,
}


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    return HANDLERS[request["verb"]](request["arguments"])


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 1:
        print("TAKT maintenance helper must run as root.", file=sys.stderr)
        return 1
    try:
        request = load_request(sys.stdin.buffer)
        result = dispatch(request)
    except (OSError, MaintenanceHelperError) as error:
        # The message is helper-authored (never echoes the request), so it is
        # safe to return, and the agent needs it to explain the failure.
        json.dump({"ok": False, "error": str(error)}, sys.stdout)
        return 1
    json.dump({"ok": True, "result": result}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
