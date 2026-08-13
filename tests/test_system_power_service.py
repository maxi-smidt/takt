from __future__ import annotations

import pathlib
import subprocess
import unittest
from unittest.mock import patch

from takt.application.system_power_service import SystemPowerService


class SystemPowerServiceTests(unittest.TestCase):
    @patch.object(SystemPowerService, "_read_model", return_value="Raspberry Pi 3 Model B")
    @patch("takt.application.system_power_service.platform.system", return_value="Linux")
    @patch("takt.application.system_power_service.subprocess.run")
    def test_requests_orderly_poweroff_on_raspberry_pi(
        self,
        run_mock,
        platform_mock,
        model_mock,
    ) -> None:
        service = SystemPowerService()
        service.shutdown()
        run_mock.assert_called_once_with(
            ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
        )

    def test_install_units_keep_bluetooth_optional_and_agent_bounded(self) -> None:
        root = pathlib.Path(__file__).parents[1]
        script = (root / "scripts" / "install_raspberry_pi.sh").read_text(encoding="utf-8")
        agent = (
            script.split("bluetooth_agent_unit=\"$(mktemp)\"", 1)[1]
            .split("say \"Headless-Audio", 1)[0]
        )
        takt = (
            script.split("unit_file=\"$(mktemp)\"", 1)[1]
            .split("if [[ -f \"$agent_config\"", 1)[0]
        )
        self.assertIn("\"Restart=on-failure\"", agent)
        self.assertIn("\"TimeoutStopSec=10\"", agent)
        self.assertIn("\"After=network-online.target\"", takt)
        self.assertIn("\"Wants=network-online.target\"", takt)
        self.assertNotIn("bluetooth.target", takt)
        self.assertNotIn("sound.target", takt)

    @patch.object(SystemPowerService, "_read_model", return_value="MacBook Pro")
    @patch("takt.application.system_power_service.platform.system", return_value="Darwin")
    def test_refuses_to_shutdown_development_computer(
        self,
        platform_mock,
        model_mock,
    ) -> None:
        service = SystemPowerService()
        with self.assertRaisesRegex(RuntimeError, "nur auf einem Raspberry Pi"):
            service.shutdown()

    @patch.object(SystemPowerService, "_read_model", return_value="Raspberry Pi 3 Model B")
    @patch("takt.application.system_power_service.platform.system", return_value="Linux")
    @patch(
        "takt.application.system_power_service.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            1, ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"]
        ),
    )
    def test_reports_denied_poweroff_request(
        self,
        run_mock,
        platform_mock,
        model_mock,
    ) -> None:
        service = SystemPowerService()
        with self.assertRaisesRegex(RuntimeError, "Anfrage abgelehnt"):
            service.shutdown()

    @patch.object(SystemPowerService, "_read_model", return_value="Raspberry Pi 3 Model B")
    @patch("takt.application.system_power_service.platform.system", return_value="Linux")
    @patch("takt.application.system_power_service.subprocess.run")
    def test_falls_back_to_systemctl_when_shutdown_is_denied(
        self,
        run_mock,
        platform_mock,
        model_mock,
    ) -> None:
        run_mock.side_effect = [
            subprocess.CalledProcessError(1, ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"]),
            subprocess.CalledProcessError(1, ["sudo", "-n", "/sbin/shutdown", "-h", "now"]),
            None,
        ]
        service = SystemPowerService()
        service.shutdown()
        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [
                ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
                ["sudo", "-n", "/sbin/shutdown", "-h", "now"],
                ["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
            ],
        )

    @patch.object(SystemPowerService, "_read_model", return_value="Raspberry Pi 3 Model B")
    @patch("takt.application.system_power_service.platform.system", return_value="Linux")
    @patch(
        "takt.application.system_power_service.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            1,
            ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
            output="",
            stderr="sudo: a password is required\n",
        ),
    )
    def test_denial_diagnostic_from_stderr_is_included_in_error_message(
        self,
        run_mock,
        platform_mock,
        model_mock,
    ) -> None:
        service = SystemPowerService()
        with self.assertRaisesRegex(RuntimeError, "a password is required"):
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
