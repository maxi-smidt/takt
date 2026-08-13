"""Validate and import the Raspberry Pi package baked into the Registry image.

CI produces exactly one Pi release archive per project version and copies it,
alongside a JSON manifest and a checksum, into ``bundled-release/`` before the
Docker build (see ``.github/workflows/registry-image.yml``). On startup the
Registry imports that archive into persistent storage so a fresh
``docker compose up`` already has the matching release available, without any
runtime network access.

This module intentionally has no dependency on ``takt.registry.app`` so that
``storage.py``/``server_main.py`` can use it without an import cycle.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from takt import __version__ as registry_version
from takt.registry.storage import RegistryStore

LOGGER = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


class ReleaseValidationError(Exception):
    """A release archive failed structural or version validation."""


def validate_release_archive(path: Path, expected_version: str) -> None:
    """Validate that ``path`` is a well-formed TAKT release archive.

    Shared by manual uploads (``app.upload_release``) and bundled-release
    import so both paths enforce the same structural guarantees: a safe,
    size-bounded tar with exactly one ``pyproject.toml`` whose version matches
    ``expected_version``, and a built web UI.
    """
    package_version = ""
    names: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            pyproject_members: list[tarfile.TarInfo] = []
            expanded_size = 0
            for member in archive.getmembers():
                member_path = Path(member.name)
                expanded_size += max(member.size, 0)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise ReleaseValidationError("Release contains an unsafe archive path.")
                if expanded_size > 500 * 1024 * 1024:
                    raise ReleaseValidationError("Expanded release is too large.")
                names.add(member.name.rstrip("/"))
                if member.isfile() and (
                    member.name == "pyproject.toml" or member.name.endswith("/pyproject.toml")
                ):
                    pyproject_members.append(member)
            if len(pyproject_members) != 1:
                raise ReleaseValidationError("Release must contain exactly one pyproject.toml.")
            pyproject_file = archive.extractfile(pyproject_members[0])
            if pyproject_file is None or pyproject_members[0].size > 1024 * 1024:
                raise ReleaseValidationError("Release pyproject.toml is invalid.")
            metadata = tomllib.loads(pyproject_file.read().decode("utf-8"))
            package_version = str(metadata.get("project", {}).get("version", ""))
    except ReleaseValidationError:
        raise
    except (OSError, UnicodeDecodeError, tarfile.TarError, tomllib.TOMLDecodeError) as error:
        raise ReleaseValidationError("Release is not a readable gzip tar archive.") from error
    if not any(name.endswith("/pyproject.toml") or name == "pyproject.toml" for name in names):
        raise ReleaseValidationError("Release does not contain pyproject.toml.")
    if not any(name.endswith("/src/takt/web/static/index.html") for name in names):
        raise ReleaseValidationError("Release does not contain the built TAKT web interface.")
    if package_version != expected_version:
        raise ReleaseValidationError(
            f"Release version {package_version or 'missing'} does not match "
            f"the requested version {expected_version}."
        )


def import_bundled_release(store: RegistryStore, directory: Path | None) -> dict[str, Any]:
    """Idempotently import the bundled Pi release into ``store``.

    Returns a status dict, never raises: a bad or missing bundle must not
    prevent the Registry from starting. Statuses:

    - ``absent``: no bundle shipped with this image (e.g. local dev build).
    - ``imported``: the bundle was verified and added to the release library.
    - ``present``: the bundle was already imported by a previous startup.
    - ``error``: the bundle exists but failed verification, or its version
      collides with a differently-hashed release already stored under the
      same version. The reason is included and the bundle is never offered.
    """
    if directory is None:
        return {"status": "absent"}
    directory = Path(directory)
    manifests = sorted(directory.glob("*.manifest.json")) if directory.is_dir() else []
    if not manifests:
        return {"status": "absent"}
    manifest_path = manifests[0]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        LOGGER.error("Bundled release manifest %s is unreadable: %s", manifest_path, error)
        return {"status": "error", "reason": "corrupt", "detail": f"manifest unreadable: {error}"}

    if manifest.get("schema_version") != 1:
        LOGGER.error(
            "Bundled release manifest has unsupported schema_version: %r",
            manifest.get("schema_version"),
        )
        return {
            "status": "error",
            "reason": "corrupt",
            "detail": "unsupported manifest schema_version",
        }

    version = str(manifest.get("version", ""))
    if not VERSION_PATTERN.fullmatch(version):
        return {"status": "error", "reason": "corrupt", "detail": "manifest version is invalid"}
    if version != registry_version:
        detail = f"bundled package {version} does not match registry version {registry_version}"
        LOGGER.error("Bundled release version mismatch: %s", detail)
        return {"status": "error", "reason": "version_mismatch", "detail": detail}

    artifact_name = str(manifest.get("artifact", ""))
    archive_path = directory / artifact_name if artifact_name else None
    expected_sha256 = str(manifest.get("sha256", ""))
    expected_size = manifest.get("size")
    if not archive_path or not archive_path.is_file():
        detail = f"artifact {artifact_name!r} referenced by manifest is missing"
        LOGGER.error("Bundled release archive missing: %s", detail)
        return {"status": "error", "reason": "missing", "detail": detail}

    actual_size = archive_path.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        detail = f"size {actual_size} does not match manifest size {expected_size}"
        LOGGER.error("Bundled release size mismatch: %s", detail)
        return {"status": "error", "reason": "corrupt", "detail": detail}

    actual_sha256 = RegistryStore._sha256_file(archive_path)
    if not expected_sha256 or actual_sha256 != expected_sha256:
        detail = "sha256 does not match manifest"
        LOGGER.error("Bundled release checksum mismatch: %s", detail)
        return {"status": "error", "reason": "corrupt", "detail": detail}

    sidecar = archive_path.with_name(archive_path.name + ".sha256")
    if sidecar.is_file():
        sidecar_text = sidecar.read_text(encoding="utf-8").strip().split()
        if sidecar_text and sidecar_text[0] != expected_sha256:
            detail = "sha256 sidecar does not match manifest"
            LOGGER.error("Bundled release checksum sidecar mismatch: %s", detail)
            return {"status": "error", "reason": "corrupt", "detail": detail}

    try:
        validate_release_archive(archive_path, version)
    except ReleaseValidationError as error:
        LOGGER.error("Bundled release archive failed validation: %s", error)
        return {"status": "error", "reason": "corrupt", "detail": str(error)}

    commit_sha = str(manifest.get("commit") or "") or None
    existing = store.get_release_by_version(version)
    if existing is not None:
        if existing["sha256"] == actual_sha256:
            store.mark_release_bundled(existing["id"], commit_sha=commit_sha)
            return {"status": "present", "version": version, "sha256": actual_sha256}
        detail = (
            f"an existing release {version} (sha256 {existing['sha256'][:12]}...) "
            f"does not match the bundled package (sha256 {actual_sha256[:12]}...)"
        )
        LOGGER.error("Bundled release collides with an existing upload: %s", detail)
        return {"status": "error", "reason": "collision", "detail": detail}

    with tempfile.NamedTemporaryFile(dir=store.data_directory, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    shutil.copyfile(archive_path, temporary_path)
    try:
        store.add_release(
            version=version,
            filename=archive_path.name,
            sha256=actual_sha256,
            size=actual_size,
            source=temporary_path,
            release_source="bundled",
            commit_sha=commit_sha,
        )
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        LOGGER.error("Failed to import bundled release %s: %s", version, error)
        return {"status": "error", "reason": "import_failed", "detail": str(error)}
    return {"status": "imported", "version": version, "sha256": actual_sha256}
