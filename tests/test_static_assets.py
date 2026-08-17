from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from takt.static_assets import require_static_assets


class StaticAssetTests(unittest.TestCase):
    def test_missing_assets_explain_how_to_build_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory, self.assertRaisesRegex(
            RuntimeError,
            r"Built frontend assets are missing.*build_web_ui\.sh",
        ):
            require_static_assets(
                Path(temporary_directory),
                "index.html",
                "scripts/build_web_ui.sh",
            )

    def test_requires_a_non_empty_assets_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
            (root / "assets").mkdir()
            with self.assertRaises(RuntimeError):
                require_static_assets(root, "index.html", "scripts/build_web_ui.sh")
            (root / "assets" / "index.js").write_text("", encoding="utf-8")
            require_static_assets(root, "index.html", "scripts/build_web_ui.sh")
