"""Shared app-level constants for the local web routes.

`RUNTIME_KEY` must stay a single object imported by every route module:
aiohttp's `web.AppKey` is looked up by identity, so each module defining
its own key (even with the same name) would silently miss the runtime
stored under `create_web_app`'s key.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from takt.web.runtime import WebRuntime

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
RUNTIME_KEY = web.AppKey("runtime", WebRuntime)
