from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.takt_wifi_helper import WifiHelperError, apply_wifi_profile, load_request


class WifiHelperTests(unittest.TestCase):
    def test_profile_upsert_is_idempotent_and_never_activates_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request = {"ssid": "  Hall\\$WiFi", "password": "pa$$ word\\123", "priority": 0}
            with patch("scripts.takt_wifi_helper.subprocess.run") as run:
                first = apply_wifi_profile(
                    request, connection_directory=root, nmcli=Path("/usr/bin/nmcli")
                )
                second = apply_wifi_profile(
                    request, connection_directory=root, nmcli=Path("/usr/bin/nmcli")
                )
            self.assertEqual(first, second)
            self.assertEqual(list(root.glob("*.nmconnection")), [first])
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            content = first.read_text(encoding="utf-8")
            self.assertIn("autoconnect-priority=0\n", content)
            self.assertIn(r"ssid=\s\sHall\\$WiFi", content)
            self.assertIn(r"psk=pa$$ word\\123", content)
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][1:3], ["connection", "load"])
                self.assertNotIn("up", call.args[0])

    def test_failed_reload_restores_the_previous_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = {"ssid": "Timing Hall", "password": "original-secret", "priority": 0}
            with patch("scripts.takt_wifi_helper.subprocess.run"):
                target = apply_wifi_profile(original, connection_directory=root)
            before = target.read_bytes()
            changed = {"ssid": "Timing Hall", "password": "replacement-secret", "priority": 0}
            failure = subprocess.CalledProcessError(1, ["nmcli", "connection", "load"])
            with (
                patch(
                    "scripts.takt_wifi_helper.subprocess.run",
                    side_effect=[failure, subprocess.CompletedProcess([], 0)],
                ),
                self.assertRaisesRegex(WifiHelperError, "rejected"),
            ):
                apply_wifi_profile(changed, connection_directory=root)
            self.assertEqual(target.read_bytes(), before)

    def test_failed_new_profile_is_removed_without_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request = {"ssid": "Timing Hall", "password": "original-secret", "priority": 0}
            failure = subprocess.CalledProcessError(1, ["nmcli", "connection", "load"])
            with (
                patch(
                    "scripts.takt_wifi_helper.subprocess.run",
                    side_effect=[failure, subprocess.CompletedProcess([], 0)],
                ) as run,
                self.assertRaisesRegex(WifiHelperError, "rejected"),
            ):
                apply_wifi_profile(request, connection_directory=root)
            self.assertEqual(list(root.glob("*.nmconnection")), [])
            self.assertEqual(run.call_args_list[-1].args[0][1:], ["connection", "reload"])
            self.assertNotIn("up", run.call_args_list[-1].args[0])

    def test_unmanaged_profile_and_invalid_request_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request = {"ssid": "Timing Hall", "password": "original-secret", "priority": 0}
            with patch("scripts.takt_wifi_helper.subprocess.run"):
                target = apply_wifi_profile(request, connection_directory=root)
            target.write_text("[connection]\nid=user-profile\n", encoding="utf-8")
            with self.assertRaisesRegex(WifiHelperError, "occupied"):
                apply_wifi_profile(request, connection_directory=root)
            self.assertEqual(target.read_text(encoding="utf-8"), "[connection]\nid=user-profile\n")
            with self.assertRaises(WifiHelperError):
                load_request(
                    io.BytesIO(b'{"ssid":"Timing Hall","password":"short","priority":0}')
                )


if __name__ == "__main__":
    unittest.main()
