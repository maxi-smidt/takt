from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from takt import __version__
from takt.registry.bundled_release import import_bundled_release
from takt.registry.storage import RegistryStore


def _release_archive(version: str) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in (
            ("takt/pyproject.toml", f"[project]\nname='takt'\nversion='{version}'\n".encode()),
            ("takt/src/takt/web/static/index.html", b"<!doctype html>"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _write_bundle(
    directory: Path,
    *,
    version: str,
    archive_bytes: bytes | None = None,
    manifest_overrides: dict | None = None,
    write_sidecar: bool = True,
) -> None:
    archive_bytes = _release_archive(version) if archive_bytes is None else archive_bytes
    archive_name = f"takt-raspberry-pi-{version}.tar.gz"
    archive_path = directory / archive_name
    archive_path.write_bytes(archive_bytes)
    sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if write_sidecar:
        (directory / f"{archive_name}.sha256").write_text(f"{sha256}  {archive_name}\n")
    manifest = {
        "schema_version": 1,
        "version": version,
        "commit": "deadbeef",
        "artifact": archive_name,
        "size": len(archive_bytes),
        "sha256": sha256,
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (directory / f"takt-raspberry-pi-{version}.manifest.json").write_text(json.dumps(manifest))


class BundledReleaseImportTests(unittest.TestCase):
    def test_absent_bundle_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = RegistryStore(Path(temporary_directory) / "data")
            try:
                status = import_bundled_release(store, Path(temporary_directory) / "missing")
                self.assertEqual(status["status"], "absent")
                self.assertEqual(store.list_releases(), [])

                status_none = import_bundled_release(store, None)
                self.assertEqual(status_none["status"], "absent")
            finally:
                store.close()

    def test_fresh_bundle_imports_and_is_idempotent_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle_directory = root / "bundle"
            bundle_directory.mkdir()
            _write_bundle(bundle_directory, version=__version__)

            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "imported")
                releases = store.list_releases()
                self.assertEqual(len(releases), 1)
                self.assertEqual(releases[0]["version"], __version__)
                self.assertEqual(releases[0]["source"], "bundled")
                self.assertEqual(releases[0]["commit_sha"], "deadbeef")
            finally:
                store.close()

            # Simulate a restart against the same data directory.
            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "present")
                self.assertEqual(len(store.list_releases()), 1)
            finally:
                store.close()

    def test_version_mismatch_is_degraded_and_not_offered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle_directory = root / "bundle"
            bundle_directory.mkdir()
            other_version = "0.0.1" if __version__ != "0.0.1" else "0.0.2"
            _write_bundle(bundle_directory, version=other_version)

            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], "version_mismatch")
                self.assertEqual(store.list_releases(), [])
                health = store.health()
                self.assertTrue(health["ok"])
            finally:
                store.close()

    def test_corrupt_archive_is_degraded_and_not_offered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle_directory = root / "bundle"
            bundle_directory.mkdir()
            garbage = b"not a tarball"
            _write_bundle(bundle_directory, version=__version__, archive_bytes=garbage)

            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], "corrupt")
                self.assertEqual(store.list_releases(), [])
                self.assertTrue(store.health()["ok"])
            finally:
                store.close()

    def test_truncated_archive_checksum_mismatch_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle_directory = root / "bundle"
            bundle_directory.mkdir()
            _write_bundle(bundle_directory, version=__version__)
            archive_path = bundle_directory / f"takt-raspberry-pi-{__version__}.tar.gz"
            archive_path.write_bytes(archive_path.read_bytes()[:-5])

            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], "corrupt")
                self.assertTrue(store.health()["ok"])
            finally:
                store.close()

    def test_collision_with_existing_upload_fails_health_and_keeps_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_directory = root / "data"
            store = RegistryStore(data_directory)
            try:
                uploaded_bytes = io.BytesIO()
                with tarfile.open(fileobj=uploaded_bytes, mode="w:gz") as archive:
                    for name, content in (
                        (
                            "takt/pyproject.toml",
                            f"[project]\nname='takt'\nversion='{__version__}'\n"
                            "# manually uploaded\n".encode(),
                        ),
                        ("takt/src/takt/web/static/index.html", b"<!doctype html>"),
                    ):
                        info = tarfile.TarInfo(name)
                        info.size = len(content)
                        archive.addfile(info, io.BytesIO(content))
                uploaded_bytes = uploaded_bytes.getvalue()
                uploaded_sha256 = hashlib.sha256(uploaded_bytes).hexdigest()
                with tempfile.NamedTemporaryFile(dir=data_directory, delete=False) as handle:
                    handle.write(uploaded_bytes)
                    uploaded_path = Path(handle.name)
                store.add_release(
                    version=__version__,
                    filename="manual-upload.tar.gz",
                    sha256=uploaded_sha256,
                    size=len(uploaded_bytes),
                    source=uploaded_path,
                )

                bundle_directory = root / "bundle"
                bundle_directory.mkdir()
                _write_bundle(bundle_directory, version=__version__)

                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], "collision")

                releases = store.list_releases()
                self.assertEqual(len(releases), 1)
                self.assertEqual(releases[0]["sha256"], uploaded_sha256)
                self.assertEqual(releases[0]["source"], "upload")

                store.bundled_release_status = status
                health = store.health()
                self.assertFalse(health["ok"])
            finally:
                store.close()

    def test_missing_sha256_sidecar_mismatch_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle_directory = root / "bundle"
            bundle_directory.mkdir()
            _write_bundle(bundle_directory, version=__version__)
            sidecar = bundle_directory / f"takt-raspberry-pi-{__version__}.tar.gz.sha256"
            sidecar.write_text("0" * 64 + "  takt-raspberry-pi.tar.gz\n")

            store = RegistryStore(root / "data")
            try:
                status = import_bundled_release(store, bundle_directory)
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], "corrupt")
                self.assertEqual(store.list_releases(), [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
