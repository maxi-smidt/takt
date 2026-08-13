from __future__ import annotations

import io
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.takt_maintenance_helper import (
    HELPER_VERSION,
    MAX_REQUEST_BYTES,
    MaintenanceHelperError,
    dispatch,
    load_request,
)


def _request(verb: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"verb": verb, "arguments": arguments}


class MaintenanceHelperRequestTests(unittest.TestCase):
    def test_valid_request_round_trips(self) -> None:
        payload = json.dumps(_request("version", {})).encode("utf-8")
        self.assertEqual(load_request(io.BytesIO(payload)), _request("version", {}))

    def test_oversized_request_is_rejected(self) -> None:
        payload = b'{"verb":"version","arguments":{"pad":"' + b"a" * MAX_REQUEST_BYTES + b'"}}'
        with self.assertRaisesRegex(MaintenanceHelperError, "too large"):
            load_request(io.BytesIO(payload))

    def test_malformed_and_unknown_requests_are_rejected(self) -> None:
        for payload in (
            b"not json",
            b'["verb", "version"]',
            b'{"verb":"version"}',
            b'{"verb":"version","arguments":{},"extra":1}',
            b'{"verb":"rm_rf","arguments":{}}',
            b'{"verb":"service","arguments":"takt.service"}',
        ):
            with self.subTest(payload=payload), self.assertRaises(MaintenanceHelperError):
                load_request(io.BytesIO(payload))


class MaintenanceHelperDispatchTests(unittest.TestCase):
    def test_version_reports_the_verb_and_unit_allowlists(self) -> None:
        with patch("scripts.takt_maintenance_helper.subprocess.run") as run:
            result = dispatch(_request("version", {}))
        self.assertEqual(run.call_count, 0)
        self.assertEqual(result["helper_version"], HELPER_VERSION)
        self.assertEqual(sorted(result["units"]), ["takt-agent.service", "takt.service"])
        self.assertIn("journal", result["verbs"])

    def test_service_builds_an_exact_argv_without_interpolation(self) -> None:
        with patch(
            "scripts.takt_maintenance_helper.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=""),
        ) as run:
            dispatch(_request("service", {"unit": "takt-agent.service", "operation": "restart"}))
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/systemctl", "restart", "takt-agent.service"],
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_service_rejects_units_outside_the_allowlist(self) -> None:
        for unit in ("sshd.service", "takt.service; rm -rf /", "../takt.service", ""):
            with (
                self.subTest(unit=unit),
                patch("scripts.takt_maintenance_helper.subprocess.run") as run,
                self.assertRaises(MaintenanceHelperError),
            ):
                dispatch(_request("service", {"unit": unit, "operation": "restart"}))
            self.assertEqual(run.call_count, 0)

    def test_service_rejects_operations_outside_the_allowlist(self) -> None:
        for operation in ("mask", "enable", "restart; poweroff", True):
            with (
                self.subTest(operation=operation),
                patch("scripts.takt_maintenance_helper.subprocess.run") as run,
                self.assertRaises(MaintenanceHelperError),
            ):
                dispatch(_request("service", {"unit": "takt.service", "operation": operation}))
            self.assertEqual(run.call_count, 0)

    def test_service_failure_is_reported_as_an_error(self) -> None:
        with (
            patch(
                "scripts.takt_maintenance_helper.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, stdout="unit not found"),
            ),
            self.assertRaisesRegex(MaintenanceHelperError, "failed"),
        ):
            dispatch(_request("service", {"unit": "takt.service", "operation": "start"}))

    def test_power_accepts_only_reboot_and_poweroff(self) -> None:
        with patch(
            "scripts.takt_maintenance_helper.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=""),
        ) as run:
            dispatch(_request("power", {"mode": "reboot"}))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "reboot"])

        for mode in ("halt", "kexec", "reboot --force", 1):
            with (
                self.subTest(mode=mode),
                patch("scripts.takt_maintenance_helper.subprocess.run") as run,
                self.assertRaises(MaintenanceHelperError),
            ):
                dispatch(_request("power", {"mode": mode}))
            self.assertEqual(run.call_count, 0)

    def test_journal_caps_lines_and_targets_an_allowlisted_unit(self) -> None:
        with patch(
            "scripts.takt_maintenance_helper.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="line\n"),
        ) as run:
            result = dispatch(_request("journal", {"unit": "takt.service", "lines": 50}))
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/journalctl",
                "--no-pager",
                "--output=short-iso",
                "--unit",
                "takt.service",
                "--lines",
                "50",
            ],
        )
        self.assertFalse(result["truncated"])

        for lines in (0, 2001, -1, True, "50"):
            with (
                self.subTest(lines=lines),
                patch("scripts.takt_maintenance_helper.subprocess.run") as run,
                self.assertRaises(MaintenanceHelperError),
            ):
                dispatch(_request("journal", {"unit": "takt.service", "lines": lines}))
            self.assertEqual(run.call_count, 0)

    def test_journal_output_is_truncated_to_the_output_cap(self) -> None:
        with patch(
            "scripts.takt_maintenance_helper.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="x" * (600 * 1024)),
        ):
            result = dispatch(_request("journal", {"unit": "takt.service", "lines": 2000}))
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode("utf-8")), 512 * 1024)

    def test_extra_arguments_are_rejected_for_every_verb(self) -> None:
        for verb, arguments in (
            ("version", {"unit": "takt.service"}),
            ("service", {"unit": "takt.service", "operation": "start", "extra": 1}),
            ("power", {"mode": "reboot", "delay": 5}),
            ("journal", {"unit": "takt.service", "lines": 10, "since": "now"}),
        ):
            with (
                self.subTest(verb=verb),
                patch("scripts.takt_maintenance_helper.subprocess.run") as run,
                self.assertRaises(MaintenanceHelperError),
            ):
                dispatch(_request(verb, arguments))
            self.assertEqual(run.call_count, 0)


class MaintenanceHelperEntryPointTests(unittest.TestCase):
    def test_helper_refuses_to_run_unprivileged(self) -> None:
        from scripts.takt_maintenance_helper import main

        with patch("scripts.takt_maintenance_helper.os.geteuid", return_value=1000):
            self.assertEqual(main(), 1)

    def test_helper_refuses_command_line_arguments(self) -> None:
        from scripts.takt_maintenance_helper import main

        with (
            patch("scripts.takt_maintenance_helper.os.geteuid", return_value=0),
            patch("scripts.takt_maintenance_helper.sys.argv", ["helper", "power"]),
        ):
            self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()
