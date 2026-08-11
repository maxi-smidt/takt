from __future__ import annotations

import argparse
import os
import ssl
from pathlib import Path

from aiohttp import web

from takt.registry.app import create_registry_app
from takt.registry.auth import AdminAuth
from takt.registry.storage import RegistryStore


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
    parser.add_argument("--tls-certificate")
    parser.add_argument("--tls-key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.admin_password:
        raise SystemExit(
            "Set TAKT_REGISTRY_ADMIN_PASSWORD or pass --admin-password (minimum 10 characters)."
        )
    data_directory = Path(args.data_directory).expanduser().resolve()
    data_directory.mkdir(parents=True, exist_ok=True)
    store = RegistryStore(data_directory)
    try:
        auth = AdminAuth(args.admin_password, data_directory)
    except Exception:
        store.close()
        raise
    app = create_registry_app(store, auth)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
