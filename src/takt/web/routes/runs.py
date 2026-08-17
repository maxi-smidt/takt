"""Run history, database export, and remote run-curation endpoints."""

from __future__ import annotations

import asyncio
import csv
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web

from takt.web.routes.common import RUNTIME_KEY
from takt.web.routes.security import _require_loopback, _require_same_origin
from takt.web.runtime import json_body, parse_chart_days

EXPORT_CHUNK_SIZE = 64 * 1024


async def history(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    chart_days = parse_chart_days(
        request.query.get("days"),
        runtime.config.display.chart_default_days,
    )
    return web.json_response(runtime.history_payload(chart_days))


async def database_export(request: web.Request) -> web.StreamResponse:
    _require_same_origin(request)
    runtime = request.app[RUNTIME_KEY]

    export_format = request.query.get("format")
    if export_format not in {"db", "csv"}:
        raise web.HTTPBadRequest(text="Exportformat muss db oder csv sein.")
    if runtime.data_export_blocked:
        raise web.HTTPConflict(
            text="Datenexport ist während einer laufenden Zeitmessung nicht möglich."
        )
    if runtime.maintenance_status()["held"]:
        raise web.HTTPConflict(text="Datenexport ist während der Wartung nicht möglich.")

    suffix = ".db" if export_format == "db" else ".csv"
    prefix = "takt-" if export_format == "db" else "takt-runs-"
    filename = f"{prefix}{date.today().isoformat()}{suffix}"
    content_type = (
        "application/vnd.sqlite3"
        if export_format == "db"
        else "text/csv; charset=utf-8"
    )
    with TemporaryDirectory(prefix="takt-export-") as directory:
        artifact = Path(directory) / filename
        try:
            if export_format == "db":
                await asyncio.to_thread(runtime.repository.backup_to, artifact)
            else:
                await asyncio.to_thread(runtime.repository.export_runs_csv, artifact)
        except (OSError, csv.Error, sqlite3.Error) as error:
            raise web.HTTPInternalServerError(
                text="Datenexport konnte nicht erstellt werden."
            ) from error
        return await _stream_export(request, artifact, filename, content_type)


async def _stream_export(
    request: web.Request,
    artifact: Path,
    filename: str,
    content_type: str,
) -> web.StreamResponse:
    response = web.StreamResponse(
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Content-Type": content_type,
        }
    )
    response.content_length = (await asyncio.to_thread(artifact.stat)).st_size
    await response.prepare(request)
    try:
        stream = await asyncio.to_thread(artifact.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, EXPORT_CHUNK_SIZE):
                await response.write(chunk)
            await response.write_eof()
        finally:
            await asyncio.to_thread(stream.close)
    except ConnectionResetError:
        # The temporary directory is still cleaned by database_export().
        return response
    return response


async def run_curation(request: web.Request) -> web.Response:
    _require_loopback(request)
    body = await json_body(request)
    command_id = body.get("command_id")
    operation = body.get("operation")
    run_id = body.get("run_id")
    expected_updated_at = body.get("expected_updated_at")
    desired_added_time_ms = body.get("desired_added_time_ms")
    if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
        raise web.HTTPBadRequest(text="A valid command_id is required.")
    if operation not in {"adjust_added_time", "delete"}:
        raise web.HTTPBadRequest(text="Unsupported run curation operation.")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
        raise web.HTTPBadRequest(text="A valid run_id is required.")
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        raise web.HTTPBadRequest(text="expected_updated_at is required.")
    if desired_added_time_ms is not None and (
        not isinstance(desired_added_time_ms, int) or isinstance(desired_added_time_ms, bool)
    ):
        raise web.HTTPBadRequest(text="desired_added_time_ms must be an integer.")
    try:
        result = request.app[RUNTIME_KEY].apply_remote_curation(
            command_id=command_id,
            operation=operation,
            run_id=run_id,
            expected_updated_at=expected_updated_at,
            desired_added_time_ms=desired_added_time_ms,
        )
    except ValueError as error:
        raise web.HTTPConflict(text=str(error)) from error
    return web.json_response({"ok": True, "result": result})
