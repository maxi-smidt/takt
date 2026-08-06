from __future__ import annotations

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
            ["systemctl", "poweroff"],
            check=True,
            timeout=10,
        )

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
        side_effect=subprocess.CalledProcessError(1, ["systemctl", "poweroff"]),
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
    def test_uses_installer_authorized_fallback_when_direct_request_is_denied(
        self,
        run_mock,
        platform_mock,
        model_mock,
    ) -> None:
        run_mock.side_effect = [
            subprocess.CalledProcessError(1, ["systemctl", "poweroff"]),
            None,
        ]
        service = SystemPowerService()
        service.shutdown()
        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [
                ["systemctl", "poweroff"],
                ["sudo", "-n", "systemctl", "poweroff"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
