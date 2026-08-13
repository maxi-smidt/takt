from __future__ import annotations

import argparse
import logging
import os
import ssl
import tempfile
from pathlib import Path

from aiohttp import web

from takt.registry.app import create_registry_app
from takt.registry.auth import AdminAuth
from takt.registry.bundled_release import import_bundled_release
from takt.registry.storage import RegistryStore

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TAKT fleet registry")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--data-directory",
        default="~/.local/share/takt-registry",
        help="registry database, release, and mirror storage",
    )
    parser.add_argument("--admin-password", default=os.environ.get("TAKT_REGISTRY_ADMIN_PASSWORD"))
    parser.add_argument(
        "--admin-password-file",
        default=os.environ.get("TAKT_REGISTRY_ADMIN_PASSWORD_FILE"),
    )
    parser.add_argument(
        "--secure-cookies",
        action="store_true",
        default=os.environ.get("TAKT_REGISTRY_SECURE_COOKIES", "").lower()
        in {"1", "true", "yes", "on"},
        help="mark the admin cookie Secure when TLS terminates at a reverse proxy",
    )
    parser.add_argument("--tls-certificate")
    parser.add_argument("--tls-key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    password = args.admin_password
    if args.admin_password_file:
        password = Path(args.admin_password_file).read_text(encoding="utf-8").strip()
    if not password:
        raise SystemExit(
            "Set TAKT_REGISTRY_ADMIN_PASSWORD(_FILE) or pass --admin-password "
            "(minimum 10 characters)."
        )
    data_directory = Path(args.data_directory).expanduser().resolve()
    data_directory.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=data_directory):
            pass
    except OSError as error:
        raise SystemExit(
            f"Registry data directory is not writable: {data_directory}: {error}"
        ) from error
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    lock_handle = _acquire_instance_lock(data_directory)
    store = RegistryStore(data_directory)
    bundled_release_directory = os.environ.get("TAKT_BUNDLED_RELEASE_DIR")
    store.bundled_release_status = import_bundled_release(
        store, Path(bundled_release_directory) if bundled_release_directory else None
    )
    LOGGER.info("Bundled release import: %s", store.bundled_release_status)
    try:
        auth = AdminAuth(password, data_directory)
    except Exception:
        store.close()
        raise
    app = create_registry_app(store, auth, secure_cookies=args.secure_cookies)

    async def close_store(_application: web.Application) -> None:
        store.close()

    app.on_cleanup.append(close_store)
    ssl_context = None
    if bool(args.tls_certificate) != bool(args.tls_key):
        raise SystemExit("Both --tls-certificate and --tls-key are required together.")
    if args.tls_certificate:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(args.tls_certificate, args.tls_key)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)
    lock_handle.close()
    return 0


def _acquire_instance_lock(data_directory: Path):
    try:
        import fcntl
    except ImportError:  # pragma: no cover - TAKT production targets are Unix systems.
        return (data_directory / "registry.lock").open("a+", encoding="utf-8")
    handle = (data_directory / "registry.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit(
            "Another TAKT registry process is already using this data directory."
        ) from None
    return handle


if __name__ == "__main__":
    raise SystemExit(main())
